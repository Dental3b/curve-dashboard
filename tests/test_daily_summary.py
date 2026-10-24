import base64
import copy
import io
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, patch

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
        self.assertEqual(daily.summarize(DAY, [], [{}]).pending, 1)

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

    def test_local_hours_across_daylight_saving_and_utc_midnight(self):
        cases = [
            ("2026-10-23T19:59:00+00:00", False),
            ("2026-10-23T20:00:00+00:00", True),
            ("2026-11-06T20:59:00+00:00", False),
            ("2026-11-06T21:00:00+00:00", True),
            ("2026-11-07T01:30:00+00:00", True),
            ("2026-11-07T02:00:00+00:00", False),
            ("2026-10-24T18:00:00+00:00", True),
            ("2026-10-24T23:00:00+00:00", False),
            ("2026-10-25T20:00:00+00:00", False),
        ]
        for timestamp, expected in cases:
            with self.subTest(timestamp=timestamp):
                self.assertEqual(daily.in_send_window(datetime.fromisoformat(timestamp)), expected)

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

    def test_changed_totals_restart_close_interval(self):
        state, sender = MemoryState(), Mock()
        daily.process(report(), NOW, state, sender)
        changed = report(collections=Decimal("1800"))
        daily.process(changed, NOW + timedelta(minutes=15), state, sender)
        sender.send.assert_not_called()
        self.assertEqual(state.record["ready_since"], (NOW + timedelta(minutes=15)).isoformat())

    def test_pending_and_zero_production_do_not_send(self):
        for summary in (report(pending=1), report(production=Decimal(0))):
            state, sender = MemoryState(), Mock()
            daily.process(report(), NOW, state, sender)
            daily.process(summary, NOW + timedelta(minutes=15), state, sender)
            sender.send.assert_not_called()
            self.assertEqual(state.record["status"], "waiting")

    def test_no_appointments_waits_until_final_hour(self):
        state, sender = MemoryState(), Mock()
        summary = report(appointments=0)
        daily.process(summary, NOW, state, sender)
        self.assertEqual(state.record, {})
        daily.process(summary, NOW.replace(hour=20), state, sender)
        self.assertEqual(state.record["status"], "ready")

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
        sender = daily.MetaSender("test", "123", "14049666951", "dental3b_resumen_diario", "es", "v23.0")
        with patch.object(daily, "request_json", return_value={"messages": [{"id": "wamid.example"}]}) as api:
            self.assertEqual(sender.send(report()), "wamid.example")
        payload = api.call_args.kwargs["payload"]
        self.assertEqual(payload["type"], "template")
        self.assertEqual(payload["template"]["language"]["code"], "es")
        self.assertEqual([p["text"] for p in payload["template"]["components"][0]["parameters"]],
                         ["23 Oct 2026", "3,381", "1,798", "53.2", "9"])

    def test_no_message_id_is_not_success(self):
        sender = daily.MetaSender("test", "123", "14049666951", "daily", "es", "v23.0")
        with patch.object(daily, "request_json", return_value={}):
            with self.assertRaises(RuntimeError):
                sender.send(report())

    def test_preview_never_uses_sender_or_writes_state(self):
        with patch("sys.argv", ["daily_summary.py", "--date", "2026-10-23"]), \
             patch.object(daily, "get_summary", return_value=report()), \
             patch.object(daily, "MetaSender") as sender, patch.object(daily, "GitHubState") as state:
            daily.main()
        sender.assert_not_called()
        sender.from_environment.assert_not_called()
        state.assert_not_called()

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
