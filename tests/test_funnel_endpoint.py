import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "whatsapp_webhook"))
from test_funnel import example_report

spec = importlib.util.spec_from_file_location("funnel_webhook_app", ROOT / "whatsapp_webhook" / "app.py")
webhook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(webhook)


class EndpointTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"REPORT_API_TOKEN": "test-only"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = webhook.app.test_client()
        self.headers = {"Authorization": "Bearer test-only"}

    def request(self, query="start=2026-10-01&end=2026-10-26"):
        return self.client.get("/api/funnel?" + query, headers=self.headers)

    def test_authentication_precedes_database_access(self):
        with patch.object(webhook, "_firestore_client") as db:
            self.assertEqual(self.client.get("/api/funnel").status_code, 401)
            db.assert_not_called()

    def test_invalid_dates_do_not_query_database(self):
        with patch.object(webhook, "_firestore_client") as db:
            self.assertEqual(self.request("start=2026-1-1&end=2026-10-26").status_code, 400)
            self.assertEqual(self.request("start=2026-10-27&end=2026-10-26").status_code, 400)
            db.assert_not_called()

    def test_missing_snapshot_returns_404_instead_of_zero(self):
        db = Mock()
        db.collection.return_value.document.return_value.get.return_value.exists = False
        with patch.object(webhook, "_firestore_client", return_value=db):
            response = self.request()
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("counts", response.json)

    def test_returns_exact_verified_snapshot(self):
        db = Mock()
        snapshot = db.collection.return_value.document.return_value.get.return_value
        snapshot.exists = True
        snapshot.to_dict.return_value = example_report()
        with patch.object(webhook, "_firestore_client", return_value=db): response = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, example_report())
        db.collection.return_value.document.assert_called_once_with("2026-10-01_2026-10-26")

    def test_inconsistent_snapshot_is_not_exposed(self):
        db = Mock()
        snapshot = db.collection.return_value.document.return_value.get.return_value
        snapshot.exists = True
        report = example_report()
        report["patient_name"] = "Must not escape"
        snapshot.to_dict.return_value = report
        with patch.object(webhook, "_firestore_client", return_value=db): response = self.request()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("Must not escape", response.text)

    def test_privacy_and_deletion_pages_preserved(self):
        self.assertEqual(self.client.get("/privacy").status_code, 200)
        self.assertEqual(self.client.get("/data-deletion").status_code, 200)


if __name__ == "__main__": unittest.main()
