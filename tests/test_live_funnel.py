import copy
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'whatsapp_webhook'))
from whatsapp_live import build_live_report, fetch_live_contacts, LiveNotReady
from live_capture import inbound_events, aggregate_contacts, remember_event
from logic import contact_key
from test_funnel_endpoint import webhook

NOW = datetime.fromisoformat('2026-10-28T18:00:00-04:00')


def contact(key='a', ids=None, first='2026-10-27T09:00:00-04:00', count=2):
    return {'key': key, 'patient_ids': ids or [], 'first_received_at': first,
            'last_received_at': first, 'message_count': count}


def activity(contacts):
    return {'start':'2026-10-27', 'end':'2026-10-28', 'capture_since':'2026-10-27T00:00:00-04:00',
            'as_of':NOW.isoformat(), 'contacts':contacts}


def appt(pid='1', when='2026-10-28T10:00:00', status='check out', aid='10'):
    return {'id':aid, 'patientId':pid, 'startTime':when, 'appointmentStatus':status}


def report(contacts, appts=None, ledger=None, items=None):
    return build_live_report(activity(contacts), appts or [], ledger or [], items or [], now=NOW)['counts']


class FunnelTest(unittest.TestCase):
    def test_recent_single_message_is_not_prematurely_lost(self):
        c=contact(first='2026-10-28T17:00:00-04:00', count=1)
        r=report([c]);self.assertEqual(r['pending_classification'],1);self.assertEqual(r['initial_no_response'],0)

    def test_single_message_after_24_hours(self):
        r=report([contact(count=1)]);self.assertEqual(r['initial_no_response'],1)

    def test_old_single_message_is_a_followup_not_initial(self):
        c=contact(count=1);c['first_observed_at']='2026-09-01T10:00:00-04:00'
        self.assertEqual(report([c])['conversation_no_booking'],1)

    def test_previous_visit_outside_selected_month_is_existing(self):
        self.assertEqual(report([contact(ids=['1'])],[appt(when='2026-08-01T09:00:00')])['existing_patients'],1)

    def test_shared_family_number_is_not_arbitrarily_assigned(self):
        self.assertEqual(report([contact(ids=['1','2'])])['pending_classification'],1)

    def test_two_numbers_for_one_patient_are_deduplicated(self):
        self.assertEqual(report([contact('a',['1']),contact('b',['1'])])['contacts'],1)

    def test_future_visit_has_priority_over_previous_cancellation(self):
        appts=[appt(status='cancel'),appt(aid='11',when='2026-11-02T10:00:00',status='active')]
        r=report([contact(ids=['1'])],appts);self.assertEqual(r['pending_visit'],1);self.assertEqual(r['no_show_or_cancelled'],0)

    def test_missed_and_cancelled_are_one_outcome(self):
        r=report([contact(ids=['1'])],[appt(status='missed'),appt(aid='11',status='cancel')])
        self.assertEqual(r['booked'],1);self.assertEqual(r['no_show_or_cancelled'],1)

    def test_checked_in_is_pending_not_arrived(self):
        r=report([contact(ids=['1'])],[appt(status='check in')]);self.assertEqual(r['arrived'],0);self.assertEqual(r['other_booking_outcome'],1)

    def test_49_collected_is_consultation_only(self):
        ledger=[{'patientId':'1','calculations':{'payments':'49'}}]
        self.assertEqual(report([contact(ids=['1'])],[appt()],ledger)['paid_only_49'],1)

    def test_deposit_above_49_does_not_prove_treatment(self):
        ledger=[{'patientId':'1','calculations':{'deposits':'800'}}]
        r=report([contact(ids=['1'])],[appt()],ledger)
        self.assertEqual(r['paid_over_49_with_treatment'],0);self.assertEqual(r['other_payment_outcome'],1)

    def test_real_procedure_and_cash_not_credit_transfer(self):
        items=[{'id':'12','appointment':{'id':'10'},'procedureCode':'D7140','status':'Active'}]
        ledger=[{'patientId':'1','calculations':{'deposits':'800'}},
                {'patientId':'1','calculations':{'production':'800'},'appointmentItem':{'id':'12'}},
                {'patientId':'1','calculations':{'paymentsFromAccountCredit':'800','totalPayments':'800'}}]
        self.assertEqual(report([contact(ids=['1'])],[appt()],ledger,items)['paid_over_49_with_treatment'],1)
        ledger[0]['calculations']={'paymentsFromAccountCredit':'800'}
        self.assertEqual(report([contact(ids=['1'])],[appt()],ledger,items)['paid_over_49_with_treatment'],0)

    def test_planned_clinical_item_without_production_stays_pending(self):
        items=[{'id':'12','appointment':{'id':'10'},'procedureCode':'D7140','status':'Active'}]
        ledger=[{'patientId':'1','calculations':{'payments':'200'}}]
        self.assertEqual(report([contact(ids=['1'])],[appt()],ledger,items)['other_payment_outcome'],1)

    def test_diagnostic_code_does_not_count_as_treatment(self):
        items=[{'id':'12','appointment':{'id':'10'},'procedureCode':'D0150','status':'Active'}]
        ledger=[{'patientId':'1','appointmentItem':{'id':'12'},'calculations':{'payments':'150','production':'150'}}]
        self.assertEqual(report([contact(ids=['1'])],[appt()],ledger,items)['paid_over_49_with_treatment'],0)


class CaptureTest(unittest.TestCase):
    def test_matching_inbound_only_and_no_text_leaks(self):
        payload={'object':'whatsapp_business_account','entry':[{'changes':[{'value':{
            'metadata':{'phone_number_id':'9'},'messages':[{'id':'message','from':'17700000000','timestamp':'1793131200','text':{'body':'private'}}]}}]}]}
        events=list(inbound_events(payload,'9'));self.assertEqual(len(events),1);self.assertNotIn('private',str(events))
        self.assertEqual(list(inbound_events(payload,'8')),[])

    def test_aggregate_across_local_dates_and_normalize_us_phone(self):
        key=contact_key('17700000000','test')
        rows=[{'contact_key':key,'first_received_at':NOW,'last_received_at':NOW,'message_count':2},
              {'contact_key':key,'first_received_at':NOW-timedelta(days=1),'last_received_at':NOW,'message_count':1}]
        result=aggregate_contacts(rows,[{'id':'1','phones':['(770) 000-0000']},{'id':'2','phones':['+1 7700000000']}],'test')
        self.assertEqual(len(result),1);self.assertEqual(result[0]['message_count'],3)
        self.assertEqual(result[0]['patient_ids'],['1','2']);self.assertNotIn('7700000000',str(result))

    def test_duplicate_event_is_noop(self):
        db=Mock();db.collection.return_value.document.return_value.get.return_value.exists=True
        with patch('live_capture.firestore.transactional',side_effect=lambda f:f):
            self.assertFalse(remember_event(db,'17700000000','message',NOW,'test'))
        db.transaction.return_value.set.assert_not_called()

    def test_local_day_and_out_of_order_events(self):
        db=Mock();event=Mock();daily=Mock();db.collection.side_effect=lambda name: Mock(document=Mock(return_value=event if 'received_events' in name else daily))
        event.get.return_value.exists=False;daily.get.return_value.exists=True
        later=NOW+timedelta(hours=1)
        daily.get.return_value.to_dict.return_value={'first_received_at':later,'last_received_at':later,'message_count':1}
        with patch('live_capture.firestore.transactional',side_effect=lambda f:f):
            self.assertTrue(remember_event(db,'17700000000','message',NOW,'test'))
        written=db.transaction.return_value.set.call_args_list[-1].args[1]
        self.assertEqual(written['day'],'2026-10-28');self.assertEqual(written['first_received_at'],NOW)
        self.assertEqual(written['last_received_at'],later);self.assertEqual(written['message_count'],2)


class LiveEndpointTest(unittest.TestCase):
    def test_auth_and_validation_precede_queries(self):
        with patch.dict('os.environ',{'REPORT_API_TOKEN':'test'}),patch.object(webhook,'_firestore_client') as db:
            client=webhook.app.test_client()
            self.assertEqual(client.post('/api/live-contacts',json={}).status_code,401)
            self.assertEqual(client.post('/api/live-contacts',json={'start':'bad'},headers={'Authorization':'Bearer test'}).status_code,400)
            db.assert_not_called()

    def test_unknown_fields_cannot_smuggle_message_text(self):
        with patch.dict('os.environ',{'REPORT_API_TOKEN':'test'}):
            r=webhook.app.test_client().post('/api/live-contacts',headers={'Authorization':'Bearer test'},
                json={'start':'2026-10-27','end':'2026-10-28','patients':[{'id':'1','phones':[],'message':'private'}]})
            self.assertEqual(r.status_code,400);self.assertNotIn('private',r.text)

    def test_live_endpoint_returns_daily_activity_and_lifetime_first_seen(self):
        from live_capture import ACTIVITY
        from logic import contact_key
        key=contact_key('17700000000','hash-test')
        db=Mock(); query=db.collection.return_value.where.return_value.where.return_value.limit.return_value
        row=Mock();row.to_dict.return_value={'contact_key':key,'first_received_at':NOW,
            'last_received_at':NOW,'message_count':1}
        query.stream.return_value=[row]
        earliest=Mock();earliest.id=key;earliest.to_dict.return_value={'first_seen':NOW-timedelta(days=30)}
        db.get_all.return_value=[earliest]
        env={'REPORT_API_TOKEN':'test','CONTACT_HASH_KEY':'hash-test','LIVE_CAPTURE_SINCE':'2026-10-27T00:00:00-04:00'}
        with patch.dict('os.environ',env),patch.object(webhook,'_firestore_client',return_value=db):
            r=webhook.app.test_client().post('/api/live-contacts',headers={'Authorization':'Bearer test'},
                json={'start':'2026-10-27','end':'2026-10-28','patients':[{'id':'1','phones':['7700000000']}]})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json['contacts'][0]['patient_ids'],['1'])
        self.assertEqual(r.json['contacts'][0]['message_count'],1)
        self.assertNotIn('7700000000',r.text)
        self.assertEqual(r.json['contacts'][0]['first_observed_at'],(NOW-timedelta(days=30)).isoformat())

    def test_missing_capture_boundary_is_unavailable_not_empty(self):
        with patch.dict('os.environ',{'REPORT_API_TOKEN':'test','LIVE_CAPTURE_SINCE':''}),patch.object(webhook,'_firestore_client') as db:
            r=webhook.app.test_client().post('/api/live-contacts',headers={'Authorization':'Bearer test'},
                json={'start':'2026-10-27','end':'2026-10-28','patients':[]})
        self.assertEqual(r.status_code,503);db.assert_not_called()

    def test_predeployment_response_is_not_zero(self):
        session=Mock();session.post.return_value.status_code=404
        with self.assertRaises(LiveNotReady):
            fetch_live_contacts('https://example.com','test','2026-10-27','2026-10-28',[],session=session)


if __name__=='__main__':unittest.main()
