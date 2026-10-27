from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any


def verify_signature(body: bytes, signature: str | None, app_secret: str) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    supplied = signature.removeprefix("sha256=").strip()
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied, expected)


def contact_key(wa_id: str, hash_secret: str) -> str:
    """Create a stable, keyed pseudonymous ID without persisting the phone number."""
    return hmac.new(hash_secret.encode("utf-8"), wa_id.encode("utf-8"), hashlib.sha256).hexdigest()


def extract_first_messages(payload: dict[str, Any], expected_phone_id: str) -> list[tuple[str, datetime]]:
    """Return only sender IDs and event timestamps for inbound messages on this phone."""
    events: list[tuple[str, datetime]] = []
    if payload.get("object") != "whatsapp_business_account":
        return events

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            if str(metadata.get("phone_number_id") or "") != str(expected_phone_id):
                continue
            for message in value.get("messages", []):
                sender = str(message.get("from") or "").strip()
                timestamp = message.get("timestamp")
                if not sender or timestamp is None:
                    continue
                try:
                    sent_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
                except (TypeError, ValueError, OverflowError):
                    continue
                events.append((sender, sent_at))
    return events
