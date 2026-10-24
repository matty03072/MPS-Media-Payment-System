import unittest
import time
import hashlib
import uuid
import pyotp
from sqlalchemy import select
from werkzeug.security import generate_password_hash
import test_mps as base
from mps.core import db, seal, put
from licence_store.models import Base, Customer, CustomerToken, Order, Activation, RecoveryCode
from licence_store.web import install


class AccountTests(unittest.TestCase):
    def setUp(self):
        base.Tests.setUp(self)
        install(self.app, 'mps')
        Base.metadata.create_all(self.app.extensions['engine'])
        with self.app.app_context():
            self.customer=Customer(email='buyer@example.com',password_hash=generate_password_hash('buyer-password-123'),verified=True,mfa_secret=seal(pyotp.random_base32()))
            db().add(self.customer);db().commit()
    tearDown=base.Tests.tearDown
    post=base.Tests.post
    as_user=base.Tests.as_user
    def customer_session(self, mfa=True):
        with self.client.session_transaction() as s:
            s['mps_customer']=self.customer.id;s['mps_customer_version']=self.customer.version;s['mps_mfa']=mfa
    def test_mfa_required_for_account_and_purchase(self):
        self.customer_session(False)
        for path in ['/mps/account','/mps/checkout']:
            r=self.client.get(path) if path.endswith('account') else self.post(path)
            self.assertTrue(r.location.endswith('/mps/mfa'))
        self.customer_session()
        self.assertEqual(self.client.get('/mps/account').status_code,200)
    def test_customer_cannot_access_admin_panel(self):
        self.as_user(self.member);self.customer_session()
        self.assertEqual(self.client.get('/admin/mps-licences').status_code,403)
        self.as_user(self.admin)
        self.assertEqual(self.client.get('/admin/mps-licences').status_code,200)
    def test_customer_routes_keep_csrf(self):
        self.customer_session()
        self.assertEqual(self.client.post('/mps/account/remove',data={}).status_code,400)
        self.assertEqual(self.client.post('/mps/register',data={}).status_code,400)
    def test_cannot_remove_other_customers_registration(self):
        self.customer_session()
        with self.app.app_context():
            other=Customer(email='other@example.com',password_hash='unused');db().add(other);db().flush()
            order=Order(customer_id=other.id,email=other.email,installation_id='',amount=500,currency='gbp',price_id='price_x',live=True)
            db().add(order);db().flush()
            activation=Activation(application_id=str(uuid.uuid4()),order_id=order.id,first_ip='203.0.113.1',last_ip='203.0.113.1')
            db().add(activation);db().commit();oid=order.id;aid=activation.application_id
        result=self.post('/mps/account/remove',{'order_id':oid,'application_id':aid,'password':'buyer-password-123'})
        self.assertEqual(result.status_code,404)
        with self.app.app_context():self.assertTrue(db().get(Activation,aid).active)
    def test_password_reset_preserves_mfa_and_invalidates_sessions(self):
        self.customer_session()
        with self.app.app_context():
            original=db().get(Customer,self.customer.id).mfa_secret
            db().add(CustomerToken(digest=hashlib.sha256(b'reset-example').hexdigest(),customer_id=self.customer.id,purpose='reset',expires=time.time()+600));db().commit()
        r=self.post('/mps/reset/reset-example',{'password':'new-buyer-password','confirm':'new-buyer-password'})
        self.assertEqual(r.status_code,302)
        with self.app.app_context():
            customer=db().get(Customer,self.customer.id)
            self.assertEqual(customer.mfa_secret,original);self.assertEqual(customer.version,2)
        self.assertTrue(self.client.get('/mps/account').location.endswith('/mps/login'))
    def test_mfa_recovery_is_single_use(self):
        self.customer_session(False)
        with self.app.app_context():
            db().add(RecoveryCode(customer_id=self.customer.id,digest=hashlib.sha256(b'recovery-test').hexdigest()));db().commit()
        self.assertEqual(self.post('/mps/mfa',{'code':'recovery-test'}).status_code,302)
        self.customer_session(False)
        self.assertEqual(self.post('/mps/mfa',{'code':'recovery-test'}).status_code,400)
    def test_public_pages_render_and_purchase_disabled(self):
        for path in ['/mps','/mps/login','/mps/register','/mps/forgot','/mps/terms']:
            self.assertEqual(self.client.get(path).status_code,200,path)
        self.customer_session()
        self.assertEqual(self.post('/mps/checkout',{'terms':'yes'}).status_code,503)
    def test_signed_webhook_duplicate_and_wrong_environment(self):
        import json,hmac
        from licence_store.models import PaymentEvent
        with self.app.app_context():
            put('stripe_secret','sk_test_fixture',secret=True)
            put('mps_stripe_webhook','whsec_fixture',secret=True);db().commit()
        def deliver(live=False,signature_valid=True):
            body=json.dumps({'id':'evt_mps_fixture','type':'checkout.session.completed','livemode':live,'data':{'object':{'id':'cs_fixture'}}}).encode()
            stamp=str(int(time.time()))
            signature=hmac.new(b'whsec_fixture',stamp.encode()+b'.'+body,hashlib.sha256).hexdigest() if signature_valid else 'bad'
            return self.client.post('/mps/stripe/webhook',data=body,headers={'Content-Type':'application/json','Stripe-Signature':f't={stamp},v1={signature}'})
        self.assertEqual(deliver().status_code,200)
        self.assertEqual(deliver().status_code,200)
        self.assertEqual(deliver(True).status_code,400)
        self.assertEqual(deliver(signature_valid=False).status_code,400)
        with self.app.app_context():self.assertEqual(len(db().scalars(select(PaymentEvent)).all()),1)
    def test_mfa_setup_must_confirm_before_purchase(self):
        with self.app.app_context():
            customer=db().get(Customer,self.customer.id);customer.mfa_secret='';db().commit()
        self.customer_session(False)
        self.assertTrue(self.client.get('/mps/account').location.endswith('/mps/mfa/setup'))
        self.assertEqual(self.client.get('/mps/mfa/setup').status_code,200)
        self.assertEqual(self.post('/mps/mfa/setup',{'code':'bad'}).status_code,400)
        from mps.core import unseal
        with self.app.app_context():secret=unseal(db().get(Customer,self.customer.id).mfa_pending)
        result=self.post('/mps/mfa/setup',{'code':pyotp.TOTP(secret).now()})
        self.assertEqual(result.status_code,200)
        self.assertIn('Save your recovery codes',result.text)
        self.assertEqual(self.client.get('/mps/account').status_code,200)
    def test_mocked_purchase_webhook_email_activation_removal_flow(self):
        import json,hmac
        from mps.core import unseal
        from mps_licence_tokens import verify,encode
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
        from pathlib import Path
        class StripeFixture:
            objects={}
            posts=[]
            def get(self,path):return self.objects[path]
            def post(self,path,data,key):
                self.posts.append((path,data,key))
                return {'id':getattr(self,'next_checkout','cs_flow'),'url':'https://checkout.stripe.com/fixture'}
        remote=StripeFixture();remote.objects={'/prices/price_fixture':{'active':True,'unit_amount':500,'currency':'gbp','livemode':False,'recurring':{'interval':'month','interval_count':1}}}
        self.app.config['MPS_STORE_STRIPE_FACTORY']=lambda:remote
        initialized=self.app.test_cli_runner().invoke(args=['mps-store-init'])
        self.assertEqual(initialized.exit_code,0,initialized.output)
        public=encode(Ed25519PrivateKey.from_private_bytes(Path(self.app.config['DATA_DIR'],'mps-signing.key').read_bytes()).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw))
        with self.app.app_context():
            put('stripe_secret','sk_test_fixture',secret=True);put('mps_stripe_webhook','whsec_flow',secret=True)
            put('mps_sales_enabled','1');put('mps_stripe_price','price_fixture');db().commit()
        self.customer_session()
        self.assertEqual(self.post('/mps/checkout',{'terms':'yes'}).status_code,303)
        with self.app.app_context():order=db().scalar(select(Order).where(Order.customer_id==self.customer.id));oid=order.id
        remote.objects.update({
            '/checkout/sessions/cs_flow':{'id':'cs_flow','client_reference_id':oid,'mode':'subscription','livemode':False,'status':'complete','payment_status':'paid','amount_total':500,'currency':'gbp','subscription':'sub_flow','metadata':{'product':'mps-unlock','order_id':oid}},
            '/checkout/sessions/cs_flow/line_items?limit=100':{'data':[{'price':{'id':'price_fixture'},'quantity':1}]},
            '/subscriptions/sub_flow':{'id':'sub_flow','livemode':False,'status':'active','metadata':{'mps_order_id':oid},'latest_invoice':'in_flow','current_period_end':int(time.time())+86400,'items':{'data':[{'price':{'id':'price_fixture'},'quantity':1}]}},
            '/invoices/in_flow':{'status':'paid','amount_paid':500,'currency':'gbp','subscription':'sub_flow','livemode':False}})
        body=json.dumps({'id':'evt_flow','type':'checkout.session.completed','livemode':False,'data':{'object':{'id':'cs_flow'}}}).encode();stamp=str(int(time.time()))
        signature=hmac.new(b'whsec_flow',stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
        response=self.client.post('/mps/stripe/webhook',data=body,headers={'Content-Type':'application/json','Stripe-Signature':f't={stamp},v1={signature}'})
        self.assertEqual(response.status_code,200)
        run=self.app.test_cli_runner().invoke(args=['mps-store-worker','--once'])
        self.assertEqual(run.exit_code,0,run.output)
        with self.app.app_context():
            order=db().get(Order,oid);self.assertEqual(order.state,'paid');self.assertEqual(order.email_state,'accepted')
            code=unseal(order.encrypted_code)
        self.assertIn(code,self.mail[-1][2])
        appids=[str(uuid.uuid4()) for _ in range(4)]
        for appid in appids[:3]:
            result=self.client.post('/mps/api/check-in',json={'code':code,'installation_id':appid})
            self.assertEqual(result.status_code,200)
            self.assertEqual(verify(result.json['lease'],public,appid,allow_test=True)['member_limit'],0)
        self.assertEqual(self.client.post('/mps/api/check-in',json={'code':code,'installation_id':appids[3]}).status_code,409)
        removed=self.post('/mps/account/remove',{'order_id':oid,'application_id':appids[0],'password':'buyer-password-123'})
        self.assertEqual(removed.status_code,302)
        result=self.client.post('/mps/api/check-in',json={'code':code,'installation_id':appids[0]})
        self.assertEqual(verify(result.json['lease'],public,appids[0],allow_test=True)['member_limit'],-1)
        self.assertEqual(self.client.post('/mps/api/check-in',json={'code':code,'installation_id':appids[3]}).status_code,200)

        # Repurchasing must not inherit the previous cancellation/refund request.
        from licence_store.models import Cancellation
        with self.app.app_context():
            db().add(Cancellation(order_id=oid,invoice_id='in_flow',ends=time.time(),cancel_status='scheduled',refund_status='succeeded'));db().commit()
        remote.objects['/subscriptions/sub_flow']['status']='canceled'
        remote.next_checkout='cs_new_subscription'
        self.assertEqual(self.post('/mps/checkout',{'terms':'yes'}).status_code,303)
        with self.app.app_context():
            orders=db().scalars(select(Order).where(Order.customer_id==self.customer.id)).all()
            self.assertEqual(len(orders),2)
            self.assertEqual(db().get(Cancellation,oid).refund_status,'succeeded')
            new=next(x for x in orders if x.id!=oid)
            self.assertIsNone(db().get(Cancellation,new.id))
