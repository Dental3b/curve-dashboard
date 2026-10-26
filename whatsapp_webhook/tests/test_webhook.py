import hashlib
import hmac
from pathlib import Path
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from logic import contact_key, extract_first_messages, verify_signature


class WebhookHelpersTest(unittest.TestCase):
    def test_signature_requires_valid_sha256_header(self):
        body = b'{"object":"whatsapp_business_account"}'
        secret = "test-app-secret"
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature(body, f"sha256={digest}", secret))
        self.assertFalse(verify_signature(body, f"sha1={digest}", secret))
        self.assertFalse(verify_signature(body, None, secret))
        self.assertFalse(verify_signature(body, "sha256=bad", secret))

    def test_contact_key_is_stable_and_keyed(self):
        self.assertEqual(contact_key("15551234567", "salt-a"), contact_key("15551234567", "salt-a"))
        self.assertNotEqual(contact_key("15551234567", "salt-a"), contact_key("15551234567", "salt-b"))
        self.assertNotEqual(contact_key("15551234567", "salt-a"), "15551234567")

    def test_extracts_only_matching_inbound_messages_without_message_text(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "value": {
                        "metadata": {"phone_number_id": "d3b-phone-id"},
                        "messages": [
                            {"from": "15551234567", "timestamp": "1793030400", "text": {"body": "private text"}},
                            {"from": "", "timestamp": "1793030400"},
                            {"from": "15550000000", "timestamp": "bad"},
                        ],
                        "statuses": [{"id": "ignored"}],
                    }
                }, {
                    "value": {
                        "metadata": {"phone_number_id": "other-number"},
                        "messages": [{"from": "15559999999", "timestamp": "1793030400"}],
                    }
                }]
            }]
        }
        events = extract_first_messages(payload, "d3b-phone-id")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "15551234567")
        self.assertIsInstance(events[0][1], datetime)
        self.assertEqual(events[0][1].tzinfo, timezone.utc)

    def test_ignores_non_whatsapp_payloads(self):
        self.assertEqual(extract_first_messages({"object": "page", "entry": []}, "phone"), [])


if __name__ == "__main__":
    unittest.main()
