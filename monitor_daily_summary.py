#!/usr/bin/env python3
"""Keep checking the daily close within one bounded GitHub Actions execution."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time


POLL_SECONDS = 5 * 60
MONITOR_SECONDS = 330 * 60
CHECK_TIMEOUT_SECONDS = 4 * 60


def monitor(*, duration=MONITOR_SECONDS, interval=POLL_SECONDS,
            clock=time.monotonic, sleep=time.sleep, run=subprocess.run):
    if duration <= 0 or interval <= 0:
        raise ValueError("Monitor duration and interval must be positive")
    deadline = clock() + duration
    command = [sys.executable, "-u", str(Path(__file__).with_name("daily_summary.py")), "--send"]
    failures = 0
    checks = 0
    print("Daily-close monitor started; checks repeat within this job every 5 minutes.", flush=True)
    while clock() < deadline:
        started = clock()
        remaining = deadline - started
        if remaining <= 0:
            break
        try:
            result = run(command, timeout=min(CHECK_TIMEOUT_SECONDS, remaining), check=False)
            failed = result.returncode != 0
        except subprocess.TimeoutExpired:
            # daily_summary reserves delivery durably before POST; never reset that
            # state or retry WhatsApp directly after an interrupted subprocess.
            failed = True
            print("Close check timed out; durable delivery state will be rechecked.", flush=True)
        failures += int(failed)
        checks += 1
        print(f"Close check {checks}: {'failed; inspect the sanitized error above' if failed else 'completed'}.", flush=True)
        # Measure start-to-start, so API request time does not add five more minutes.
        delay = min(started + interval, deadline) - clock()
        if delay > 0:
            sleep(delay)
    print(f"Monitor window finished: {checks} checks, {failures} failures.", flush=True)
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Monitor and send when the close is ready")
    args = parser.parse_args()
    if not args.send or os.environ.get("DAILY_SUMMARY_ENABLED") != "true":
        raise RuntimeError("Monitoring requires --send and DAILY_SUMMARY_ENABLED=true")
    return monitor()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Daily-close monitor failed: {error if isinstance(error, RuntimeError) else type(error).__name__}", file=sys.stderr)
        sys.exit(1)
