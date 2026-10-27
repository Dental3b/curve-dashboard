# Verified WhatsApp funnel

The dashboard shows the agreed funnel as a table of people and percentages of contacts. Existing patients are classified at the time of their first observed message. They are excluded from the new-patient booking and attendance stages. A chat containing only outbound reminders is not evidence of patient acquisition through WhatsApp.

No-shows and cancellations share one row: **No-shows / cancellations**. Attendance, future visits and that combined outcome are exclusive person-level groups. A person who attended is not also counted as a no-show because of a later appointment. Remaining unresolved booking or payment outcomes have explicit rows.

## Configuration

Set these private Streamlit secrets, or environment variables:

- `WHATSAPP_FUNNEL_URL`: HTTPS base URL of the deployed webhook service, without `/api/funnel`.
- `WHATSAPP_FUNNEL_REPORT_TOKEN`: the same private value used by the service's `REPORT_API_TOKEN`.

The existing Curve credential configuration remains unchanged. Do not commit reports, tokens, patient names or phone numbers to the repository.

Deploy `whatsapp_webhook` as the Cloud Run build source. Its Dockerfile includes the report schema module as well as the existing signature and parsing helpers. The privacy, deletion, webhook, diagnostic and legacy monthly counter endpoints are preserved.

## Verified reports

`GET /api/funnel?start=YYYY-MM-DD&end=YYYY-MM-DD` requires a bearer credential. It returns the exact approved aggregate snapshot for those dates from Firestore. It does not add daily unique counts to create a monthly count, extrapolate missing history or calculate acquisition from the old first-contact counter.

The collection defaults to `d3b_whatsapp_funnel_reports` and may be overridden with `FUNNEL_COLLECTION`. The document ID is `START_END`. Each snapshot contains:

- `schema_version`: integer `1`.
- `start`, `end`: inclusive clinic dates.
- `as_of`: the actual data verification timestamp, with UTC offset.
- `timezone`: `America/New_York`.
- `quality`: `minimum_verified` or `complete`.
- `counts`: exactly the fields defined in `whatsapp_webhook/funnel_report.py`. Missing/unverified outcomes are `null`, not zero.

Validate and publish an aggregate JSON report with the authenticated operator's Google Cloud identity:

```sh
python3 scripts/publish_funnel_report.py REPORT.json --project YOUR_PROJECT --dry-run
python3 scripts/publish_funnel_report.py REPORT.json --project YOUR_PROJECT
```

The publishing helper uses the standard library and `gcloud`; it requires no service-account key file. Firestore permissions must be supplied through the operator's existing account. Report validation rejects personal-data fields and inconsistent category totals before any write.

## Reporting semantics

The first partition is `existing_patients + initial_no_response + conversation_no_booking + booked = contacts`. Attendance and payment outcomes are nested under those bookings, not added to the contact partition. Payment outcomes appear as indented children immediately below first-appointment attendance and use `arrived` as their denominator. Other rows use `contacts`; each displayed share names its base. `paid_over_49_with_treatment` requires received funds above $49 and evidence of treatment, rather than an invoice alone. Deposits must be included once and credit transfers must not be counted as fresh collections. Formal treatment acceptance is not inferred from a future appointment alone.

This change displays **verified snapshots**, not an automated acquisition pipeline. The deployed first-contact counter cannot reconstruct daily repeat activity, no-response status or a complete historic cohort. A separate capture and reconciliation job must be implemented before these figures can update automatically. Missing snapshots display a pending-data message. They never display invented zeros or the illustrative example as clinic data.

## Checks

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s whatsapp_webhook/tests -v
```
