# Live WhatsApp funnel

## Operation

The signed Meta webhook receives inbound activity in the cloud even when the dashboard is closed. Each HMAC-coded message ID is recorded once in a Firestore transaction. Daily per-contact counts use America/New_York including daylight saving time. Text, names, media and clear-text phones are not stored in these collections.

The authenticated live endpoint processes a minimal Curve phone registry in memory, matches coded contacts and returns activity. It does not store the submitted registry. Shared family phones remain ambiguous rather than being assigned to an arbitrary patient.

Streamlit refreshes the live section every 60 seconds while open and joins activity with current Curve appointments, payments/deposits and billed clinical items. Today's records refresh each minute; older records are rechecked hourly. The first load can take longer while the historical Curve baseline is read. New and changed patient records are fetched incrementally using changedSince. Opening the app triggers the current join; no daily manual report command is needed.

## Classification

People are unique within the selected contact period. Two numbers matched unambiguously to one Curve patient are merged. Existing patients had a completed visit before their first inbound message in that period. Unmatched phones are not proof of lost leads.

Recent first single messages remain In progress / match pending for 24 hours. After that, a first observed single message is Initial message, no further reply. Multiple messages or known follow-ups without a linked appointment are Chatted, not booked. Shared phone matches remain pending.

Among bookings, completed visits take priority, then active future bookings, then cancelled/missed appointments together, then unresolved appointment statuses. Check-in alone is not attendance. Appointment linkage does not prove that WhatsApp caused the booking.

Consultation-only requires exactly $49 of fresh collections and no billed clinical procedure. Treatment requires collections above $49, a completed appointment, and a clinical CDT item with positive ledger production. Diagnostic D0 codes, mere planned items, credit transfers and account-credit applications cannot satisfy this outcome. Other financial outcomes remain pending. Outcomes evolve beyond the contact period, as of the current date.

## One-time deployment

Run from the repository in an authenticated Cloud Shell:

```bash
bash scripts/deploy_live_whatsapp.sh YOUR_PROJECT_ID
```

The script preserves the existing Cloud Run identity, secret references and IAM. It sets LIVE_CAPTURE_SINCE on the first deployment and preserves it thereafter. The verification posts an empty patient registry to the authenticated endpoint and reports only aggregate counts; it does not send messages or create fake activity.

After deployment, a real inbound message must verify the webhook-to-dashboard path before calling the feature operational. Unit tests cannot verify external Meta delivery.

## Coverage

Daily activity starts with this receiver deployment. The first day is partial. Earlier first_seen records cannot reconstruct daily activity or message counts. Historical verified aggregates remain separate under Verified historical report with their exact dates. They are not added to live totals, because they lack the identifiers required for deduplication. Earlier history needs a separately verified, identified backfill.

No unrelated PDR automation, Meta asset assignment or outgoing messaging configuration is changed. No cron service is required to receive WhatsApp activity.
