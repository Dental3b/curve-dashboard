import base64
import copy
import io
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import daily_summary as daily


DAY = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 17, tzinfo=daily.EASTERN)


class MemoryState:
    def __init__(self, record=None):
        self.record = record or {}

    def read(self):
        return copy.deepcopy(self.record)

    def write(self, value):
        self.record = copy.deepcopy(value)


def report(**changes):
    values = dict(day=DAY, production=Decimal("3381"), collections=Decimal("1798.10"),
                  patients=9, appointments=10, pending=0)
    values.update(changes)
    return daily.Summary(**values)


class DailySummaryTests(unittest.TestCase):
    def setUp(self):
        quiet = patch("sys.stdout", new_callable=io.StringIO)
        quiet.start()
        self.addCleanup(quiet.stop)

    def test_ledger_uses_calculations_not_double_entry_amounts(self):
        ledger = [
            {"amount": "100", "patientId": 1, "calculations": {"production": "100"}},
            {"amount": "100", "patientId": 1, "calculations": {}},
            {"patientId": "1", "calculations": {"production": "0.10", "payments": "50.10"}},
            {"patientId": 2, "calculations": {"production": "-10", "payments": "0.20"}},
        ]
        result = daily.summarize(DAY, ledger, [{"appointmentStatus": "Check Out"}, {"appointmentStatus": "Missed"}])
        self.assertEqual((result.production, result.collections), (Decimal("90.10"), Decimal("50.30")))
        self.assertEqual((result.patients, result.pending), (1, 0))

    def test_unknown_appointment_status_blocks_close(self):
        for status in (None, "Left Message", "Cut", "Active", "Check In"):
            with self.subTest(status=status):
                self.assertEqual(daily.summarize(DAY, [], [{"appointmentStatus": status}]).pending, 1)

    def test_deposit_and_its_later_account_credit_application_count_once(self):
        ledger = [
            {"calculations": {"payments": "49.25"}},
            {"calculations": {"deposits": "300.00", "payments": "0"}},
            {"calculations": {"paymentsFromAccountCredit": "300.00", "totalPayments": "300.00"}},
            {"amount": "300.00", "calculations": {}},
        ]
        summary = daily.summarize(DAY, ledger, [])
        self.assertEqual(summary.collections, Decimal("349.25"))
        self.assertEqual(summary.parameters()[2], "349.25")

    def test_existing_credit_without_new_deposit_is_not_new_money(self):
        ledger = [{"calculations": {"paymentsFromAccountCredit": "300", "totalPayments": "300"}}]
        self.assertEqual(daily.summarize(DAY, ledger, []).collections, Decimal(0))

    def test_deposit_with_no_payment_still_counts(self):
        ledger = [{"calculations": {"deposits": "125.10"}}]
        self.assertEqual(daily.summarize(DAY, ledger, []).collections, Decimal("125.10"))

    def test_invalid_deposit_blocks_the_report(self):
        with self.assertRaises(RuntimeError):
            daily.summarize(DAY, [{"calculations": {"deposits": "NaN"}}], [])

    def test_attended_cancelled_and_missed_appointments_allow_close(self):
        appointments = [{"appointmentStatus": status} for status in
                        ("Check Out", "Missed", "Cancel", "Cancelled", "Canceled")]
        self.assertEqual(daily.summarize(DAY, [], appointments).pending, 0)

    def test_appointment_fingerprint_tracks_changes_but_not_list_order(self):
        appointments = [{"id": 1, "appointmentStatus": "Check Out"},
                        {"id": 2, "appointmentStatus": "Cancel"}]
        original = daily.summarize(DAY, [], appointments)
        reordered = daily.summarize(DAY, [], list(reversed(appointments)))
        self.assertEqual(original.fingerprint(), reordered.fingerprint())
        appointments[1]["id"] = 3
        self.assertNotEqual(original.fingerprint(), daily.summarize(DAY, [], appointments).fingerprint())

    def test_token_reader_accepts_google_cloud_config_without_other_fields(self):
        value = json.dumps({"WHATSAPP_TOKEN": "EAAexample", "OTHER_SECRET": "do-not-use"}, indent=2)
        with patch.dict("os.environ", {"WHATSAPP_TOKEN": value}):
            self.assertEqual(daily.whatsapp_token(), "EAAexample")

    def test_token_reader_accepts_raw_wrapped_and_env_values(self):
        cases = ["EAAexample", "EAAex\nample", '"EAAexample"',
                 'WHATSAPP_TOKEN="EAAexample"\nWHATSAPP_PHONE_NUMBER_ID=123',
                 'WHATSAPP_PHONE_NUMBER_ID=123\nWHATSAPP_TOKEN="EAAexample"']
        for value in cases:
            with self.subTest(value=value), patch.dict("os.environ", {"WHATSAPP_TOKEN": value}):
                self.assertEqual(daily.whatsapp_token(), "EAAexample")

    def test_token_reader_rejects_incomplete_or_missing_config_without_echoing_it(self):
        for value in ('{"WHATSAPP_TOKEN": "sensitive', '{"OTHER_SECRET":"sensitive"}', 'WHATSAPP_TOKEN: sensitive'):
            with self.subTest(value=value), patch.dict("os.environ", {"WHATSAPP_TOKEN": value}):
                with self.assertRaises(RuntimeError) as error:
                    daily.whatsapp_token()
                self.assertNotIn("sensitive", str(error.exception))

    def test_all_ledger_pages_and_exclusive_end_date(self):
        with patch.object(daily, "curve_token", return_value="test"), patch.object(daily, "request_json", side_effect=[
            {"objects": [{"patientId": 1, "calculations": {"production": "1"}}], "meta": {"next": "/cheetah/ledger_entry?state=2"}},
            {"objects": [{"patientId": 2, "calculations": {"production": "2", "payments": "3"}}]},
            {"objects": [{"appointmentStatus": "Check Out"}], "meta": {"next": "?pageToken=2"}},
            {"objects": [{"appointmentStatus": "Active"}]},
        ]) as api:
            result = daily.get_summary(DAY)
        self.assertEqual((result.production, result.collections, result.patients, result.pending), (Decimal(3), Decimal(3), 2, 1))
        self.assertIn("endDate=2026-10-24", api.call_args_list[0].args[2])
        self.assertEqual(api.call_count, 4)

    def test_pagination_failure_does_not_return_partial_totals(self):
        with patch.object(daily, "request_json", side_effect=[{"objects": [], "meta": {"next": "?page=2"}}, daily.ApiError("Curve", 500)]):
            with self.assertRaises(daily.ApiError):
                daily.curve_pages("https://example.org/ledger", "test", {})

    def test_pagination_rejects_foreign_host_and_missing_objects(self):
        for response in ({"objects": [], "meta": {"next": "https://elsewhere.test/ledger"}}, {"meta": {}}):
            with patch.object(daily, "request_json", return_value=response) as api:
                with self.assertRaises(RuntimeError):
                    daily.curve_pages("https://example.org/ledger", "test", {})
                self.assertEqual(api.call_count, 1)

    def test_early_closes_and_weekends_do_not_wait_for_fixed_hours(self):
        for timestamp in ("2026-10-23T10:00:00-04:00", "2026-11-06T09:00:00-05:00",
                          "2026-10-24T11:00:00-04:00", "2026-10-25T10:00:00-04:00",
                          "2026-11-07T03:00:00+00:00"):
            with self.subTest(timestamp=timestamp):
                now = datetime.fromisoformat(timestamp)
                summary = report(day=now.astimezone(daily.EASTERN).date())
                state, sender = MemoryState(), Mock()
                daily.process(summary, now, state, sender)
                daily.process(summary, now + timedelta(minutes=15), state, sender)
                sender.send.assert_called_once()

    def test_retrieval_crossing_local_midnight_does_not_send_yesterdays_report(self):
        state, sender = MemoryState(), Mock()
        with self.assertRaises(RuntimeError):
            daily.process(report(), NOW + timedelta(days=1), state, sender)
        sender.send.assert_not_called()
        self.assertEqual(state.record, {})

    def test_waits_fifteen_minutes_then_sends_only_once(self):
        state, sender = MemoryState(), Mock()
        sender.send.return_value = "wamid.example"
        daily.process(report(), NOW, state, sender)
        daily.process(report(), NOW + timedelta(minutes=14), state, sender)
        sender.send.assert_not_called()
        daily.process(report(), NOW + timedelta(minutes=15), state, sender)
        daily.process(report(), NOW + timedelta(minutes=30), state, sender)
        sender.send.assert_called_once()
        self.assertEqual(state.record["status"], "accepted")

    def test_changed_totals_do_not_delay_patient_close_and_send_latest_values(self):
        state, sender = MemoryState(), Mock()
        daily.process(report(), NOW, state, sender)
        changed = report(collections=Decimal("1800"))
        daily.process(changed, NOW + timedelta(minutes=15), state, sender)
        sender.send.assert_called_once()
        self.assertEqual(sender.send.call_args.args[0].parameters()[2], "1,800.00")

    def test_pending_and_empty_schedules_cancel_the_timer(self):
        for summary in (report(pending=1), report(appointments=0)):
            state, sender = MemoryState(), Mock()
            daily.process(report(), NOW, state, sender)
            daily.process(summary, NOW + timedelta(minutes=15), state, sender)
            sender.send.assert_not_called()
            self.assertEqual(state.record["status"], "waiting")

    def test_no_appointments_never_uses_a_fixed_closing_hour(self):
        state, sender = MemoryState(), Mock()
        summary = report(appointments=0)
        daily.process(summary, NOW, state, sender)
        self.assertEqual(state.record, {})
        daily.process(summary, NOW.replace(hour=20), state, sender)
        self.assertEqual(state.record, {})
        sender.send.assert_not_called()

    def test_cancelled_day_without_production_still_reports_close(self):
        state, sender = MemoryState(), Mock()
        summary = daily.summarize(DAY, [], [{"appointmentStatus": "Cancel"}, {"appointmentStatus": "Missed"}])
        daily.process(summary, NOW, state, sender)
        daily.process(summary, NOW + timedelta(minutes=15), state, sender)
        sender.send.assert_called_once()
        self.assertEqual(sender.send.call_args.args[0].parameters()[1:], ["0.00", "0.00", "0.0", "0"])

    def test_reopened_appointment_restarts_fifteen_minutes_after_new_close(self):
        state, sender = MemoryState(), Mock()
        daily.process(report(), NOW, state, sender)
        daily.process(report(pending=1), NOW + timedelta(minutes=10), state, sender)
        daily.process(report(), NOW + timedelta(minutes=15), state, sender)
        daily.process(report(), NOW + timedelta(minutes=29), state, sender)
        sender.send.assert_not_called()
        daily.process(report(), NOW + timedelta(minutes=30), state, sender)
        sender.send.assert_called_once()

    def test_new_completed_appointment_restarts_timer_even_without_pending_status(self):
        state, sender = MemoryState(), Mock()
        daily.process(report(appointment_fingerprint="first"), NOW, state, sender)
        changed = report(appointment_fingerprint="changed")
        daily.process(changed, NOW + timedelta(minutes=15), state, sender)
        sender.send.assert_not_called()
        daily.process(changed, NOW + timedelta(minutes=30), state, sender)
        sender.send.assert_called_once()

    def test_old_ready_record_must_start_a_new_appointment_timer(self):
        state = MemoryState({"status": "ready", "fingerprint": report().fingerprint(),
                             "ready_since": (NOW - timedelta(hours=2)).isoformat()})
        sender = Mock()
        daily.process(report(), NOW, state, sender)
        sender.send.assert_not_called()
        self.assertEqual(state.record["ready_since"], NOW.isoformat())

    def test_close_message_includes_detected_time_not_send_time_with_five_parameters(self):
        state, sender = MemoryState(), Mock()
        daily.process(report(), NOW.replace(hour=13, minute=5), state, sender)
        daily.process(report(), NOW.replace(hour=13, minute=20), state, sender)
        values = sender.send.call_args.args[0].parameters()
        self.assertEqual(len(values), 5)
        self.assertEqual(values[0], "23 Oct 2026 · Cierre detectado: 1:05 p. m. (hora de Georgia)")
        self.assertEqual(values[1:], report().parameters()[1:])

    def test_close_time_uses_georgia_time_in_summer_and_winter(self):
        for timestamp, day, clock in (("2026-10-24T00:30:00+00:00", DAY, "8:30 p. m."),
                                      ("2026-11-06T15:30:00+00:00", date(2026, 11, 6), "10:30 a. m.")):
            values = daily.CloseMessage(report(day=day), datetime.fromisoformat(timestamp)).parameters()
            self.assertIn(clock, values[0])

    def test_uncertain_send_blocks_automatic_retries(self):
        state, sender = MemoryState(), Mock()
        sender.send.side_effect = RuntimeError("network timeout")
        daily.process(report(), NOW, state, sender)
        with self.assertRaises(RuntimeError):
            daily.process(report(), NOW + timedelta(minutes=15), state, sender)
        self.assertEqual(state.record["status"], "sending")
        with self.assertRaises(RuntimeError):
            daily.process(report(), NOW + timedelta(minutes=30), state, sender)
        sender.send.assert_called_once()

    def test_explicit_meta_rejection_can_retry_after_fix(self):
        state, sender = MemoryState(), Mock()
        sender.send.side_effect = [daily.ApiError("Meta", 400), "wamid.example"]
        daily.process(report(), NOW, state, sender)
        with self.assertRaises(daily.ApiError):
            daily.process(report(), NOW + timedelta(minutes=15), state, sender)
        self.assertEqual(state.record["status"], "ready")
        daily.process(report(), NOW + timedelta(minutes=30), state, sender)
        self.assertEqual(state.record["status"], "accepted")

    def test_failed_state_reservation_prevents_send(self):
        state, sender = MemoryState(), Mock()
        daily.process(report(), NOW, state, sender)
        with patch.object(state, "write", side_effect=RuntimeError("state conflict")):
            with self.assertRaises(RuntimeError):
                daily.process(report(), NOW + timedelta(minutes=15), state, sender)
        sender.send.assert_not_called()

    def test_lost_acceptance_record_does_not_duplicate(self):
        state, sender = MemoryState(), Mock()
        sender.send.return_value = "wamid.example"
        daily.process(report(), NOW, state, sender)
        original_write = state.write

        def fail_after_send(value):
            if value["status"] == "accepted":
                raise RuntimeError("state unavailable")
            original_write(value)

        with patch.object(state, "write", side_effect=fail_after_send):
            with self.assertRaises(RuntimeError):
                daily.process(report(), NOW + timedelta(minutes=15), state, sender)
        self.assertEqual(state.record["status"], "sending")
        with self.assertRaises(RuntimeError):
            daily.process(report(), NOW + timedelta(minutes=30), state, sender)
        sender.send.assert_called_once()

    def test_meta_uses_approved_template_and_expected_parameters(self):
        sender = daily.MetaSender("test", "123", "12025550123", "dental3b_resumen_diario", "es", "v23.0")
        with patch.object(daily, "request_json", return_value={"messages": [{"id": "wamid.example"}]}) as api:
            self.assertEqual(sender.send(report()), "wamid.example")
        payload = api.call_args.kwargs["payload"]
        self.assertEqual(payload["type"], "template")
        self.assertEqual(payload["template"]["language"]["code"], "es")
        self.assertEqual([p["text"] for p in payload["template"]["components"][0]["parameters"]],
                         ["23 Oct 2026", "3,381.00", "1,798.10", "53.2", "9"])

    def test_no_message_id_is_not_success(self):
        sender = daily.MetaSender("test", "123", "12025550123", "daily", "es", "v23.0")
        with patch.object(daily, "request_json", return_value={}):
            with self.assertRaises(RuntimeError):
                sender.send(report())

    def test_meta_error_exposes_numeric_code_without_response_details(self):
        body = json.dumps({"error": {"code": 132001, "error_subcode": 42,
                                  "message": "private response value"}}).encode()
        error = HTTPError("https://graph.facebook.com/test", 400, "Bad Request", {}, io.BytesIO(body))
        with patch.object(daily, "urlopen", side_effect=error):
            with self.assertRaises(daily.ApiError) as caught:
                daily.request_json("Meta WhatsApp", "POST", "https://graph.facebook.com/test", payload={})
        self.assertEqual((caught.exception.status, caught.exception.code), (400, 132001))
        self.assertNotIn("private response value", str(caught.exception))

    def test_error_code_never_echoes_non_numeric_fields(self):
        error = daily.ApiError("Meta WhatsApp", 400, "private code", {"token": "private"})
        self.assertNotIn("private", str(error))
        self.assertIsNone(error.code)

    def test_preview_never_uses_sender_or_writes_state(self):
        with patch("sys.argv", ["daily_summary.py", "--date", "2026-10-23"]), \
             patch.object(daily, "get_summary", return_value=report()), \
             patch.object(daily, "MetaSender") as sender, patch.object(daily, "GitHubState") as state:
            daily.main()
        sender.assert_not_called()
        sender.from_environment.assert_not_called()
        state.assert_not_called()

    def test_send_entrypoint_detects_early_sunday_close(self):
        early = datetime(2026, 10, 25, 10, tzinfo=daily.EASTERN)
        summary = report(day=early.date())
        state, sender = MemoryState(), Mock()
        with patch("sys.argv", ["daily_summary.py", "--send"]), \
             patch.dict("os.environ", {"DAILY_SUMMARY_ENABLED": "true"}), \
             patch.object(daily, "datetime") as clock, \
             patch.object(daily, "get_summary", return_value=summary) as fetch, \
             patch.object(daily.MetaSender, "from_environment", return_value=sender), \
             patch.object(daily, "GitHubState", return_value=state):
            clock.now.return_value = early
            daily.main()
        fetch.assert_called_once_with(early.date())
        self.assertEqual(state.record["status"], "ready")
        sender.send.assert_not_called()

    def test_send_entrypoint_skips_curve_after_report_was_accepted(self):
        state = MemoryState({"status": "accepted"})
        with patch("sys.argv", ["daily_summary.py", "--send"]), \
             patch.dict("os.environ", {"DAILY_SUMMARY_ENABLED": "true"}), \
             patch.object(daily, "get_summary") as fetch, \
             patch.object(daily.MetaSender, "from_environment") as sender, \
             patch.object(daily, "GitHubState", return_value=state):
            daily.main()
        fetch.assert_not_called()
        sender.return_value.send.assert_not_called()

    def test_durable_state_writes_with_current_sha(self):
        content = base64.b64encode(json.dumps({"status": "accepted"}).encode()).decode()
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_TOKEN": "test"}), \
             patch.object(daily, "request_json", side_effect=[{}, {"sha": "old", "content": content}, {"content": {"sha": "new"}}]) as api:
            state = daily.GitHubState(DAY)
            self.assertEqual(state.read()["status"], "accepted")
            state.write({"status": "ready"})
        self.assertEqual(api.call_args.kwargs["payload"]["sha"], "old")
        self.assertEqual(state.sha, "new")


if __name__ == "__main__":
    unittest.main()
