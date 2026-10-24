#!/usr/bin/env python3
"""Dental 3B daily close, sent with an approved Meta WhatsApp template.

Dry run by default. A real send requires --send and DAILY_SUMMARY_ENABLED=true.
Only aggregate totals go to WhatsApp; no patient records are persisted.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
STATE_BRANCH = "daily-summary-state"
TERMINAL_STATUSES = {"check out", "missed", "cancel", "cancelled", "canceled", "left message", "cut"}


class ApiError(RuntimeError):
    def __init__(self, service, status):
        self.status = status
        super().__init__(f"{service}: HTTP {status}; no message delivery confirmed")


def request_json(service, method, url, *, headers=None, payload=None, form=None):
    headers = dict(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        body = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        with urlopen(Request(url, data=body, headers=headers, method=method), timeout=60) as response:
            result = json.load(response)
    except HTTPError as error:
        # Never print response bodies, bearer tokens, or patient data.
        raise ApiError(service, error.code) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise RuntimeError(f"{service}: request failed or invalid JSON response") from None
    if not isinstance(result, dict) or result.get("error"):
        raise RuntimeError(f"{service}: unexpected API response")
    return result


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing configuration: {name}")
    return value


def curve_token():
    result = request_json("Curve authentication", "POST", "https://sso.curvehero.com/connect/token", form={
        "grant_type": "client_credentials",
        "client_id": required("CURVE_CLIENT_ID"),
        "client_secret": required("CURVE_CLIENT_SECRET"),
        "scope": "ledger_entry:read appointment:read",
    })
    token = result.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Curve authentication: missing access token")
    return token


def curve_pages(url, token, params):
    origin = urlsplit(url)
    next_url = url + "?" + urlencode(params)
    visited = set()
    objects = []
    while next_url:
        parsed = urlsplit(next_url)
        if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc) or next_url in visited:
            raise RuntimeError("Curve pagination: invalid or repeated next URL")
        visited.add(next_url)
        data = request_json("Curve", "GET", next_url, headers={"Authorization": f"Bearer {token}"})
        page = data.get("objects")
        meta = data.get("meta") or {}
        if not isinstance(page, list) or not all(isinstance(item, dict) for item in page) or not isinstance(meta, dict):
            raise RuntimeError("Curve: incomplete data response")
        objects.extend(page)
        link = meta.get("next")
        if link is not None and not isinstance(link, str):
            raise RuntimeError("Curve pagination: invalid next link")
        next_url = urljoin(next_url, link) if link else None
    return objects


@dataclass(frozen=True)
class Summary:
    day: date
    production: Decimal
    collections: Decimal
    patients: int
    appointments: int
    pending: int

    def parameters(self):
        months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        ratio = self.collections / self.production * 100 if self.production else Decimal(0)
        return [f"{self.day.day:02d} {months[self.day.month - 1]} {self.day.year}",
                f"{self.production:,.0f}", f"{self.collections:,.0f}", f"{ratio:.1f}", str(self.patients)]

    def fingerprint(self):
        values = [str(self.production), str(self.collections), self.patients, self.appointments, self.pending]
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def summarize(day, ledger, appointments):
    production = Decimal(0)
    collections = Decimal(0)
    patients = set()
    for entry in ledger:
        calc = entry.get("calculations") or {}
        amount = Decimal(str(calc.get("production") or 0))
        payment = Decimal(str(calc.get("payments") or 0))
        if not amount.is_finite() or not payment.is_finite():
            raise RuntimeError("Curve: invalid ledger amount")
        production += amount
        collections += payment
        if amount > 0 and entry.get("patientId") is not None:
            pid = str(entry["patientId"]).strip()
            if pid:
                patients.add(pid)
    pending = sum(str(a.get("appointmentStatus", "")).strip().lower() not in TERMINAL_STATUSES for a in appointments)
    return Summary(day, production, collections, len(patients), len(appointments), pending)


def get_summary(day):
    tenant = os.environ.get("CURVE_INSTANCE", "dentalthreeb").strip()
    if not re.fullmatch(r"[a-zA-Z0-9-]+", tenant):
        raise RuntimeError("Invalid CURVE_INSTANCE")
    token = curve_token()
    ledger = curve_pages(f"https://{tenant}.curvehero.com/cheetah/ledger_entry", token, {
        "startDate": day.isoformat(), "endDate": (day + timedelta(days=1)).isoformat(),
    })
    appointments = curve_pages(f"https://api.curvehero.com/v1/{tenant}/appointment", token, {
        "startDate": f"{day} 00:00:00", "endDate": f"{day} 23:59:59",
        "statuses": "active,check in,check out,missed,cancel", "pageSize": 500,
    })
    return summarize(day, ledger, appointments)


def in_send_window(now):
    now = now.astimezone(EASTERN)
    if now.weekday() == 6:
        return False
    start, end = (14, 19) if now.weekday() == 5 else (16, 21)
    return start <= now.hour < end


class GitHubState:
    """Durable date-specific records, separate from main; SHA writes reject races."""
    def __init__(self, day):
        repo = required("GITHUB_REPOSITORY")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise RuntimeError("Invalid GITHUB_REPOSITORY")
        self.base = f"https://api.github.com/repos/{repo}"
        self.headers = {"Authorization": f"Bearer {required('GITHUB_TOKEN')}",
                        "Accept": "application/vnd.github+json"}
        self.path = f"/contents/daily-summary-state/{day.isoformat()}.json"
        self.sha = None
        try:
            self.api("GET", f"/git/ref/heads/{STATE_BRANCH}")
        except ApiError as error:
            if error.status != 404:
                raise
            self.api("POST", "/git/refs", {"ref": f"refs/heads/{STATE_BRANCH}", "sha": required("GITHUB_SHA")})

    def api(self, method, path, payload=None):
        return request_json("GitHub state", method, self.base + path, headers=self.headers, payload=payload)

    def read(self):
        try:
            data = self.api("GET", self.path + "?" + urlencode({"ref": STATE_BRANCH}))
        except ApiError as error:
            if error.status == 404:
                return {}
            raise
        self.sha = data["sha"]
        result = json.loads(base64.b64decode(data["content"]))
        if not isinstance(result, dict) or result.get("status") not in {"ready", "waiting", "sending", "accepted"}:
            raise RuntimeError("Invalid summary state; inspect the state branch before sending")
        return result

    def write(self, value):
        payload = {"message": "Update daily summary delivery state", "branch": STATE_BRANCH,
                   "content": base64.b64encode(json.dumps(value, sort_keys=True).encode()).decode()}
        if self.sha:
            payload["sha"] = self.sha
        result = self.api("PUT", self.path, payload)
        self.sha = result["content"]["sha"]


@dataclass(frozen=True)
class MetaSender:
    token: str
    phone_id: str
    recipient: str
    template: str
    language: str
    version: str

    @classmethod
    def from_environment(cls):
        config = cls(required("WHATSAPP_TOKEN"), required("WHATSAPP_PHONE_NUMBER_ID"),
                     required("WHATSAPP_TO").lstrip("+"), required("WHATSAPP_TEMPLATE_NAME"),
                     os.environ.get("WHATSAPP_TEMPLATE_LANGUAGE") or "es",
                     os.environ.get("WHATSAPP_GRAPH_VERSION") or "v23.0")
        if not config.phone_id.isdigit() or not re.fullmatch(r"[1-9][0-9]{7,14}", config.recipient):
            raise RuntimeError("Invalid WhatsApp phone ID or recipient")
        if not re.fullmatch(r"[a-z0-9_]+", config.template) or not re.fullmatch(r"v[0-9]+\.0", config.version):
            raise RuntimeError("Invalid WhatsApp template or API version")
        return config

    def send(self, summary):
        data = request_json("Meta WhatsApp", "POST",
                            f"https://graph.facebook.com/{self.version}/{self.phone_id}/messages",
                            headers={"Authorization": f"Bearer {self.token}"}, payload={
            "messaging_product": "whatsapp", "to": self.recipient, "type": "template",
            "template": {"name": self.template, "language": {"code": self.language},
                         "components": [{"type": "body", "parameters": [
                             {"type": "text", "text": value} for value in summary.parameters()]}]},
        })
        messages = data.get("messages") or []
        message_id = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
        if not isinstance(message_id, str) or not message_id.startswith("wamid."):
            raise RuntimeError("Meta response has no message ID; check delivery before retrying")
        return message_id


def process(summary, now, store, sender):
    record = store.read()
    status = record.get("status")
    if status == "accepted":
        print("Already accepted by Meta for this date; skipping")
        return
    if status == "sending":
        raise RuntimeError("Previous send is unconfirmed; check Meta before resetting the date's state")
    # Keep the old production-based patient definition and skip non-production days.
    # Without appointments, wait until the previous workflow's final hour (20:00/18:00).
    closing_hour = 18 if now.weekday() == 5 else 20
    ready = (summary.production > 0 and summary.pending == 0
             and (summary.appointments > 0 or now.hour >= closing_hour))
    if not ready:
        if status == "ready":
            store.write({"status": "waiting"})
        print("Waiting for daily close or positive production")
        return
    fingerprint = summary.fingerprint()
    if status != "ready" or record.get("fingerprint") != fingerprint:
        store.write({"status": "ready", "ready_since": now.isoformat(), "fingerprint": fingerprint})
        print("Close detected; waiting at least 15 minutes with stable totals")
        return
    since = datetime.fromisoformat(record["ready_since"])
    if now - since < timedelta(minutes=15):
        print("Waiting for the 15-minute close interval")
        return
    # Record intent BEFORE POST. An ambiguous network failure must not trigger duplicates.
    store.write({"status": "sending", "started_at": now.isoformat()})
    try:
        message_id = sender.send(summary)
    except ApiError as error:
        if 400 <= error.status < 500 and error.status != 408:
            store.write(record)  # Explicit rejection: permit a later retry after correction.
        raise
    store.write({"status": "accepted", "message_id": message_id, "accepted_at": now.isoformat()})
    print("Meta accepted the message; delivery confirmation requires the existing WhatsApp webhook")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Send when configured and daily close is ready")
    parser.add_argument("--date", type=date.fromisoformat, help="Date for a read-only preview")
    args = parser.parse_args()
    if args.send and args.date:
        raise RuntimeError("--date is only available for previews")
    now = datetime.now(EASTERN)
    sender = None
    if args.send:
        if os.environ.get("DAILY_SUMMARY_ENABLED") != "true":
            raise RuntimeError("Set DAILY_SUMMARY_ENABLED=true after configuring the approved template")
        if not in_send_window(now):
            print("Outside the America/New_York send window")
            return
        sender = MetaSender.from_environment()
    summary = get_summary(args.date or now.date())
    if not args.send:
        # Aggregate preview only. No state writes and no WhatsApp requests.
        day, production, collections, ratio, patients = summary.parameters()
        print(f"Dental 3B — {day}\nProduction: ${production}\nCollections: ${collections} ({ratio}%)\nPatients seen: {patients}")
        print(f"Appointments: {summary.appointments}; pending: {summary.pending}; preview only")
        return
    process(summary, now, GitHubState(summary.day), sender)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Unexpected exceptions are deliberately not dumped with patient/credential context.
        print(f"Daily summary failed: {error if isinstance(error, RuntimeError) else type(error).__name__}", file=sys.stderr)
        sys.exit(1)
