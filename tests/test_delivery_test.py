import io
import unittest
from unittest.mock import Mock, patch

import daily_summary as daily
import delivery_test
from test_daily_summary import MemoryState, report


class DeliveryTestTests(unittest.TestCase):
    def setUp(self):
        quiet = patch("sys.stdout", new_callable=io.StringIO)
        quiet.start()
        self.addCleanup(quiet.stop)

    def test_one_labeled_example_and_rerun_does_not_duplicate(self):
        state, sender = MemoryState(), Mock()
        sender.send.return_value = "wamid.private"
        delivery_test.send_example(report(), state, sender)
        delivery_test.send_example(report(), state, sender)
        sender.send.assert_called_once()
        values = sender.send.call_args.args[0].parameters()
        self.assertTrue(values[0].startswith("PRUEBA - "))
        self.assertEqual(len(values), 5)
        self.assertEqual(state.record["status"], "accepted")
        self.assertNotIn("message_id", state.record)

    def test_uncertain_result_blocks_retry(self):
        state, sender = MemoryState(), Mock()
        sender.send.side_effect = RuntimeError("request failed")
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                delivery_test.send_example(report(), state, sender)
        sender.send.assert_called_once()

    def test_rejected_send_can_retry_after_configuration_fix(self):
        state, sender = MemoryState(), Mock()
        sender.send.side_effect = [daily.ApiError("Meta", 400), "wamid.private"]
        with self.assertRaises(daily.ApiError):
            delivery_test.send_example(report(), state, sender)
        self.assertEqual(state.record["status"], "waiting")
        delivery_test.send_example(report(), state, sender)
        self.assertEqual(state.record["status"], "accepted")

    def test_failed_reservation_never_posts(self):
        state, sender = MemoryState(), Mock()
        with patch.object(state, "write", side_effect=RuntimeError("conflict")):
            with self.assertRaises(RuntimeError):
                delivery_test.send_example(report(), state, sender)
        sender.send.assert_not_called()

    def test_test_state_is_separate_from_production_date(self):
        with patch.object(daily.GitHubState, "__init__", return_value=None):
            state = delivery_test.DeliveryTestState(report().day)
        self.assertTrue(state.path.endswith("/delivery-test-v1.json"))
        self.assertNotIn(report().day.isoformat(), state.path)

    def test_test_requires_explicit_activation(self):
        with patch.dict("os.environ", {"ALLOW_DELIVERY_TEST": "false"}), \
             patch.object(delivery_test, "MetaSender") as sender:
            with self.assertRaises(RuntimeError):
                delivery_test.main()
        sender.from_environment.assert_not_called()
