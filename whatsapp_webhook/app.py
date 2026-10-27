from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from typing import Any

from flask import Flask, Response, jsonify, request
from google.cloud import firestore

from logic import contact_key, extract_first_messages, verify_signature
from funnel_report import iso_date, validate_report
from live_capture import inbound_events, remember_event, aggregate_contacts, ACTIVITY

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
CONTACTS_COLLECTION = os.environ.get("CONTACTS_COLLECTION", "d3b_whatsapp_first_contacts")
FUNNEL_COLLECTION = os.environ.get("FUNNEL_COLLECTION", "d3b_whatsapp_funnel_reports")


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required configuration: {name}")
    return value


def _remember_earliest_contact(db: Any, sender: str, first_seen: datetime, hash_secret: str) -> None:
    ref = db.collection(CONTACTS_COLLECTION).document(contact_key(sender, hash_secret))
    transaction = db.transaction()

    @firestore.transactional
    def upsert(tx: Any) -> None:
        snapshot = ref.get(transaction=tx)
        if not snapshot.exists:
            tx.set(ref, {"first_seen": first_seen})
            return
        existing = (snapshot.to_dict() or {}).get("first_seen")
        if existing is None or first_seen < existing:
            tx.update(ref, {"first_seen": first_seen})

    upsert(transaction)


def _firestore_client() -> Any:
    return firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None)


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/webhook")
def verify_webhook():
    mode = request.args.get("hub.mode", "")
    supplied = request.args.get("hub.verify_token", "")
    challenge = request.args.get("hub.challenge", "")
    expected = os.environ.get("META_VERIFY_TOKEN", "")
    if mode == "subscribe" and expected and hmac.compare_digest(supplied, expected):
        return Response(challenge, status=200, mimetype="text/plain")
    return Response("Forbidden", status=403, mimetype="text/plain")


@app.post("/webhook")
def receive_webhook():
    body = request.get_data(cache=True)
    try:
        app_secret = _required_env("META_APP_SECRET")
        if not verify_signature(body, request.headers.get("X-Hub-Signature-256"), app_secret):
            return jsonify({"error": "invalid signature"}), 403

        payload = request.get_json(silent=False)
        expected_phone_id = _required_env("META_PHONE_NUMBER_ID")
        hash_secret = _required_env("CONTACT_HASH_KEY")
        db = _firestore_client()
        events = extract_first_messages(payload, expected_phone_id)
        changes = [change for entry in payload.get("entry", []) for change in entry.get("changes", [])]
        values = [change.get("value") or {} for change in changes]
        app.logger.warning(
            "WA_DIAGNOSTIC changes=%s messages=%s statuses=%s matching_changes=%s accepted=%s",
            len(changes), sum(len(v.get("messages") or []) for v in values),
            sum(len(v.get("statuses") or []) for v in values),
            sum(str((v.get("metadata") or {}).get("phone_number_id") or "") == expected_phone_id for v in values),
            len(events),
        )
        for sender, received_at in events:
            _remember_earliest_contact(db, sender, received_at, hash_secret)
        for sender, event_id, stamp in inbound_events(payload, expected_phone_id):
            remember_event(db, sender, event_id, stamp, hash_secret)
        return jsonify({"ok": True}), 200
    except Exception:
        app.logger.exception("WhatsApp webhook processing failed")
        return jsonify({"error": "webhook processing failed"}), 500


@app.get("/api/summary")
def monthly_summary():
    expected_token = os.environ.get("REPORT_API_TOKEN", "")
    supplied_header = request.headers.get("Authorization", "")
    if not expected_token or not hmac.compare_digest(supplied_header, f"Bearer {expected_token}"):
        return jsonify({"error": "unauthorized"}), 401

    month = request.args.get("month", "")
    try:
        start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError:
        return jsonify({"error": "month must be YYYY-MM"}), 400
    if start.strftime("%Y-%m") != month:
        return jsonify({"error": "month must be YYYY-MM"}), 400

    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)

    db = _firestore_client()
    query = (
        db.collection(CONTACTS_COLLECTION)
        .where("first_seen", ">=", start)
        .where("first_seen", "<", end)
    )
    count = sum(1 for _ in query.stream())
    return jsonify({"month": month, "new_unique_people": count})


@app.get("/privacy")
def privacy_notice():
    return Response("""Dental 3B Dashboard — Privacy Notice
Effective date: October 26, 2026

Dental 3B operates this internal analytics integration to count unique
people who contact the clinic through WhatsApp.

The integration receives incoming message events from Meta. For its
counter, it stores a keyed, coded identifier derived from the sender's
WhatsApp identifier and the date and time of the earliest message it
has received for that identifier. This identifier is pseudonymous,
not anonymous.

The counter does not store message text, names, media, or phone numbers
in clear text. The original WhatsApp conversations and other clinic
systems are separate from this counter and have their own records.

The stored data is used to calculate aggregate contact statistics.
Meta provides WhatsApp messaging, and Google Cloud hosts the integration
and its database. Their services process data under their respective
terms and privacy policies.

Daily activity also stores coded message identifiers to prevent duplicates,
and coded contact identifiers, message counts, and timestamps. For matching,
the reporting service processes patient phone numbers supplied by the
internal dashboard in memory; these numbers are not stored in the counter.

Counter records currently have no automatic expiration. They remain
until Dental 3B deletes them or retires the integration.

To ask about this integration or request deletion of your counter
record, contact office@dental3b.com. See /data-deletion for instructions.
""", mimetype="text/plain")


@app.get("/data-deletion")
def data_deletion_instructions():
    return Response("""Dental 3B Dashboard — Data Deletion Instructions

To request deletion of your record from the WhatsApp contact counter,
email office@dental3b.com with the subject:
Dental 3B Dashboard — Data deletion request

Include the WhatsApp phone number you used, including its country code,
so Dental 3B can identify the coded counter record and daily activity records. Do not include
medical details, identification documents, or passwords.

Dental 3B may need to verify that you control the number before deleting
the record. The clinic will respond regarding the request.

Deleting a counter record does not delete the original WhatsApp
conversation, Meta's records, or clinical records held in other systems.
If you contact the clinic again after deletion, the counter may create
a new record.
""", mimetype="text/plain")


@app.get("/api/funnel")
def funnel_summary():
    expected = os.environ.get("REPORT_API_TOKEN", "")
    supplied = request.headers.get("Authorization", "")
    if not expected or not hmac.compare_digest(supplied, f"Bearer {expected}"):
        return jsonify({"error": "unauthorized"}), 401
    start, end = request.args.get("start", ""), request.args.get("end", "")
    try:
        if iso_date(start) > iso_date(end):
            raise ValueError("Invalid date range")
    except (ValueError, TypeError):
        return jsonify({"error": "start and end must be YYYY-MM-DD"}), 400
    try:
        snapshot = _firestore_client().collection(FUNNEL_COLLECTION).document(f"{start}_{end}").get()
        if not snapshot.exists:
            return jsonify({"error": "verified report not available"}), 404
        report = validate_report(snapshot.to_dict())
        if (report["start"], report["end"]) != (start, end):
            raise ValueError("Mismatched report dates")
        return jsonify(report)
    except Exception:
        app.logger.error("Verified funnel report unavailable")
        return jsonify({"error": "report unavailable"}), 503


@app.post("/api/live-contacts")
def live_contacts():
    expected = os.environ.get("REPORT_API_TOKEN", "")
    if not expected or not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {expected}"):
        return jsonify({"error": "unauthorized"}), 401
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {"start", "end", "patients"}:
            raise ValueError()
        start, end = iso_date(payload["start"]), iso_date(payload["end"])
        if start > end or (end - start).days > 366:
            raise ValueError()
        candidates = payload["patients"]
        if not isinstance(candidates, list) or len(candidates) > 5000:
            raise ValueError()
        for patient in candidates:
            if not isinstance(patient, dict) or set(patient) != {"id", "phones"}:
                raise ValueError()
            if not isinstance(patient["id"], str) or not patient["id"].isdigit():
                raise ValueError()
            if not isinstance(patient["phones"], list) or len(patient["phones"]) > 5:
                raise ValueError()
            if any(not isinstance(phone, str) or len(phone) > 40 for phone in patient["phones"]):
                raise ValueError()
    except (ValueError, TypeError, KeyError):
        return jsonify({"error": "invalid request"}), 400
    since = os.environ.get("LIVE_CAPTURE_SINCE", "")
    try:
        stamp = datetime.fromisoformat(since.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify({"error": "live capture not configured"}), 503
    try:
        query = (_firestore_client().collection(ACTIVITY)
                 .where("day", ">=", start.isoformat()).where("day", "<=", end.isoformat()).limit(5001))
        rows = [snap.to_dict() for snap in query.stream()]
        if len(rows) > 5000:
            return jsonify({"error": "date range too large"}), 422
        contacts = aggregate_contacts(rows, candidates, _required_env("CONTACT_HASH_KEY"))
        db = _firestore_client()
        refs = [db.collection(CONTACTS_COLLECTION).document(row["key"]) for row in contacts]
        earliest = {snap.id: (snap.to_dict() or {}).get("first_seen") for snap in db.get_all(refs)} if refs else {}
        for row in contacts:
            first = earliest.get(row["key"])
            row["first_observed_at"] = first.isoformat() if first else row["first_received_at"]
        return jsonify({"start": start.isoformat(), "end": end.isoformat(),
                        "capture_since": stamp.isoformat(), "as_of": datetime.now(timezone.utc).isoformat(),
                        "contacts": contacts})
    except Exception:
        app.logger.error("Live activity report unavailable")
        return jsonify({"error": "live report unavailable"}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))

