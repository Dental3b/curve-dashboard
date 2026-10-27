"""Idempotent daily activity. Persist coded IDs and timestamps, never message text."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from google.cloud import firestore
from logic import contact_key

ZONE = ZoneInfo('America/New_York')
ACTIVITY = 'd3b_whatsapp_daily_activity'
EVENTS = 'd3b_whatsapp_received_events'


def inbound_events(payload, phone_id):
    if payload.get('object') != 'whatsapp_business_account':
        return
    for entry in payload.get('entry', []):
        for change in entry.get('changes', []):
            value = change.get('value') or {}
            if str((value.get('metadata') or {}).get('phone_number_id', '')) != str(phone_id):
                continue
            for msg in value.get('messages') or []:
                sender, event_id = str(msg.get('from') or ''), str(msg.get('id') or '')
                if not sender.isdigit() or not event_id:
                    continue
                try:
                    stamp = datetime.fromtimestamp(int(msg['timestamp']), timezone.utc)
                except (KeyError, ValueError, TypeError, OverflowError, OSError):
                    continue
                yield sender, event_id, stamp


def remember_event(db, sender, event_id, stamp, hash_secret):
    key = contact_key(sender, hash_secret)
    event = db.collection(EVENTS).document(contact_key('event:' + event_id, hash_secret))
    day = stamp.astimezone(ZONE).date().isoformat()
    daily = db.collection(ACTIVITY).document(day + '_' + key)
    tx = db.transaction()

    @firestore.transactional
    def apply(transaction):
        seen = event.get(transaction=transaction)
        if seen.exists:
            return False
        snapshot = daily.get(transaction=transaction)
        data = snapshot.to_dict() or {} if snapshot.exists else {}
        transaction.set(event, {'received_at': stamp, 'contact_key': key})
        transaction.set(daily, {
            'day': day, 'contact_key': key,
            'first_received_at': min(data.get('first_received_at', stamp), stamp),
            'last_received_at': max(data.get('last_received_at', stamp), stamp),
            'message_count': int(data.get('message_count', 0)) + 1,
        })
        return True

    return apply(tx)


def normalize_phone(value):
    digits = ''.join(c for c in str(value) if c.isdigit())
    if len(digits) == 10:
        digits = '1' + digits
    return digits if 11 <= len(digits) <= 15 else ''


def aggregate_contacts(rows, candidates, secret):
    matches = {}
    for patient in candidates:
        for phone in patient['phones']:
            phone = normalize_phone(phone)
            if phone:
                matches.setdefault(contact_key(phone, secret), set()).add(patient['id'])
    grouped = {}
    for row in rows:
        key = row['contact_key']
        if key not in grouped:
            grouped[key] = {
                'key': key, 'patient_ids': sorted(matches.get(key, set())),
                'first_received_at': row['first_received_at'],
                'last_received_at': row['last_received_at'], 'message_count': 0,
            }
        dest = grouped[key]
        dest['first_received_at'] = min(dest['first_received_at'], row['first_received_at'])
        dest['last_received_at'] = max(dest['last_received_at'], row['last_received_at'])
        dest['message_count'] += row['message_count']
    return [{**row, 'first_received_at': row['first_received_at'].isoformat(),
             'last_received_at': row['last_received_at'].isoformat()} for row in grouped.values()]
