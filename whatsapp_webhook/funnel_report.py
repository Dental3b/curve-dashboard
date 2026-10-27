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
    ("conversation_no_booking", "Chatted, not booked"),
    ("initial_no_response", "Initial message, no further reply"),
    ("existing_patients", "Existing patients when they contacted us"),
    ("booked", "Booked for 1st appointment"),
    ("pending_visit", " ↳ Future appointments"),
    ("no_show_or_cancelled", " ↳ No-shows / cancellations"),
    ("arrived", " ↳ Attended 1st appointment"),
    ("paid_only_49", "  ↳ Consultation only ($49)"),
    ("paid_over_49_with_treatment", "  ↳ Paid over $49 + received treatment"),
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
    if set(report) != allowed or report["schema_version"] not in (1, 2):
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
    expected_counts = set(COUNT_FIELDS) | ({"pending_classification"} if report["schema_version"] == 2 else set())
    if not isinstance(counts, dict) or set(counts) != expected_counts:
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
        ("contacts", ("existing_patients", "initial_no_response", "conversation_no_booking", "booked") + (("pending_classification",) if report["schema_version"] == 2 else ())),
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
    if report["schema_version"] == 2 and counts["pending_classification"]:
        metrics.insert(4, ("pending_classification", "In progress / match pending"))
    payment_keys = {"paid_only_49", "paid_over_49_with_treatment", "other_payment_outcome"}
    booking_keys = {"arrived", "pending_visit", "no_show_or_cancelled", "other_booking_outcome"}
    if counts["other_payment_outcome"] is None or counts["other_payment_outcome"] > 0:
        metrics.append(("other_payment_outcome", "  ↳ Other payment outcome / pending verification"))
    if counts["other_booking_outcome"] is None or counts["other_booking_outcome"] > 0:
        insertion = next(i for i, (key, _) in enumerate(metrics) if key == "arrived")
        metrics.insert(insertion, ("other_booking_outcome", " ↳ Appointment status pending verification"))
    rows = []
    for key, label in metrics:
        value = counts[key]
        parent = "arrived" if key in payment_keys else "booked" if key in booking_keys else "contacts"
        denominator = counts[parent]
        basis = "attendees" if key in payment_keys else "bookings" if key in booking_keys else "contacts"
        share = f"{100 * value / denominator:.1f}% of {basis}" if value is not None and denominator else "—"
        rows.append({"Metric": label, "People": value if value is not None else "Pending verification", "Share": share})
    return rows

