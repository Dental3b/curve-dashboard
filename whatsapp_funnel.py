"""Optional authenticated WhatsApp funnel section for the existing dashboard."""
from __future__ import annotations

from base64 import b64encode
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from whatsapp_webhook.funnel_report import report_rows, validate_report


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


def render_whatsapp_funnel(st, secret, start_date, end_date):
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
    mode = st.radio("Funnel period", ["Selected date range", "Today"], horizontal=True, key="wa_funnel_period")
    if mode == "Today":
        start_date = end_date = datetime.now(ZoneInfo("America/New_York")).date()
    st.caption(f"{start_date:%b %d, %Y} to {end_date:%b %d, %Y} · Atlanta time")
    base_url = secret("WHATSAPP_FUNNEL_URL")
    token = secret("WHATSAPP_FUNNEL_REPORT_TOKEN")
    if not base_url or not token:
        st.info("The funnel is awaiting connection to its verified reports.")
        return
    try:
        report = fetch_funnel_report(base_url, token, start_date.isoformat(), end_date.isoformat())
        if report is None:
            st.info("No verified report is available for these dates yet. Missing data is not shown as zero.")
            return
        rows = report_rows(report)
        contact_order = {
            "People who contacted us": 0,
            "Chatted, not booked": 1,
            "Initial message, no further reply": 2,
            "Existing patients when they contacted us": 3,
        }
        # Keep the contact categories first; preserve the grouped appointment outcomes.
        rows.sort(key=lambda row: contact_order.get(row["Metric"], 4))
        st.dataframe(rows, use_container_width=True, hide_index=True)
        as_of = datetime.fromisoformat(report["as_of"].replace("Z", "+00:00")).astimezone(ZoneInfo(report["timezone"]))
        st.caption(f"Data verified as of {as_of:%b %d, %Y %I:%M %p} · Appointment outcomes are shares of bookings; payment outcomes are shares of attendees.")
        if report["quality"] == "minimum_verified":
            st.warning("Confirmed minimum: the reviewed history may not include every contact.")
        st.caption("Contact categories are separate groups. Appointment and payment stages follow the same people. Treatment received is not proof of verified formal treatment acceptance.")
    except Exception:
        # Never expose credential-bearing request details, patient data, or raw server errors.
        st.warning("The funnel could not be loaded. Curve data remains available; missing data is not replaced with zeros.")
