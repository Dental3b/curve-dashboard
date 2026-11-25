"""Pure helpers for Financials and the new-patient treatment-plan count.

The Streamlit script loads Curve data and renders it. These functions decide
which appointments still count as scheduled, and which treatment plans belong
to the selected range, so the rules can be tested without starting the app.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

# Curve appointment statuses that are still on the book. Check Out is posted
# production. Cancel, Missed, and Delete are not scheduled treatment.
ACTIVE_APPOINTMENT_STATUSES = {"active", "check in", "checkin"}


def calendar_month_end(day: date) -> date:
    """Last calendar day of the month that contains ``day``."""
    first_of_next = (day.replace(day=1) + timedelta(days=32)).replace(day=1)
    return first_of_next - timedelta(days=1)


def rest_of_month_start(end_date: date, today: date) -> date:
    """First day whose appointments are not already inside the selected range.

    When the range ends today, today itself still has to be included: visits
    left Active or Check In have not been posted to the ledger yet. Check Out
    visits are dropped later so that production is not counted twice. Any
    other end date keeps the previous behavior and starts the next day.
    """
    if end_date == today:
        return end_date
    return end_date + timedelta(days=1)


def _status(value) -> str:
    return str(value or "").strip().lower()


def item_appointment_id(item: dict) -> str:
    reference = item.get("appointment") or {}
    if isinstance(reference, dict):
        return str(reference.get("id") or "").strip()
    return str(reference).strip()


def appointment_ids(appointments: list[dict] | None) -> set[str]:
    ids: set[str] = set()
    for appt in appointments or []:
        appt_id = appt.get("id")
        if appt_id is None or str(appt_id).strip() == "":
            continue
        ids.add(str(appt_id).strip())
    return ids


def active_appointment_ids(appointments: list[dict] | None) -> set[str]:
    """Ids of appointments still Active or Check In."""
    ids: set[str] = set()
    for appt in appointments or []:
        if _status(appt.get("appointmentStatus")) not in ACTIVE_APPOINTMENT_STATUSES:
            continue
        appt_id = appt.get("id")
        if appt_id is None or str(appt_id).strip() == "":
            continue
        ids.add(str(appt_id).strip())
    return ids


def scheduled_new_patient_ids(appointments: list[dict] | None, new_patient_ids: set[str] | None) -> set[str]:
    """New patients who still have an Active or Check In appointment.

    Cancel, Missed, Delete, and Check Out do not count as a future appointment.
    """
    wanted = {str(pid).strip() for pid in (new_patient_ids or set()) if str(pid).strip()}
    found: set[str] = set()
    for appt in appointments or []:
        if _status(appt.get("appointmentStatus")) not in ACTIVE_APPOINTMENT_STATUSES:
            continue
        pid = str(appt.get("patientId") or "").strip()
        if pid and pid in wanted:
            found.add(pid)
    return found


def filter_active_scheduled_items(items: list[dict] | None, appointment_ids_active: set[str]) -> list[dict]:
    """Active line items on Active/Check In appointments, one row per item id.

    Items on Cancel, Missed, Delete, or Check Out appointments are left out,
    as are line items whose own status is not active. A repeated item id
    (pagination overlap) is kept once.
    """
    seen: set[str] = set()
    kept: list[dict] = []
    allowed = {str(appt_id).strip() for appt_id in appointment_ids_active}
    for item in items or []:
        if _status(item.get("status")) != "active":
            continue
        appt_id = item_appointment_id(item)
        if appt_id not in allowed:
            continue
        item_id = item.get("id")
        if item_id is not None and str(item_id).strip() != "":
            key = str(item_id).strip()
            if key in seen:
                continue
            seen.add(key)
        kept.append(item)
    return kept


def item_amount(item: dict) -> Decimal:
    return Decimal(str(item.get("amount") or item.get("cost") or 0))


def sum_scheduled(items: list[dict] | None) -> Decimal:
    total = Decimal(0)
    for item in items or []:
        total += item_amount(item)
    return total


def projected_total(production, scheduled) -> Decimal:
    """Adjusted production already posted, plus treatment still booked."""
    return Decimal(production) + Decimal(scheduled)


def future_treatment_value(items: list[dict] | None, known_appointment_ids: set[str]) -> Decimal:
    """Sum item cost once.

    Items tied to a loaded appointment are the total. When none match, fall
    back to every item in the window. The old screen added both.
    """
    known = {str(appt_id).strip() for appt_id in known_appointment_ids}
    matched = Decimal(0)
    saw_match = False
    for item in items or []:
        if item_appointment_id(item) not in known:
            continue
        matched += Decimal(str(item.get("cost") or 0))
        saw_match = True
    if saw_match:
        return matched
    total = Decimal(0)
    for item in items or []:
        total += Decimal(str(item.get("cost") or 0))
    return total


def plan_created_date(created_at) -> date | None:
    """Calendar date of a treatment plan's createdAt.

    Missing or unreadable values return None so they are not counted.
    """
    if isinstance(created_at, datetime):
        return created_at.date()
    if isinstance(created_at, date):
        return created_at
    text = str(created_at or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return parsed.date()
