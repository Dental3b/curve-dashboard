import io
import subprocess
import unittest
from unittest.mock import Mock, patch

import monitor_daily_summary as watcher


class FakeClock:
    def __init__(self):
        self.seconds = 0

    def now(self):
        return self.seconds

    def sleep(self, seconds):
        self.seconds += seconds


class MonitorTests(unittest.TestCase):
    def setUp(self):
        quiet = patch("sys.stdout", new_callable=io.StringIO)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.clock = FakeClock()

    def test_checks_continue_without_another_scheduler_event(self):
        times = []

        def run(command, **kwargs):
            times.append(self.clock.now())
            self.assertEqual(command[-1], "--send")
            self.assertTrue(command[-2].endswith("daily_summary.py"))
            return Mock(returncode=0)

        code = watcher.monitor(duration=16 * 60, clock=self.clock.now, sleep=self.clock.sleep, run=run)
        self.assertEqual(times, [0, 300, 600, 900])
        self.assertEqual(code, 0)
        self.assertEqual(self.clock.now(), 16 * 60)

    def test_request_duration_does_not_accumulate_in_poll_interval(self):
        times = []

        def run(*args, **kwargs):
            times.append(self.clock.now())
            self.clock.sleep(40)
            return Mock(returncode=0)

        watcher.monitor(duration=610, clock=self.clock.now, sleep=self.clock.sleep, run=run)
        self.assertEqual(times, [0, 300, 600])

    def test_failure_and_timeout_do_not_stop_later_checks_or_reset_state(self):
        results = iter([1, "timeout", 0, 0])
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            result = next(results)
            if result == "timeout":
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return Mock(returncode=result)

        code = watcher.monitor(duration=960, clock=self.clock.now, sleep=self.clock.sleep, run=run)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(c == calls[0] for c in calls))

    def test_check_timeout_is_bounded_by_remaining_monitor_time(self):
        timeouts = []

        def run(*args, timeout, **kwargs):
            timeouts.append(timeout)
            return Mock(returncode=0)

        watcher.monitor(duration=310, clock=self.clock.now, sleep=self.clock.sleep, run=run)
        self.assertEqual(timeouts, [240, 10])

    def test_explicit_activation_is_required(self):
        for args, enabled in (([], "true"), (["--send"], "false")):
            with patch("sys.argv", ["monitor_daily_summary.py"] + args), \
                 patch.dict("os.environ", {"DAILY_SUMMARY_ENABLED": enabled}), \
                 patch.object(watcher, "monitor") as monitor:
                with self.assertRaises(RuntimeError):
                    watcher.main()
                monitor.assert_not_called()

    def test_active_monitor_uses_existing_delivery_checks(self):
        with patch("sys.argv", ["monitor_daily_summary.py", "--send"]), \
             patch.dict("os.environ", {"DAILY_SUMMARY_ENABLED": "true"}), \
             patch.object(watcher, "monitor", return_value=0) as monitor:
            self.assertEqual(watcher.main(), 0)
            monitor.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
