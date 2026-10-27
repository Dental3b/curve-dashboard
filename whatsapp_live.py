"""Build a live funnel from coded WhatsApp events and current Curve records."""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from whatsapp_webhook.funnel_report import COUNT_FIELDS, validate_report

ZONE = ZoneInfo('America/New_York')


class LiveNotReady(RuntimeError):
    pass


def stamp(value):
    value = str(value or '')
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=ZONE) if parsed.tzinfo is None else parsed.astimezone(ZONE)


def patient_candidates(patients):
    result = []
    for patient in patients:
        phones = []
        for field in ('cellPhone', 'homePhone', 'workPhone'):
            value = patient.get(field)
            if isinstance(value, dict):
                value = value.get('number')
            if value:
                phones.append(str(value))
        result.append({'id': str(patient['id']), 'phones': phones})
    return result


def fetch_live_contacts(base_url, token, start, end, patients, session=None):
    url = urlsplit(base_url)
    if url.scheme != 'https' or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError('Invalid service URL')
    if not token:
        raise ValueError('Missing credential')
    if session is None:
        import requests
        session = requests
    response = session.post(base_url.rstrip('/') + '/api/live-contacts',
        headers={'Authorization': 'Bearer ' + token},
        json={'start': start, 'end': end, 'patients': patient_candidates(patients)},
        timeout=30, allow_redirects=False)
    if response.status_code in (404, 503):
        raise LiveNotReady('Live capture is awaiting deployment or is temporarily unavailable')
    if response.status_code != 200:
        raise RuntimeError('Live activity unavailable')
    data = response.json()
    if data.get('start') != start or data.get('end') != end or not isinstance(data.get('contacts'), list):
        raise ValueError('Unexpected activity response')
    if stamp(data.get('capture_since')) is None or stamp(data.get('as_of')) is None:
        raise ValueError('Missing capture dates')
    return data


def build_live_report(activity, appointments, ledger, items, now=None):
    now = now or datetime.now(ZONE)
    appts_by_patient = defaultdict(list)
    appts_by_id = {}
    for appt in appointments:
        pid = str(appt.get('patientId') or '')
        if pid:
            appts_by_patient[pid].append(appt)
            appts_by_id[str(appt['id'])] = appt
    paid = defaultdict(lambda: Decimal('0'))
    performed_item_ids = set()
    for entry in ledger:
        pid = str(entry.get('patientId') or '')
        calc = entry.get('calculations') or {}
        reference = entry.get('appointmentItem') or {}
        item_id = str(reference.get('id') or '') if isinstance(reference, dict) else str(reference)
        if item_id and Decimal(str(calc.get('production') or 0)) > 0:
            performed_item_ids.add(item_id)
        # Account-credit applications and internal transfers are not fresh collections.
        paid[pid] += sum((Decimal(str(calc.get(k) or 0)) for k in ('payments', 'deposits')), Decimal(0))
        paid[pid] -= sum((Decimal(str(calc.get(k) or 0)) for k in ('paymentRefunds', 'creditRefunds')), Decimal(0))
    treatment_dates = defaultdict(list)
    for item in items:
        reference = item.get('appointment') or {}
        aid = str(reference.get('id') or '') if isinstance(reference, dict) else str(reference)
        appt = appts_by_id.get(aid)
        code = str(item.get('procedureCode') or '').upper()
        # The first-visit diagnostic bundle is not treatment. Require a completed clinical code.
        if not appt or not code.startswith('D') or len(code) < 2 or code[1] not in '123456789':
            continue
        if str(appt.get('appointmentStatus', '')).lower() != 'check out':
            continue
        when = stamp(appt.get('startTime'))
        if when and when <= now and str(item.get('id') or '') in performed_item_ids:
            treatment_dates[str(appt['patientId'])].append(when)
    # A patient can use two numbers. Merge an unambiguous Curve identity, never shared family phones.
    people = {}
    for contact in activity['contacts']:
        ids = contact['patient_ids']
        identity = 'patient:' + ids[0] if len(ids) == 1 else 'contact:' + contact['key']
        row = dict(contact)
        row['first_received_at'] = stamp(row['first_received_at'])
        row['last_received_at'] = stamp(row['last_received_at'])
        if identity in people:
            other = people[identity]
            other['first_received_at'] = min(other['first_received_at'], row['first_received_at'])
            other['last_received_at'] = max(other['last_received_at'], row['last_received_at'])
            other['message_count'] += row['message_count']
        else:
            people[identity] = row
    counts = {key: 0 for key in COUNT_FIELDS}
    counts['pending_classification'] = 0
    counts['contacts'] = len(people)
    for contact in people.values():
        first = contact['first_received_at']
        ids = contact['patient_ids']
        if len(ids) > 1:
            counts['pending_classification'] += 1
            continue
        pid = ids[0] if ids else None
        appts = appts_by_patient.get(pid, [])
        completed = [a for a in appts if str(a.get('appointmentStatus', '')).lower() == 'check out'
                     and stamp(a.get('startTime')) and stamp(a['startTime']) <= now]
        if any(stamp(a['startTime']) < first for a in completed):
            counts['existing_patients'] += 1
            continue
        bookings = [a for a in appts if stamp(a.get('startTime')) and stamp(a['startTime']) >= first]
        if not bookings:
            is_initial = stamp(contact.get('first_observed_at') or contact['first_received_at']) >= first
            if contact['message_count'] == 1 and is_initial and now - contact['last_received_at'] < timedelta(hours=24):
                counts['pending_classification'] += 1
            elif contact['message_count'] == 1 and is_initial:
                counts['initial_no_response'] += 1
            else:
                counts['conversation_no_booking'] += 1
            continue
        counts['booked'] += 1
        arrived = [a for a in completed if stamp(a['startTime']) >= first]
        if arrived:
            counts['arrived'] += 1
            visit = min(stamp(a['startTime']) for a in arrived)
            if paid[pid] == Decimal('49') and not any(d >= visit for d in treatment_dates[pid]):
                counts['paid_only_49'] += 1
            elif paid[pid] > Decimal('49') and any(d >= visit for d in treatment_dates[pid]):
                counts['paid_over_49_with_treatment'] += 1
            else:
                counts['other_payment_outcome'] += 1
        elif any(stamp(a['startTime']) > now and str(a.get('appointmentStatus', '')).lower() in {'active', 'check in'} for a in bookings):
            counts['pending_visit'] += 1
        elif all(str(a.get('appointmentStatus', '')).lower() in {'missed', 'cancel', 'cancelled', 'canceled'} for a in bookings):
            counts['no_show_or_cancelled'] += 1
        else:
            counts['other_booking_outcome'] += 1
    report = {'schema_version': 2, 'start': activity['start'], 'end': activity['end'],
              'as_of': now.isoformat(), 'timezone': 'America/New_York',
              'quality': 'minimum_verified', 'counts': counts}
    return validate_report(report)
