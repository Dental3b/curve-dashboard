import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from whatsapp_webhook.funnel_report import COUNT_FIELDS, report_rows, validate_report
from whatsapp_funnel import fetch_funnel_report


def example_report():
    return {
        "schema_version": 1, "start": "2026-10-01", "end": "2026-10-26",
        "as_of": "2026-10-26T19:00:00-04:00", "timezone": "America/New_York", "quality": "minimum_verified",
        "counts": dict(zip(COUNT_FIELDS, (100, 10, 20, 20, 50, 25, 15, 10, 0, 15, 10, 0))),
    }


class ReportTest(unittest.TestCase):
    def test_agreed_categories_reconcile(self):
        report = example_report()
        self.assertEqual(validate_report(report), report)
        self.assertEqual(report_rows(report)[0]["% of contacts"], "100.0%")

    def test_combined_no_show_and_cancel_row(self):
        rows = report_rows(example_report())
        labels = [r["Metric"] for r in rows]
        self.assertEqual(labels.count("No-shows / cancellations"), 1)
        self.assertEqual(next(r["People"] for r in rows if r["Metric"] == "No-shows / cancellations"), 10)

    def test_unknown_is_not_zero(self):
        r = example_report()
        r["counts"]["paid_over_49_with_treatment"] = None
        row = next(x for x in report_rows(r) if "received treatment" in x["Metric"])
        self.assertEqual((row["People"], row["% of contacts"]), ("Pending verification", "—"))

    def test_empty_report_has_no_misleading_conversion(self):
        r = example_report()
        r["counts"] = dict.fromkeys(COUNT_FIELDS, 0)
        self.assertTrue(all(x["% of contacts"] == "—" for x in report_rows(r)))

    def test_rejects_nonexclusive_contact_counts(self):
        r = example_report()
        r["counts"]["existing_patients"] += 1
        with self.assertRaises(ValueError): validate_report(r)

    def test_rejects_statuses_not_matching_bookings(self):
        r = example_report()
        r["counts"]["no_show_or_cancelled"] += 1
        with self.assertRaises(ValueError): validate_report(r)

    def test_rejects_double_counted_payment_buckets(self):
        r = example_report()
        r["counts"]["paid_only_49"] = 25
        with self.assertRaises(ValueError): validate_report(r)

    def test_rejects_patient_data_in_aggregate(self):
        r = example_report()
        r["patient_name"] = "Test Person"
        with self.assertRaises(ValueError): validate_report(r)
        r = example_report()
        r["counts"]["phone"] = "15551234567"
        with self.assertRaises(ValueError): validate_report(r)

    def test_rejects_invalid_count_types(self):
        for value in (True, -1, 1.5, "20"):
            r = example_report()
            r["counts"]["initial_no_response"] = value
            with self.subTest(value=value), self.assertRaises(ValueError): validate_report(r)

    def test_invalid_period_or_unzoned_timestamp_rejected(self):
        for key, value in (("start", "2026-1-1"), ("end", "2026-09-01"), ("as_of", "2026-10-26T19:00:00")):
            r = example_report()
            r[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): validate_report(r)


class FakeResponse:
    def __init__(self, status, payload): self.status_code, self.payload = status, payload
    def json(self): return self.payload


class FakeSession:
    def __init__(self, response): self.response, self.calls = response, []
    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class ClientTest(unittest.TestCase):
    def test_authenticated_request_matches_period_and_does_not_follow_redirects(self):
        session = FakeSession(FakeResponse(200, example_report()))
        r = fetch_funnel_report("https://example.test", "test-secret", "2026-10-01", "2026-10-26", session)
        self.assertEqual(r["counts"]["contacts"], 100)
        args, kwargs = session.calls[0]
        self.assertEqual(args[0], "https://example.test/api/funnel")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer test-secret"})
        self.assertFalse(kwargs["allow_redirects"])

    def test_missing_report_is_not_empty_report(self):
        self.assertIsNone(fetch_funnel_report("https://example.test", "test", "2026-10-01", "2026-10-26", FakeSession(FakeResponse(404, {}))))

    def test_errors_do_not_disclose_token(self):
        with self.assertRaises(RuntimeError) as caught:
            fetch_funnel_report("https://example.test", "private-token", "2026-10-01", "2026-10-26", FakeSession(FakeResponse(401, {})))
        self.assertNotIn("private-token", str(caught.exception))

    def test_other_period_is_rejected(self):
        with self.assertRaises(ValueError):
            fetch_funnel_report("https://example.test", "test", "2026-10-26", "2026-10-26", FakeSession(FakeResponse(200, example_report())))

    def test_insecure_or_credential_url_rejected(self):
        for url in ("http://example.test", "https://user:pass@example.test", "https://example.test?token=x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch_funnel_report(url, "test", "2026-10-01", "2026-10-26", FakeSession(None))


if __name__ == "__main__": unittest.main()
