import time
import unittest
import tempfile
import uuid
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from licence_store.models import Base, Order, Activation
from licence_store.service import fulfil, check_in, deliver, PaymentMismatch, ActivationLimit, remove_activation
from mps.license_tokens import verify, encode


class Remote:
    def __init__(self, objects): self.objects = objects
    def get(self, path): return self.objects[path]


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///'+self.temp.name+'/store.sqlite3')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.key = Ed25519PrivateKey.generate()
        self.public = encode(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
        self.cipher = Fernet(Fernet.generate_key())
        self.encrypt = lambda value: self.cipher.encrypt(value.encode()).decode()
        self.decrypt = lambda value: self.cipher.decrypt(value.encode()).decode()
        self.order = Order(email='buyer@example.com', installation_id=str(uuid.uuid4()),
                           amount=500, currency='gbp', price_id='price_mps', live=True, checkout_id='cs_mps')
        self.db.add(self.order); self.db.commit()
        self.checkout = {'id':'cs_mps', 'client_reference_id':self.order.id, 'mode':'subscription',
                         'livemode':True, 'status':'complete', 'payment_status':'paid', 'amount_total':500,
                         'currency':'gbp', 'subscription':'sub_mps',
                         'metadata':{'product':'mps-unlock', 'order_id':self.order.id}}
        self.sub = {'id':'sub_mps', 'livemode':True, 'status':'active',
                    'metadata':{'mps_order_id':self.order.id}, 'latest_invoice':'in_mps',
                    'current_period_end':int(time.time())+86400,
                    'items':{'data':[{'price':{'id':'price_mps'}, 'quantity':1}]}}
        self.invoice = {'status':'paid', 'amount_paid':500, 'currency':'gbp', 'subscription':'sub_mps', 'livemode':True}
        self.remote = Remote({'/checkout/sessions/cs_mps':self.checkout,
                              '/checkout/sessions/cs_mps/line_items?limit=100':{'data':[{'price':{'id':'price_mps'},'quantity':1}]},
                              '/subscriptions/sub_mps':self.sub, '/invoices/in_mps':self.invoice})
    def tearDown(self):
        self.db.close(); self.engine.dispose(); self.temp.cleanup()
    def pay(self):
        return fulfil(self.db,self.remote,'cs_mps',True,self.key,self.encrypt)
    def code(self):
        return self.decrypt(self.order.encrypted_code)
    def lease(self):
        return verify(check_in(self.db,self.remote,self.code(),self.order.installation_id,self.key),self.public,self.order.installation_id)
    def test_duplicate_payment_issues_one_code(self):
        self.pay(); first=self.code(); self.pay()
        self.assertEqual(self.code(),first)
        self.assertEqual(self.lease()['member_limit'],0)
        self.assertLessEqual(self.lease()['expires'],time.time()+3600)
    def test_unpaid_does_not_issue(self):
        self.checkout['payment_status']='unpaid'; self.assertIsNone(self.pay())
        self.assertEqual(self.order.encrypted_code,'')
    def test_wrong_price_currency_amount_and_mode_rejected(self):
        for key,value in [('amount_total',499),('currency','usd'),('livemode',False),('mode','payment')]:
            original=self.checkout[key]; self.checkout[key]=value
            with self.assertRaises(PaymentMismatch):self.pay()
            self.db.rollback();self.checkout[key]=original
        self.remote.objects['/checkout/sessions/cs_mps/line_items?limit=100']['data'][0]['price']['id']='price_other'
        with self.assertRaises(PaymentMismatch):self.pay()
    def test_failed_payment_falls_back_and_recovery_restores(self):
        self.pay(); code=self.code()
        self.sub['status']='past_due'
        self.assertEqual(self.lease()['member_limit'],10)
        self.sub['status']='active'; self.invoice['status']='open'
        self.assertEqual(self.lease()['member_limit'],10)
        self.invoice['status']='paid'
        self.assertEqual(self.lease()['member_limit'],0)
        self.assertEqual(self.code(),code)
    def test_cancelled_and_revoked_fall_back(self):
        self.pay(); self.sub['status']='canceled'
        self.assertEqual(self.lease()['member_limit'],10)
        self.sub['status']='active';self.order.revoked=True;self.db.commit()
        self.assertEqual(self.lease()['member_limit'],-1)
    def test_cross_installation_and_fake_activation_rejected(self):
        self.pay()
        for code,installation in [(self.code(),'bad-id'),(self.code()+'x',self.order.installation_id)]:
            with self.assertRaises(ValueError):check_in(self.db,self.remote,code,installation,self.key)
    def test_email_failure_retries_same_key(self):
        self.pay(); original=self.code()
        def fail(*args):raise OSError('offline')
        deliver(self.db,self.decrypt,fail)
        self.db.refresh(self.order)
        self.assertEqual(self.order.email_state,'pending')
        self.order.email_next=0;self.db.commit(); messages=[]
        deliver(self.db,self.decrypt,lambda *args:messages.append(args))
        self.db.refresh(self.order)
        self.assertEqual(self.order.email_state,'accepted')
        self.assertIn(original,messages[0][2])
        deliver(self.db,self.decrypt,lambda *args:messages.append(args))
        self.assertEqual(len(messages),1)

    def test_three_activations_then_removal_disables_old_id(self):
        self.pay()
        ids=[str(uuid.uuid4()) for _ in range(4)]
        for app_id in ids[:3]:
            value=check_in(self.db,self.remote,self.code(),app_id,self.key,'203.0.113.9')
            self.assertEqual(verify(value,self.public,app_id)['member_limit'],0)
        with self.assertRaises(ActivationLimit):
            check_in(self.db,self.remote,self.code(),ids[3],self.key,'203.0.113.10')
        remove_activation(self.db,self.order.id,ids[0],'203.0.113.20')
        removed=check_in(self.db,self.remote,self.code(),ids[0],self.key,'203.0.113.9')
        self.assertEqual(verify(removed,self.public,ids[0])['member_limit'],-1)
        value=check_in(self.db,self.remote,self.code(),ids[3],self.key,'203.0.113.10')
        self.assertEqual(verify(value,self.public,ids[3])['member_limit'],0)
        self.assertEqual(self.db.get(Activation,ids[3]).first_ip,'203.0.113.10')
        self.assertFalse(self.db.get(Activation,ids[0]).active)

    def test_concurrent_registration_cannot_exceed_three(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        self.pay();code=self.code()
        for _ in range(2):check_in(self.db,self.remote,code,str(uuid.uuid4()),self.key)
        self.db.commit();barrier=Barrier(2)
        def attempt(_):
            with Session(self.engine) as connection:
                barrier.wait()
                try:
                    check_in(connection,self.remote,code,str(uuid.uuid4()),self.key)
                    return True
                except ActivationLimit:return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
        self.assertEqual(sum(results),1)
