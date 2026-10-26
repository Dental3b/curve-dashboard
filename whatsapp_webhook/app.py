from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from typing import Any

from flask import Flask, Response, jsonify, request
from google.cloud import firestore

from logic import contact_key, extract_first_messages, verify_signature

app = Flask(__name__)
CONTACTS_COLLECTION = os.environ.get("CONTACTS_COLLECTION", "d3b_whatsapp_first_contacts")


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required configuration: {name}")
    return value


def _remember_earliest_contact(db: Any, sender: str, first_seen: datetime, hash_secret: str) -> None:
    ref = db.collection(CONTACTS_COLLECTION).document(contact_key(sender, hash_secret))
    transaction = db.transaction()

    @firestore.transactional
    def upsert(tx: Any) -> None:
        snapshot = ref.get(transaction=tx)
        if not snapshot.exists:
            tx.set(ref, {"first_seen": first_seen})
            return
        existing = (snapshot.to_dict() or {}).get("first_seen")
        if existing is None or first_seen < existing:
            tx.update(ref, {"first_seen": first_seen})

    upsert(transaction)


def _firestore_client() -> Any:
    return firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None)


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/webhook")
def verify_webhook():
    mode = request.args.get("hub.mode", "")
    supplied = request.args.get("hub.verify_token", "")
    challenge = request.args.get("hub.challenge", "")
    expected = os.environ.get("META_VERIFY_TOKEN", "")
    if mode == "subscribe" and expected and hmac.compare_digest(supplied, expected):
        return Response(challenge, status=200, mimetype="text/plain")
    return Response("Forbidden", status=403, mimetype="text/plain")


@app.post("/webhook")
def receive_webhook():
    body = request.get_data(cache=True)
    try:
        app_secret = _required_env("META_APP_SECRET")
        if not verify_signature(body, request.headers.get("X-Hub-Signature-256"), app_secret):
            return jsonify({"error": "invalid signature"}), 403

        payload = request.get_json(silent=False)
        expected_phone_id = _required_env("META_PHONE_NUMBER_ID")
        hash_secret = _required_env("CONTACT_HASH_KEY")
        db = _firestore_client()
        for sender, received_at in extract_first_messages(payload, expected_phone_id):
            _remember_earliest_contact(db, sender, received_at, hash_secret)
        return jsonify({"ok": True}), 200
    except Exception:
        app.logger.exception("WhatsApp webhook processing failed")
        return jsonify({"error": "webhook processing failed"}), 500


@app.get("/api/summary")
def monthly_summary():
    expected_token = os.environ.get("REPORT_API_TOKEN", "")
    supplied_header = request.headers.get("Authorization", "")
    if not expected_token or not hmac.compare_digest(supplied_header, f"Bearer {expected_token}"):
        return jsonify({"error": "unauthorized"}), 401

    month = request.args.get("month", "")
    try:
        start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError:
        return jsonify({"error": "month must be YYYY-MM"}), 400
    if start.strftime("%Y-%m") != month:
        return jsonify({"error": "month must be YYYY-MM"}), 400

    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)

    db = _firestore_client()
    query = (
        db.collection(CONTACTS_COLLECTION)
        .where("first_seen", ">=", start)
        .where("first_seen", "<", end)
    )
    count = sum(1 for _ in query.stream())
    return jsonify({"month": month, "new_unique_people": count})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
