"""Optional authenticated WhatsApp funnel section for the existing dashboard."""
from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from whatsapp_webhook.funnel_report import report_rows, validate_report
from whatsapp_live import fetch_live_contacts, build_live_report, LiveNotReady, stamp


def fetch_funnel_report(base_url: str, token: str, start: str, end: str, session=None):
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Invalid funnel service configuration")
    if not token:
        raise ValueError("Missing report credential")
    if session is None:
        import requests
        session = requests
    response = session.get(
        base_url.rstrip("/") + "/api/funnel",
        params={"start": start, "end": end},
        headers={"Authorization": "Bearer " + token},
        timeout=20,
        allow_redirects=False,
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise RuntimeError("Funnel report could not be loaded")
    report = validate_report(response.json())
    if (report["start"], report["end"]) != (start, end):
        raise ValueError("Report does not match selected dates")
    return report


def load_curve_baseline(_api, instance, start, today):
    # Cache stable history. Capture the boundary before reading so concurrent changes are not lost.
    baseline_since = datetime.now(ZoneInfo("UTC")).isoformat()
    patients = _api.get_patients()
    history_end = datetime.combine(start, datetime.min.time()) - timedelta(seconds=1)
    history = _api.get_appointments(datetime(2000, 1, 1), history_end)
    ledger = _api.get_ledger_entries(start.isoformat(), today.isoformat(), max_pages=100) if start < today else []
    items = _api.get_appointment_items(start.isoformat(), today.isoformat()) if start < today else []
    return patients, history, ledger, items, baseline_since


def load_live_curve(_api, instance, start, today, baseline_since):
    changed = _api.get_patients(changed_since=baseline_since)
    appointments = _api.get_appointments(datetime.combine(start, datetime.min.time()), datetime(2100, 1, 1))
    ledger = _api.get_ledger_entries(today.date().isoformat(), (today + timedelta(days=1)).date().isoformat(), max_pages=100)
    items = _api.get_appointment_items(today.date().isoformat(), (today + timedelta(days=1)).date().isoformat())
    return changed, appointments, ledger, items


def render_whatsapp_funnel(st, secret, start_date, end_date, api=None):
    whatsapp_logo = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<circle cx="16" cy="16" r="16" fill="#25D366"/>'
        '<path fill="#fff" d="M16 5a10 10 0 0 0-8.6 15.1L6 26l6-1.5A10 10 0 1 0 16 5zm0 18.2'
        'a8.2 8.2 0 0 1-4.2-1.2l-.3-.2-3.2.8.8-3.1-.2-.3A8.2 8.2 0 1 1 16 23.2z"/>'
        '<path fill="#fff" d="M12 10.4c-.3-.6-.6-.6-.9-.6h-.7c-.3 0-.7.1-1 .5'
        '-.4.4-1.3 1.3-1.3 3.1s1.4 3.5 1.6 3.8c.2.2 2.7 4.1 6.5 5.5'
        '3.2 1.1 3.8.8 4.5.7.7-.1 2.2-.9 2.5-1.7.3-.9.3-1.6.2-1.7'
        '-.1-.2-.4-.3-.8-.5l-2.6-1.2c-.4-.1-.6-.2-.9.2l-1.1 1.3'
        'c-.2.3-.4.3-.8.1-1.8-.9-3-1.7-4.2-3.8-.3-.5.3-.5.9-1.6'
        '.1-.2.1-.5 0-.7z" transform="translate(3 3) scale(.75)"/>'
        '</svg>'
    )
    logo_data = b64encode(whatsapp_logo.encode()).decode()
    st.markdown(
        '<h3 style="display:flex;align-items:center;gap:14px;">'
        f'<img src="data:image/svg+xml;base64,{logo_data}" width="22" height="22" alt="WhatsApp">'
        'WhatsApp Funnel</h3>',
        unsafe_allow_html=True,
    )
    st.caption("Selected date range")
    st.caption(f"{start_date:%b %d, %Y} to {end_date:%b %d, %Y} · Atlanta time")
    base_url = secret("WHATSAPP_FUNNEL_URL")
    token = secret("WHATSAPP_FUNNEL_REPORT_TOKEN")
    if not base_url or not token:
        st.info("The funnel is awaiting connection to its verified reports.")
        return
    try:
        if api is None:
            raise LiveNotReady("Curve connection unavailable")
        now = datetime.now(ZoneInfo("America/New_York"))
        if start_date > end_date or end_date > now.date():
            st.info("Choose a date range ending today or earlier.")
            return
        # Check receiver readiness before an expensive first historical Curve load.
        availability = fetch_live_contacts(base_url, token, start_date.isoformat(), end_date.isoformat(), [])
        since = stamp(availability["capture_since"])
        if datetime.combine(end_date, datetime.max.time(), tzinfo=ZoneInfo("America/New_York")) < since:
            report = fetch_funnel_report(base_url, token, start_date.isoformat(), end_date.isoformat())
            if report is None:
                st.info("No verified historical report is available for the selected dates.")
            else:
                st.dataframe(report_rows(report), use_container_width=True, hide_index=True)
                st.caption(f"Historical snapshot through {report['end']}. These dates precede live capture.")
            return
        baseline = st.cache_data(ttl=3600, show_spinner=False)(load_curve_baseline)
        patients, history, historical_ledger, historical_items, boundary = baseline(api, api.instance, start_date, now.date())
        loader = st.cache_data(ttl=60, show_spinner=False)(load_live_curve)
        changed, appointments, ledger, items = loader(api, api.instance, start_date, datetime.combine(now.date(), datetime.min.time()), boundary)
        registry = {str(patient["id"]): patient for patient in patients}
        registry.update({str(patient["id"]): patient for patient in changed})
        patients = list(registry.values())
        appointments = history + appointments
        ledger = historical_ledger + ledger
        items = historical_items + items
        activity = fetch_live_contacts(base_url, token, start_date.isoformat(), end_date.isoformat(), patients)
        since = stamp(activity["capture_since"])
        if datetime.combine(end_date, datetime.max.time(), tzinfo=ZoneInfo("America/New_York")) < since:
            st.info("The selected dates precede automatic capture.")
            return
        report = build_live_report(activity, appointments, ledger, items, now=now)
        st.dataframe(report_rows(report), use_container_width=True, hide_index=True)
        st.caption(f"Updated {now:%b %d, %Y %I:%M:%S %p} · Atlanta time · Refreshes every 60 seconds while open.")
        st.caption("WhatsApp activity is captured in the cloud even when this dashboard is closed. Today’s Curve records refresh every 60 seconds while open; older records are rechecked hourly.")
        if datetime.combine(start_date, datetime.min.time(), tzinfo=ZoneInfo("America/New_York")) < since:
            st.warning(f"Partial coverage: automatic activity starts {since:%b %d, %Y %I:%M %p}. Earlier messages are not included in these live totals. Historical snapshots are separate.")
        st.caption("Each person counts once per date range. Shared phone matches and recent single messages remain In progress / match pending. A single message moves to Initial message, no further reply after 24 hours. Treatment requires a completed visit, a billed clinical procedure and collections over $49; formal acceptance is not inferred.")
    except LiveNotReady:
        st.warning("Live capture is not available yet. The cloud receiver must be updated once. Historical reports use the same selected date range.")
    except Exception:
        st.warning("Live data could not be refreshed. Missing data is not replaced with zeros. Please try again shortly.")
