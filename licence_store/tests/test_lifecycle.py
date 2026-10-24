import time, unittest
from unittest.mock import patch
from sqlalchemy import select
import test_licence_store as store_tests
import test_licence_accounts as account_tests
from licence_store.models import Cancellation, SupportTicket
from licence_store.lifecycle import request_cancellation,process_cancellations
from mps.core import db,put

class CancellationTests(unittest.TestCase):
    setUp=store_tests.StoreTests.setUp
    tearDown=store_tests.StoreTests.tearDown
    pay=store_tests.StoreTests.pay
    code=store_tests.StoreTests.code
    lease=store_tests.StoreTests.lease
    def prepare(self,age=1):
        self.pay();self.calls=[];self.refunds=[]
        self.invoice.update(payment_intent='pi_test',status_transitions={'paid_at':time.time()-age})
        self.remote.data=self.remote.objects if hasattr(self.remote,'objects') else None
        original=self.remote.get
        self.remote.get=lambda path: {'data':self.refunds,'has_more':False} if path.startswith('/refunds?') else original(path)
        def post(path,data,key):
            self.calls.append((path,data,key))
            if path.startswith('/subscriptions/'):
                self.sub['cancel_at_period_end']=True;return self.sub
            refund={'id':'re_test','status':'succeeded','amount':500,'metadata':{'mps_cancellation':self.order.id}}
            self.refunds.append(refund);return refund
        self.remote.post=post
    def test_eligible_cancel_refund_once_and_access_until_end(self):
        self.prepare();request_cancellation(self.db,self.remote,self.order)
        process_cancellations(self.db,self.remote);request_cancellation(self.db,self.remote,self.order);process_cancellations(self.db,self.remote)
        row=self.db.get(Cancellation,self.order.id)
        self.assertEqual(row.cancel_status,'scheduled');self.assertEqual(row.refund_status,'succeeded')
        self.assertEqual(len([x for x in self.calls if x[0]=='/refunds']),1)
        self.assertEqual(self.lease()['member_limit'],0)
        self.sub['current_period_end']=time.time()-1
        self.assertEqual(self.lease()['member_limit'],10)
    def test_outside_window_still_cancels_without_refund(self):
        self.prepare(15*86400);request_cancellation(self.db,self.remote,self.order);process_cancellations(self.db,self.remote)
        self.assertEqual(self.db.get(Cancellation,self.order.id).refund_status,'not_eligible')
        self.assertFalse(self.refunds)
    def test_uncertain_refund_recovers_without_duplicate(self):
        self.prepare();request_cancellation(self.db,self.remote,self.order)
        original=self.remote.post
        def post(path,data,key):
            result=original(path,data,key)
            if path=='/refunds':raise TimeoutError()
            return result
        self.remote.post=post;process_cancellations(self.db,self.remote)
        row=self.db.get(Cancellation,self.order.id);row.next_attempt=0;self.db.commit()
        self.remote.post=original;process_cancellations(self.db,self.remote)
        self.assertEqual(row.refund_status,'succeeded');self.assertEqual(len(self.refunds),1)
    def test_wrong_invoice_never_refunded(self):
        self.prepare();self.invoice['subscription']='sub_other'
        request_cancellation(self.db,self.remote,self.order);process_cancellations(self.db,self.remote)
        self.assertFalse(self.refunds);self.assertTrue(self.db.get(Cancellation,self.order.id).error)
    def test_duplicate_request_keeps_received_time(self):
        self.prepare();row=request_cancellation(self.db,self.remote,self.order);stamp=row.requested
        self.assertEqual(request_cancellation(self.db,self.remote,self.order).requested,stamp)

class SupportTests(unittest.TestCase):
    setUp=account_tests.AccountTests.setUp
    tearDown=account_tests.AccountTests.tearDown
    customer_session=account_tests.AccountTests.customer_session
    post=account_tests.AccountTests.post
    as_user=account_tests.AccountTests.as_user
    def test_support_requires_verified_mfa_and_csrf(self):
        self.assertEqual(self.client.get('/mps/support').status_code,302)
        self.customer_session(False);self.assertTrue(self.client.get('/mps/support').location.endswith('/mps/mfa'))
        self.customer_session();self.assertEqual(self.client.post('/mps/support',data={}).status_code,400)
    def test_support_private_recipient_honeypot_and_delivery(self):
        self.customer_session()
        with self.app.app_context():put('mps_support_email','private-owner@example.com');db().commit()
        page=self.client.get('/mps/support');self.assertNotIn(b'private-owner',page.data)
        with self.client.session_transaction() as s:s['mps_support_started']=time.time()-5
        self.assertEqual(self.post('/mps/support',{'email':'reply@example.com','issue':'My installation cannot register today.','website':'spam'}).status_code,400)
        self.assertEqual(self.post('/mps/support',{'email':'reply@example.com','issue':'My installation cannot register today.','website':''}).status_code,302)
        with self.app.app_context():
            tickets=db().scalars(select(SupportTicket)).all();self.assertEqual(len(tickets),1);self.assertEqual(tickets[0].state,'accepted')
            self.assertNotIn('cannot register',tickets[0].encrypted_message)
        self.assertEqual(self.mail[-1][0],'private-owner@example.com')
    def test_cancel_requires_password_and_ownership(self):
        self.customer_session()
        self.assertEqual(self.post('/mps/account/cancel',{'order_id':'missing','password':'x','confirm':'yes'}).status_code,404)
    def test_public_policies_and_support_link(self):
        for path in ['/mps/privacy','/mps/cancellation']:
            r=self.client.get(path);self.assertEqual(r.status_code,200);self.assertNotIn(b'private-owner',r.data)
