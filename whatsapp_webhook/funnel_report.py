"""Validated aggregate snapshots; never accept patient identifiers or message text."""
from __future__ import annotations

from datetime import date, datetime

COUNT_FIELDS = (
    "contacts", "existing_patients", "initial_no_response", "conversation_no_booking",
    "booked", "arrived", "paid_only_49", "paid_over_49_with_treatment",
    "other_payment_outcome", "pending_visit", "no_show_or_cancelled", "other_booking_outcome",
)
METRICS = (
    ("contacts", "People who contacted us"),
    ("existing_patients", "Existing patients when they contacted us"),
    ("initial_no_response", "Initial message, no further reply"),
    ("conversation_no_booking", "Contacts with no appointment found"),
    ("booked", "Booked before their first visit"),
    ("arrived", "Attended"),
    ("paid_only_49", "Paid only $49"),
    ("paid_over_49_with_treatment", "Paid over $49 and received treatment"),
    ("pending_visit", "Future appointment, not yet attended"),
    ("no_show_or_cancelled", "No-shows / cancellations"),
)


def iso_date(value: str) -> date:
    if not isinstance(value, str):
        raise ValueError("Date must be YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Date must be YYYY-MM-DD")
    return parsed


def validate_report(report: dict) -> dict:
    if not isinstance(report, dict):
        raise ValueError("Report must be an object")
    allowed = {"schema_version", "start", "end", "as_of", "timezone", "quality", "counts"}
    if set(report) != allowed or report["schema_version"] != 1:
        raise ValueError("Unsupported aggregate report schema")
    start, end = iso_date(report["start"]), iso_date(report["end"])
    if start > end:
        raise ValueError("Start must not follow end")
    stamp = datetime.fromisoformat(report["as_of"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("as_of must include its time zone")
    if report["timezone"] != "America/New_York":
        raise ValueError("Report must use the clinic time zone")
    if report["quality"] not in {"minimum_verified", "complete"}:
        raise ValueError("Invalid report quality")
    counts = report["counts"]
    if not isinstance(counts, dict) or set(counts) != set(COUNT_FIELDS):
        raise ValueError("Unexpected count fields")
    for value in counts.values():
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("Counts must be nonnegative integers or null")
    if counts["contacts"] is None:
        raise ValueError("Contact count is required")
    for value in counts.values():
        if value is not None and value > counts["contacts"]:
            raise ValueError("Stage exceeds contact count")
    partitions = (
        ("contacts", ("existing_patients", "initial_no_response", "conversation_no_booking", "booked")),
        ("booked", ("arrived", "pending_visit", "no_show_or_cancelled", "other_booking_outcome")),
        ("arrived", ("paid_only_49", "paid_over_49_with_treatment", "other_payment_outcome")),
    )
    for parent, children in partitions:
        parent_count = counts[parent]
        known = [counts[key] for key in children if counts[key] is not None]
        if parent_count is not None:
            if sum(known) > parent_count or (len(known) == len(children) and sum(known) != parent_count):
                raise ValueError("Funnel categories do not reconcile")
    return report


def report_rows(report: dict) -> list[dict]:
    validate_report(report)
    counts = report["counts"]
    metrics = list(METRICS)
    if counts["other_payment_outcome"] is None or counts["other_payment_outcome"] > 0:
        metrics.append(("other_payment_outcome", "Other payment outcome / pending verification"))
    if counts["other_booking_outcome"] is None or counts["other_booking_outcome"] > 0:
        metrics.append(("other_booking_outcome", "Appointment status pending verification"))
    total = counts["contacts"]
    return [
        {"Metric": label, "People": counts[key] if counts[key] is not None else "Pending verification",
         "% of contacts": (f"{100 * counts[key] / total:.1f}%" if total else "—")
         if counts[key] is not None else "—"}
        for key, label in metrics
    ]
