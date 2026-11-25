import unittest
from datetime import date, timedelta
from decimal import Decimal

from month_projection import (
    active_appointment_ids,
    calendar_month_end,
    filter_active_scheduled_items,
    future_treatment_value,
    plan_created_date,
    projected_total,
    rest_of_month_start,
    scheduled_new_patient_ids,
    sum_scheduled,
)


class CalendarWindowTests(unittest.TestCase):
    def test_previous_month_ends_on_its_last_day(self):
        # October 2026. The old expression resolved to Nov 1, and load_ledger
        # adds one more day because Curve's endDate is exclusive, so the
        # query included November 1.
        inclusive_end = calendar_month_end(date(2026, 10, 1))
        self.assertEqual(inclusive_end, date(2026, 10, 31))
        self.assertEqual(inclusive_end + timedelta(days=1), date(2026, 11, 1))

    def test_month_end_handles_short_and_long_months(self):
        self.assertEqual(calendar_month_end(date(2026, 2, 10)), date(2026, 2, 28))
        self.assertEqual(calendar_month_end(date(2026, 11, 24)), date(2026, 11, 30))
        self.assertEqual(calendar_month_end(date(2026, 12, 1)), date(2026, 12, 31))

    def test_range_ending_today_includes_today(self):
        today = date(2026, 11, 24)
        self.assertEqual(rest_of_month_start(today, today), today)
        self.assertLessEqual(rest_of_month_start(today, today), calendar_month_end(today))

    def test_earlier_end_date_still_starts_the_next_day(self):
        today = date(2026, 11, 24)
        self.assertEqual(rest_of_month_start(date(2026, 11, 20), today), date(2026, 11, 21))

    def test_closed_month_has_nothing_left_to_schedule(self):
        today = date(2026, 11, 24)
        end = date(2026, 10, 31)
        self.assertGreater(rest_of_month_start(end, today), calendar_month_end(end))

    def test_last_day_of_the_month_keeps_today_when_it_is_today(self):
        today = date(2026, 11, 30)
        start = rest_of_month_start(today, today)
        self.assertEqual(start, today)
        self.assertLessEqual(start, calendar_month_end(today))


class ActiveScheduledItemsTests(unittest.TestCase):
    def test_november_2026_audit_totals(self):
        # As of Nov 24 night: active booked work Nov 25-30 was $3,010
        # (Nov 25 $380, Nov 28 $2,382, Nov 30 $248). The other $264 was
        # Cancel, Missed, Delete, Check Out, or an inactive line item.
        appointments = [
            {"id": "nov25", "appointmentStatus": "Active", "patientId": 1},
            {"id": "nov28", "appointmentStatus": "Check In", "patientId": 2},
            {"id": "nov30", "appointmentStatus": "CheckIn", "patientId": 3},
            {"id": "cancel", "appointmentStatus": "Cancel", "patientId": 4},
            {"id": "missed", "appointmentStatus": "Missed", "patientId": 5},
            {"id": "delete", "appointmentStatus": "Delete", "patientId": 6},
            {"id": "checkout", "appointmentStatus": "Check Out", "patientId": 7},
        ]
        items = [
            {"id": 1, "status": "Active", "amount": "380.00", "appointment": {"id": "nov25"}},
            {"id": 2, "status": "Active", "amount": "2382.00", "appointment": {"id": "nov28"}},
            {"id": 3, "status": "Active", "cost": "248.00", "appointment": {"id": "nov30"}},
            {"id": 4, "status": "Active", "amount": "100.00", "appointment": {"id": "cancel"}},
            {"id": 5, "status": "Active", "amount": "80.00", "appointment": {"id": "missed"}},
            {"id": 6, "status": "Active", "amount": "50.00", "appointment": {"id": "delete"}},
            {"id": 7, "status": "Delete", "amount": "20.00", "appointment": {"id": "nov25"}},
            {"id": 8, "status": "Active", "amount": "14.00", "appointment": {"id": "checkout"}},
        ]
        active_ids = active_appointment_ids(appointments)
        self.assertEqual(active_ids, {"nov25", "nov28", "nov30"})
        active_items = filter_active_scheduled_items(items, active_ids)
        self.assertEqual([item["id"] for item in active_items], [1, 2, 3])
        scheduled = sum_scheduled(active_items)
        self.assertEqual(scheduled, Decimal("3010.00"))
        self.assertEqual(sum_scheduled(items), Decimal("3274.00"))
        production = Decimal("34920.98")
        payments = Decimal("31976.36")
        self.assertEqual(projected_total(production, scheduled), Decimal("37930.98"))
        self.assertEqual(payments + Decimal("3274.00"), Decimal("35250.36"))
        self.assertNotEqual(projected_total(payments, sum_scheduled(items)), projected_total(production, scheduled))

    def test_duplicate_item_ids_are_counted_once(self):
        appointments = [{"id": 10, "appointmentStatus": "Active"}]
        items = [
            {"id": 7, "status": "active", "amount": "100", "appointment": {"id": 10}},
            {"id": "7", "status": "Active", "amount": "100", "appointment": {"id": "10"}},
            {"id": 8, "status": "Active", "amount": "40", "appointment": 10},
        ]
        kept = filter_active_scheduled_items(items, active_appointment_ids(appointments))
        self.assertEqual([item["id"] for item in kept], [7, 8])
        self.assertEqual(sum_scheduled(kept), Decimal("140"))

    def test_items_without_an_id_are_kept(self):
        appointments = [{"id": 1, "appointmentStatus": " active "}]
        items = [
            {"status": "Active", "amount": "5", "appointment": {"id": 1}},
            {"status": "Active", "cost": "2", "appointment": {"id": 1}},
        ]
        kept = filter_active_scheduled_items(items, active_appointment_ids(appointments))
        self.assertEqual(len(kept), 2)
        self.assertEqual(sum_scheduled(kept), Decimal("7"))


class FutureNewPatientTests(unittest.TestCase):
    def test_cancel_and_missed_do_not_count_as_a_future_appointment(self):
        appointments = [
            {"id": 1, "appointmentStatus": "Cancel", "patientId": "10"},
            {"id": 2, "appointmentStatus": "Missed", "patientId": "11"},
            {"id": 3, "appointmentStatus": "Cancelled", "patientId": "12"},
            {"id": 4, "appointmentStatus": "Check Out", "patientId": "13"},
            {"id": 5, "appointmentStatus": "Delete", "patientId": "14"},
            {"id": 6, "appointmentStatus": "Active", "patientId": "15"},
            {"id": 7, "appointmentStatus": "Check In", "patientId": "10"},
            {"id": 8, "appointmentStatus": "Active", "patientId": "99"},
        ]
        found = scheduled_new_patient_ids(appointments, {"10", "11", "12", "13", "14", "15"})
        self.assertEqual(found, {"10", "15"})

    def test_future_value_is_not_added_twice(self):
        items = [
            {"id": 1, "cost": "10", "appointment": {"id": 1}},
            {"id": 2, "cost": "5", "appointment": {"id": 9}},
        ]
        # Matching rows only. The old loop then added every item again (25).
        self.assertEqual(future_treatment_value(items, {"1"}), Decimal("10"))

    def test_future_value_falls_back_when_nothing_matches(self):
        items = [
            {"id": 1, "cost": "10", "appointment": {"id": 9}},
            {"id": 2, "cost": "5", "appointment": {"id": 8}},
        ]
        self.assertEqual(future_treatment_value(items, {"1"}), Decimal("15"))
        self.assertEqual(future_treatment_value([], {"1"}), Decimal("0"))

    def test_only_scheduled_or_in_progress_appointments_count(self):
        # status_bucket calls Active / Check In "Scheduled / In Progress".
        appointments = [
            {"appointmentStatus": status, "patientId": str(index)}
            for index, status in enumerate(
                ["Active", "Check In", "CheckIn", "Check Out", "Cancel", "Missed", "Delete"],
                start=1,
            )
        ]
        found = scheduled_new_patient_ids(appointments, {str(i) for i in range(1, 8)})
        self.assertEqual(found, {"1", "2", "3"})


class TreatmentPlanRangeTests(unittest.TestCase):
    def _counts(self, plan, end, patients):
        pid = str((plan.get("patient") or {}).get("id") or "")
        created_date = plan_created_date(plan.get("createdAt"))
        return bool(pid) and pid in patients and created_date is not None and created_date <= end

    def test_prior_month_plans_count_until_the_selected_end(self):
        end = date(2026, 11, 24)
        patients = {"10", "11", "12"}
        prior = {"patient": {"id": 10}, "createdAt": "2026-10-02T15:00:00Z"}
        same_day = {"patient": {"id": "11"}, "createdAt": "2026-11-24 09:30:00"}
        after = {"patient": {"id": 12}, "createdAt": "2026-11-25T00:00:00-05:00"}
        self.assertTrue(self._counts(prior, end, patients))
        self.assertTrue(self._counts(same_day, end, patients))
        self.assertFalse(self._counts(after, end, patients))
        self.assertEqual(plan_created_date("2026-10-02T15:00:00Z"), date(2026, 10, 2))
        self.assertIsNone(plan_created_date(None))
        self.assertIsNone(plan_created_date(""))
        self.assertIsNone(plan_created_date("not-a-date"))

    def test_null_patient_does_not_raise(self):
        plan = {"patient": None, "createdAt": "2026-10-02T15:00:00Z"}
        pid = str((plan.get("patient") or {}).get("id") or "")
        self.assertEqual(pid, "")
        self.assertFalse(self._counts(plan, date(2026, 11, 24), {""}))


if __name__ == "__main__":
    unittest.main()
