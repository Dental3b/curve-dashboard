#!/usr/bin/env python3
"""Send the explicitly requested, clearly labeled example once per test record."""
import os
import re
import sys
from datetime import date, datetime

from daily_summary import ApiError, EASTERN, GitHubState, MetaSender, get_summary, request_json


class DeliveryTestState(GitHubState):
    def __init__(self, day):
        super().__init__(day)
        # Separate from all dated production records; reruns share this same record.
        self.path = "/contents/daily-summary-state/delivery-test-v1.json"


class TestMessage:
    def __init__(self, summary):
        self.values = summary.parameters()
        self.values[0] = "PRUEBA - " + self.values[0]

    def parameters(self):
        return list(self.values)


def send_example(summary, state, sender):
    record = state.read()
    if record.get("status") == "accepted":
        print("Example already accepted by Meta; no repeat sent.")
        return
    if record.get("status") == "sending":
        raise RuntimeError("Previous example send is unconfirmed; inspect receipt before retrying")
    state.write({"status": "sending", "started_at": datetime.now(EASTERN).isoformat()})
    try:
        sender.send(TestMessage(summary))
    except ApiError as error:
        if 400 <= error.status < 500 and error.status != 408:
            state.write({"status": "waiting"})
        raise
    # Message IDs can encode a recipient. Do not publish them in a public repository.
    state.write({"status": "accepted", "accepted_at": datetime.now(EASTERN).isoformat()})
    print("EXAMPLE ACCEPTED BY META. Receipt still requires delivery confirmation.")


def main():
    if os.environ.get("ALLOW_DELIVERY_TEST") != "true":
        raise RuntimeError("Explicit delivery-test activation is required")
    sender = MetaSender.from_environment()
    if not re.fullmatch(r"es(?:_[A-Z]{2})?", sender.language):
        raise RuntimeError("WHATSAPP_TEMPLATE_LANGUAGE must contain only the approved language code")
    info = request_json("Meta sender verification", "GET",
        f"https://graph.facebook.com/{sender.version}/{sender.phone_id}?fields=id,display_phone_number",
        headers={"Authorization": "Bearer " + sender.token})
    sender_number = re.sub(r"\D", "", str(info.get("display_phone_number", "")))
    if str(info.get("id", "")) != sender.phone_id or not sender_number:
        raise RuntimeError("Meta sender could not be verified")
    if sender_number == sender.recipient:
        raise RuntimeError("The WhatsApp sender and recipient must be different")
    summary = get_summary(date(2026, 10, 23))
    send_example(summary, DeliveryTestState(summary.day), sender)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else type(error).__name__, file=sys.stderr)
        sys.exit(1)
