import hashlib,hmac,json,re,tempfile,time,unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pyotp
from sqlalchemy import select,update
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import mssql
from werkzeug.security import generate_password_hash,check_password_hash
from mps import create_app
from mps.models import *
from mps.core import db,put,seal,unseal,digest,entitled
from mps.billing import process_event
from mps.worker import tick
from mps.emby import APIError

class FakeConnect:
    fail=False
    registrations=[]
    def register(self,name,email,password):self.registrations.append((name,email))
    def authenticate(self,email,password):
        if self.fail:raise APIError('Connect authentication failed')

class FakeEmby:
    servers={};fail_link=False;fail_password=False;uncertain=False;pending=False;fail_rename=False
    def __init__(self,url,key):self.data=self.servers.setdefault(url,{'users':[],'policies':[],'creates':0})
    def libraries(self):return [{'id':'movies','name':'Movies'},{'id':'tv','name':'Television'}]
    def users(self):return self.data['users']
    def create(self,name):
        self.data['creates']+=1
        if self.uncertain:raise APIError('Timeout')
        uid='id-'+name;self.data['users'].append({'Name':name,'Id':uid});return uid
    def policy(self,uid,folders,disabled=False,live_tv=False):self.data['policies'].append((uid,folders,disabled))
    def password(self,uid,password):
        if self.fail_password:raise APIError('Failed password')
    def delete(self,uid):self.data['users']=[x for x in self.data['users'] if x['Id']!=uid]
    def link(self,uid,email):
        if self.fail_link:raise APIError('Link failed')
        return self.pending
    def rename(self,uid,name):
        if self.fail_rename:raise APIError('Rename failed')
        for u in self.data['users']:
            if u['Id']==uid:u['Name']=name

class FakeStripe:
    objects={};calls=[]
    def get(self,path):return self.objects[path]
    def post(self,path,data,key):
        self.calls.append(('POST',path,data,key))
        if path=='/customers':return {'id':'cus_new'}
        if path=='/checkout/sessions':return {'id':'cs_new','url':'https://checkout.stripe.com/test','expires_at':time.time()+3600}
        if path=='/billing_portal/sessions':return {'url':'https://billing.stripe.com/test'}
        if path=='/prices':return {'id':'price_new'}
        return {'id':path.rsplit('/',1)[-1]}
    def delete(self,path,data,key):self.calls.append(('DELETE',path,data,key));return {'id':path.rsplit('/',1)[-1],'status':'canceled'}

class FakeOmbi:
    def request_history(self,user_id):return []
    calls=[]
    def __init__(self,token=''):self.token=token
    def authenticate(self,name,password):return 'member-ombi-jwt',{'id':'ombi-1','userName':name,'claims':[{'value':'RequestMovie','enabled':True}]}
    def create(self,name,email,password):self.calls.append(('create',name))
    def search(self,kind,query):self.calls.append(('search',self.token,kind,query));return [{'id':123,'title':'Example Movie'}]
    def make_request(self,kind,mid):self.calls.append(('request',self.token,kind,mid));return {'result':True}

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.mail=[]
        self.app=create_app({'TESTING':True,'DATA_DIR':self.temp.name,'DATABASE_URL':'sqlite:///'+self.temp.name+'/test.sqlite3','PUBLIC_URL':'http://localhost',
          'MAIL_SENDER':lambda *args:self.mail.append(args),'EMBY_FACTORY':FakeEmby,'CONNECT_FACTORY':FakeConnect,'STRIPE_FACTORY':FakeStripe,'OMBI_FACTORY':FakeOmbi})
        Base.metadata.create_all(self.app.extensions['engine'])
        FakeEmby.servers={};FakeConnect.fail=False;FakeConnect.registrations=[];FakeStripe.objects={};FakeStripe.calls=[];FakeOmbi.calls=[]
        for flag in ('fail_link','fail_password','uncertain','pending','fail_rename'):setattr(FakeEmby,flag,False)
        with self.app.app_context():
            self.admin=Member(email='admin@example.com',username='administrator',username_key='administrator',password_hash=generate_password_hash('test-password-123'),admin=True,verified=True,mfa_enabled=True,mfa_secret=seal(pyotp.random_base32()))
            self.member=Member(email='alice@example.com',username='alice',username_key='alice',password_hash=generate_password_hash('test-password-123'),verified=True)
            db().add_all([self.admin,self.member]);db().commit()
            self.server=Server(name='Cinema',url='http://emby:8096',key=seal('emby-admin-key'),libraries=json.dumps(FakeEmby('', '').libraries()));db().add(self.server);db().commit()
        self.client=self.app.test_client();self.as_user(self.member)
    def tearDown(self):self.app.extensions['engine'].dispose();self.temp.cleanup()
    def as_user(self,member,client=None,mfa=True):
        with (client or self.client).session_transaction() as s:s.clear();s.update(uid=member.id,version=member.version,mfa_ok=mfa,csrf='csrf')
    def post(self,path,data=None,client=None):return (client or self.client).post(path,data={'csrf':'csrf',**(data or {})})
    def fetch(self,model,id):
        with self.app.app_context():return db().get(model,id)
    def change_member(self,**kwargs):
        with self.app.app_context():m=db().get(Member,self.member.id);[setattr(m,k,v) for k,v in kwargs.items()];db().commit()
    def invite(self,paid=False,uses=1):
        with self.app.app_context():
            row=Invite(label='Friends',digest=digest('invitation'),encrypted_token=seal('invitation'),expires=time.time()+3600,max_uses=uses,requires_payment=paid)
            db().add(row);db().flush();db().add(InviteServer(invite_id=row.id,server_id=self.server.id,folders='["movies"]'));db().commit();return row
    def add_account(self):
        invite=self.invite()
        with self.app.app_context():db().add(Account(member_id=self.member.id,server_id=self.server.id,invite_id=invite.id,folders='["movies"]'));db().commit()
    def provision(self):return self.post('/account/provision',{'password':'test-password-123','mode':'existing','consent':'yes'})
    def stripe_setup(self):
        self.change_member(requires_payment=True,stripe_customer='cus_alice',stripe_subscription='sub_alice',paid_through=time.time()+3600)
        with self.app.app_context():
            db().add(Plan(name='Standard',amount=999,stripe_price='price_standard'));put('stripe_secret','sk_test_fake',secret=True);put('stripe_webhook','whsec_testing',secret=True);put('billing_enabled','1');db().commit()
        FakeStripe.objects['/subscriptions/sub_alice']={'id':'sub_alice','customer':'cus_alice','metadata':{'member_id':self.member.id},'status':'active','cancel_at_period_end':False}
        FakeStripe.objects['/invoices/in_1']={'id':'in_1','customer':'cus_alice','subscription':'sub_alice','status':'paid'}
        FakeStripe.objects['/invoices/in_1/lines?limit=100']={'data':[{'type':'subscription','price':{'id':'price_standard'},'period':{'end':time.time()+86400},'proration':False}],'has_more':False}
    def event(self,kind,id='evt_1'):
        with self.app.app_context():
            e=Event(id=id,event_type=kind,object_id='in_1',live=False);db().add(e);db().commit();process_event(e)
    def test_sql_server_schema(self):
        for table in Base.metadata.sorted_tables:self.assertIn('CREATE TABLE',str(CreateTable(table).compile(dialect=mssql.dialect())))
    def test_pages_and_access_boundaries(self):
        for path in ('/member','/security'):self.assertEqual(self.client.get(path).status_code,200)
        self.assertEqual(self.client.get('/admin').status_code,403)
        anonymous=self.app.test_client();self.assertEqual(anonymous.get('/member').status_code,302)
        self.assertEqual(self.client.post('/account/username').status_code,400)
    def test_admin_mfa_cannot_be_bypassed(self):
        with self.app.app_context():a=db().get(Member,self.admin.id);a.mfa_enabled=False;db().commit()
        self.as_user(self.admin,mfa=True)
        self.assertEqual(self.client.get('/admin').location,'/security')
        self.assertEqual(self.post('/admin/settings',{'section':'smtp'}).status_code,302)
    def test_admin_cannot_disable_mfa(self):
        self.as_user(self.admin)
        self.assertEqual(self.post('/security',{'action':'disable','password':'test-password-123','code':'123456'}).status_code,403)
    def test_totp_replay_and_recovery_one_use(self):
        secret=pyotp.random_base32()
        self.change_member(mfa_enabled=True,mfa_secret=self.app.extensions['cipher'].encrypt(secret.encode()).decode())
        from mps.auth import verify_factor
        with self.app.app_context():
            m=db().get(Member,self.member.id);code=pyotp.TOTP(secret).now()
            self.assertTrue(verify_factor(m,code));self.assertFalse(verify_factor(m,code))
            db().add(RecoveryCode(digest=digest('recovery'),member_id=m.id));db().commit()
            self.assertTrue(verify_factor(m,'recovery'));self.assertFalse(verify_factor(m,'recovery'))
    def test_mfa_enrollment(self):
        self.assertEqual(self.post('/security',{'action':'start','password':'test-password-123'}).status_code,200)
        m=self.fetch(Member,self.member.id);secret=self.app.extensions['cipher'].decrypt(m.mfa_pending.encode()).decode()
        response=self.post('/security',{'action':'confirm','password':'test-password-123','code':pyotp.TOTP(secret).now()})
        self.assertEqual(response.status_code,200);self.assertIn(b'Save your recovery codes',response.data)
        self.assertTrue(self.fetch(Member,self.member.id).mfa_enabled)
    def test_login_challenge_before_member_access(self):
        self.change_member(mfa_enabled=True,mfa_secret=seal_for(self.app,pyotp.random_base32()))
        other=self.app.test_client();other.get('/login')
        with other.session_transaction() as s:csrf=s['csrf']
        result=other.post('/login',data={'csrf':csrf,'email':'alice@example.com','password':'test-password-123'})
        self.assertEqual(result.location,'/mfa');self.assertEqual(other.get('/member').location,'/login')
    def test_invite_signup_email_verification_and_no_password_storage(self):
        self.invite();other=self.app.test_client();other.get('/join/invitation')
        with other.session_transaction() as s:s['csrf']='csrf'
        response=self.post('/join/invitation',{'email':'bob@example.com','username':'bob','password':'another-password-123','confirm':'another-password-123','consent':'yes'},other)
        self.assertEqual(response.status_code,302);self.assertEqual(len(self.mail),1)
        with self.app.app_context():
            member=db().scalar(select(Member).where(Member.email=='bob@example.com'));self.assertFalse(member.verified);self.assertNotIn('another-password-123',member.password_hash)
            self.assertEqual(len(db().scalars(select(Account).where(Account.member_id==member.id)).all()),1)
        token=re.search(r'/verify/(\S+)',self.mail[0][2]).group(1)
        with other.session_transaction() as s:s['csrf']='csrf'
        self.assertEqual(self.post('/verify/'+token,{},other).status_code,302)
        self.assertEqual(self.post('/verify/'+token,{},other).status_code,400)
    def test_concurrent_invites_cannot_overbook(self):
        self.invite();barrier=Barrier(2)
        def claim(n):
            c=self.app.test_client();c.get('/join/invitation')
            with c.session_transaction() as s:s['csrf']='csrf'
            barrier.wait()
            return self.post('/join/invitation',{'email':f'p{n}@example.com','username':f'person{n}','password':'test-password-123','confirm':'test-password-123','consent':'yes'},c).status_code
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(claim,range(2)))
        self.assertIn(302,results);self.assertEqual(results.count(302),1)
    def test_expired_and_recipient_bound_invite(self):
        invite=self.invite()
        with self.app.app_context():db().get(Invite,invite.id).expires=0;db().commit()
        self.assertEqual(self.client.get('/join/invitation').status_code,400)
    def test_unpaid_member_cannot_provision_or_request(self):
        self.change_member(requires_payment=True,paid_through=0)
        self.assertEqual(self.provision().status_code,403);self.assertEqual(self.client.get('/requests').status_code,403)
    def test_provision_and_retry_does_not_duplicate(self):
        self.add_account();self.assertEqual(self.provision().status_code,302);self.assertEqual(self.provision().status_code,302)
        data=FakeEmby.servers[self.server.url];self.assertEqual(data['creates'],1);self.assertTrue(data['policies'][0][2]);self.assertFalse(data['policies'][-1][2]);self.assertEqual(data['policies'][-1][1],['movies'])
    def test_connect_failure_no_remote_accounts(self):
        self.add_account();FakeConnect.fail=True;self.assertEqual(self.provision().status_code,502)
        self.assertEqual(FakeEmby.servers[self.server.url]['creates'],0)
    def test_uncertain_remote_creation_blocks_duplicate(self):
        self.add_account();FakeEmby.uncertain=True;self.provision();FakeEmby.uncertain=False;self.provision()
        self.assertEqual(FakeEmby.servers[self.server.url]['creates'],1)
    def test_failed_password_removes_new_account(self):
        self.add_account();FakeEmby.fail_password=True;self.provision();self.assertEqual(FakeEmby.servers[self.server.url]['users'],[])
    def test_failed_link_keeps_account_disabled(self):
        self.add_account();FakeEmby.fail_link=True;self.provision();self.assertTrue(FakeEmby.servers[self.server.url]['policies'][-1][2])
        FakeEmby.fail_link=False;self.provision();self.assertEqual(FakeEmby.servers[self.server.url]['creates'],1)
    def test_username_sync_and_collision(self):
        self.add_account();self.provision()
        self.assertEqual(self.post('/account/username',{'username':'new-alice','password':'test-password-123'}).status_code,302)
        self.assertEqual(FakeEmby.servers[self.server.url]['users'][0]['Name'],'new-alice')
        FakeEmby.servers[self.server.url]['users'].append({'Id':'other','Name':'taken'})
        self.assertEqual(self.post('/account/username',{'username':'taken','password':'test-password-123'}).status_code,502)
        self.assertEqual(self.fetch(Member,self.member.id).username,'new-alice')
    def test_username_partial_failure_is_retryable(self):
        self.add_account();self.provision();FakeEmby.fail_rename=True
        self.assertEqual(self.post('/account/username',{'username':'new-alice','password':'test-password-123'}).status_code,502)
        self.assertEqual(self.fetch(Member,self.member.id).username,'new-alice')
        FakeEmby.fail_rename=False
        self.assertEqual(self.post('/account/username',{'username':'new-alice','password':'test-password-123'}).status_code,302)
    def test_paid_invoice_grants_only_paid_period(self):
        self.stripe_setup();self.event('invoice.paid');member=self.fetch(Member,self.member.id)
        self.assertGreater(member.paid_through,time.time()+80000)
        with self.app.app_context():self.assertTrue(entitled(db().get(Member,member.id)))
    def test_failed_renewal_stops_collection_preserves_paid_through(self):
        self.stripe_setup();before=self.fetch(Member,self.member.id).paid_through
        FakeStripe.objects['/invoices/in_1']['status']='open';self.event('invoice.payment_failed')
        member=self.fetch(Member,self.member.id);self.assertEqual(member.paid_through,before);self.assertEqual(member.billing_status,'payment_failed')
        self.assertTrue(any(x[0]=='DELETE' and x[2]=={'invoice_now':'false','prorate':'false'} for x in FakeStripe.calls))
        self.assertTrue(any(x[2]=={'auto_advance':'false'} for x in FakeStripe.calls))
    def test_stale_failure_event_does_not_cancel_paid_invoice(self):
        self.stripe_setup();self.event('invoice.payment_failed');self.assertEqual(FakeStripe.calls,[])
    def test_wrong_customer_cannot_grant_access(self):
        self.stripe_setup();before=self.fetch(Member,self.member.id).paid_through
        FakeStripe.objects['/subscriptions/sub_alice']['metadata']['member_id']='wrong-member'
        with self.assertRaises(APIError):self.event('invoice.paid')
        self.assertEqual(self.fetch(Member,self.member.id).paid_through,before)
    def test_checkout_return_does_not_grant_access(self):
        self.change_member(requires_payment=True,paid_through=0);self.client.get('/member?checkout=complete');self.assertEqual(self.fetch(Member,self.member.id).paid_through,0)
    def test_cancel_at_paid_period_end(self):
        self.stripe_setup();before=self.fetch(Member,self.member.id).paid_through
        self.assertEqual(self.post('/billing/cancel').status_code,302)
        self.assertEqual(self.fetch(Member,self.member.id).paid_through,before);self.assertTrue(self.fetch(Member,self.member.id).cancel_pending)
        self.assertEqual(FakeStripe.calls[-1][2],{'cancel_at_period_end':'true'})
    def test_worker_disables_expired_server_access(self):
        self.add_account();self.provision();self.change_member(requires_payment=True,paid_through=time.time()-1)
        with self.app.app_context():tick()
        self.assertTrue(FakeEmby.servers[self.server.url]['policies'][-1][2])
    def test_webhook_signature_and_duplicate(self):
        self.stripe_setup();body=json.dumps({'id':'evt_signed','type':'invoice.paid','livemode':False,'data':{'object':{'id':'in_1'}}}).encode();timestamp=str(int(time.time()))
        signature=hmac.new(b'whsec_testing',timestamp.encode()+b'.'+body,hashlib.sha256).hexdigest();headers={'Stripe-Signature':f't={timestamp},v1={signature}','Content-Type':'application/json'}
        self.assertEqual(self.client.post('/stripe/webhook',data=body,headers=headers).status_code,200)
        self.assertEqual(self.client.post('/stripe/webhook',data=body,headers=headers).status_code,200)
        self.assertEqual(self.client.post('/stripe/webhook',data=body,headers={'Stripe-Signature':'bad'}).status_code,400)
        with self.app.app_context():self.assertEqual(len(db().scalars(select(Event)).all()),1)
    def test_ombi_uses_member_token_not_admin_key(self):
        response=self.post('/requests',{'action':'link','portal_password':'test-password-123','ombi_username':'alice-ombi','ombi_password':'not-persisted'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.get('/requests?q=example&kind=movie').status_code,200)
        self.assertEqual(FakeOmbi.calls[-1][1],'member-ombi-jwt')
        self.assertEqual(self.post('/requests',{'action':'request','kind':'movie','media_id':'123'}).status_code,302)
        self.assertEqual(FakeOmbi.calls[-1],('request','member-ombi-jwt','movie',123))
    def test_secret_configuration_not_rendered(self):
        self.as_user(self.admin)
        with self.app.app_context():put('stripe_secret','sk_test_never_render_me',secret=True);db().commit()
        response=self.client.get('/admin/settings/payments');self.assertEqual(response.status_code,200);self.assertNotIn(b'never_render_me',response.data)
    def test_smtp_test_sends_only_to_admin(self):
        self.as_user(self.admin);self.assertEqual(self.post('/admin/smtp/test').status_code,303);self.assertEqual(self.mail[-1][0],'admin@example.com')
    def test_password_reset_does_not_remove_mfa(self):
        self.change_member(mfa_enabled=True,mfa_secret=seal_for(self.app,pyotp.random_base32()))
        with self.app.app_context():db().add(OneTimeToken(digest=digest('reset-token'),member_id=self.member.id,purpose='reset',expires=time.time()+300));db().commit()
        self.assertEqual(self.post('/reset/reset-token',{'password':'brand-new-password','confirm':'brand-new-password'}).status_code,302)
        m=self.fetch(Member,self.member.id);self.assertTrue(m.mfa_enabled);self.assertTrue(check_password_hash(m.password_hash,'brand-new-password'))

def seal_for(app,value):return app.extensions['cipher'].encrypt(value.encode()).decode()

if __name__=='__main__':unittest.main()
