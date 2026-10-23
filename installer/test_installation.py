import hashlib, json, tempfile, time, unittest, sys, os, uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text, select
from streaminghome import create_app
from streaminghome.models import Member, Setting
from streaminghome.core import db, put
from streaminghome.schema import migrate
from installer.configure import prepare

class InstallerTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  self.pg_engine=None
  if os.environ.get('TEST_POSTGRES_URL'):
   from sqlalchemy.engine import make_url
   url=make_url(os.environ['TEST_POSTGRES_URL'])
   if not url.database.endswith('_acceptance'):raise RuntimeError('Test PostgreSQL database must end with _acceptance')
   self.pg_engine=create_engine(url);self.schema='test_'+uuid.uuid4().hex
   with self.pg_engine.begin() as c:c.execute(text('CREATE SCHEMA '+self.schema))
   url=url.update_query_dict({'options':'-csearch_path='+self.schema})
   config={'INSTALLER_ID':'members-portal','DATA_DIR':str(self.root/'data'),'DATABASE_URL':url.render_as_string(hide_password=False),'PUBLIC_URL':'http://localhost:8099'}
   (self.root/'config.json').write_text(json.dumps(config))
  self.config=prepare(self.root/'config.json',self.root/'data')
  self.app=create_app(dict(self.config,TESTING=True));self.client=self.app.test_client()
 def tearDown(self):
  self.app.extensions['engine'].dispose()
  if self.pg_engine:
   with self.pg_engine.begin() as c:c.execute(text('DROP SCHEMA '+self.schema+' CASCADE'))
   self.pg_engine.dispose()
  self.temp.cleanup()
 def form(self,token=None,**extra):
  self.client.get('/setup')
  with self.client.session_transaction() as s: csrf=s['csrf']
  return dict(csrf=csrf,token=token or (self.root/'data/setup-code.txt').read_text().strip(),site_name='Test Portal',email='owner@example.invalid',username='owner',password='Test-only-owner-2026!',**extra)
 def test_claim_once_and_mfa(self):
  form=self.form();r=self.client.post('/setup',data=form);self.assertEqual(r.status_code,302)
  self.assertEqual(self.client.get('/setup').location,'/login')
  with self.app.app_context():
   self.assertEqual(len(db().scalars(select(Member)).all()),1);self.assertEqual(db().get(Setting,'setup_token_hash').value,'')
 def test_bad_token_and_csrf(self):
  self.assertEqual(self.client.post('/setup',data=self.form(token='wrong')).status_code,403)
  form=self.form();form['csrf']='bad';self.assertEqual(self.client.post('/setup',data=form).status_code,400)
 def test_expired_code(self):
  with self.app.app_context():put('setup_expires','1');db().commit()
  self.assertEqual(self.client.post('/setup',data=self.form()).status_code,403)
 def test_anonymous_guide_protected(self):
  self.assertEqual(self.client.get('/admin/installation').status_code,302)
 def test_rerun_preserves_owner_and_key(self):
  self.client.post('/setup',data=self.form());key=(self.root/'data/master.key').read_bytes()
  prepare(self.root/'config.json',self.root/'data')
  self.assertEqual(key,(self.root/'data/master.key').read_bytes())
  with self.app.app_context():self.assertEqual(len(db().scalars(select(Member)).all()),1)
 def test_reject_unrelated_db(self):
  engine=create_engine('sqlite:///'+str(self.root/'unrelated.db'))
  with engine.begin() as c:c.execute(text('CREATE TABLE unrelated (id INTEGER)'))
  with self.assertRaises(RuntimeError):migrate(engine)
  engine.dispose()
 def test_worker_and_health(self):
  from streaminghome.worker import tick
  with self.app.app_context():
   tick();self.assertGreater(float(db().get(Setting,'worker_seen').value),time.time()-30)
  self.assertEqual(self.client.get('/health').status_code,200)
 def test_sqlite_backup(self):
  if self.pg_engine:self.skipTest('SQLite backup is not a PostgreSQL backup')
  import subprocess,sqlite3
  self.client.post('/setup',data=self.form())
  r=subprocess.run([sys.executable,str(Path(__file__).parent/'backup.py'),'--config',str(self.root/'config.json'),'--destination',str(self.root/'backup')],capture_output=True)
  self.assertEqual(r.returncode,0,r.stderr.decode())
  from contextlib import closing
  with closing(sqlite3.connect(self.root/'backup/members.sqlite3')) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM sh_members').fetchone()[0],1)
  self.assertEqual((self.root/'data/master.key').read_bytes(),(self.root/'backup/master.key').read_bytes())
  restored=dict(self.config,DATA_DIR=str(self.root/'backup'),DATABASE_URL='sqlite:///'+str(self.root/'backup/members.sqlite3'),TESTING=True)
  restored_app=create_app(restored)
  self.assertEqual(restored_app.test_client().get('/setup').location,'/login')
  self.assertEqual(restored_app.test_client().get('/health').status_code,200)
  restored_app.extensions['engine'].dispose()

 def test_mfa_required_and_guide(self):
  import pyotp
  from streaminghome.core import unseal
  self.client.post('/setup',data=self.form())
  self.client.get('/login')
  with self.client.session_transaction() as s:csrf=s['csrf']
  self.assertEqual(self.client.post('/login',data={'csrf':csrf,'email':'owner@example.invalid','password':'Test-only-owner-2026!'}).location,'/security')
  self.assertEqual(self.client.get('/admin/installation').location,'/security')
  with self.client.session_transaction() as s:csrf=s['csrf']
  self.assertEqual(self.client.post('/security',data={'csrf':csrf,'action':'start','password':'Test-only-owner-2026!'}).status_code,200)
  with self.app.app_context():secret=unseal(db().scalar(select(Member)).mfa_pending)
  self.assertEqual(self.client.post('/security',data={'csrf':csrf,'action':'confirm','password':'Test-only-owner-2026!','code':pyotp.TOTP(secret).now()}).status_code,200)
  self.assertEqual(self.client.get('/admin/installation').status_code,200)
 def test_concurrent_owner_claim(self):
  from concurrent.futures import ThreadPoolExecutor
  clients=[self.app.test_client(),self.app.test_client()];forms=[]
  for c in clients:
   c.get('/setup')
   with c.session_transaction() as session:csrf=session['csrf']
   form=self.form();form['csrf']=csrf;forms.append(form)
  with ThreadPoolExecutor(max_workers=2) as pool:
   responses=list(pool.map(lambda i:clients[i].post('/setup',data=forms[i]),range(2)))
  with self.app.app_context():self.assertEqual(len(db().scalars(select(Member)).all()),1)
  self.assertTrue(all(r.status_code in (302,403,409) for r in responses))
 def test_unknown_schema_version_refused(self):
  with self.app.extensions['engine'].begin() as c:c.execute(text('UPDATE sh_schema_version SET version=999'))
  with self.assertRaises(RuntimeError):migrate(self.app.extensions['engine'])
 def test_connection_requires_admin(self):
  self.client.get('/login')
  with self.client.session_transaction() as s:csrf=s['csrf']
  self.assertEqual(self.client.post('/admin/installation/connection',data={'csrf':csrf,'service':'sonarr','url':'http://localhost:8989','key':'test'}).status_code,302)

 def admin_session(self):
  self.client.post('/setup',data=self.form())
  with self.app.app_context():
   member=db().scalar(select(Member));member.mfa_enabled=True;db().commit();uid=member.id;version=member.version
  with self.client.session_transaction() as session:session.update(uid=uid,version=version,mfa_ok=True,csrf='fixture-csrf')
 def test_readonly_connection_secret_encryption(self):
  from unittest.mock import patch,Mock
  from streaminghome.core import unseal
  self.admin_session()
  response=Mock(status_code=200);response.json.return_value={'version':'4.0'}
  with patch('requests.get',return_value=response) as get:
   result=self.client.post('/admin/installation/connection',data={'csrf':'fixture-csrf','service':'sonarr','url':'http://localhost:8989','key':'fixture-secret-key'})
   self.assertEqual(result.status_code,302)
   self.assertEqual(get.call_args.args[0],'http://localhost:8989/api/v3/system/status')
   self.assertFalse(get.call_args.kwargs['allow_redirects'])
  with self.app.app_context():
   encrypted=db().get(Setting,'sonarr_key').value
   self.assertNotIn('fixture-secret-key',encrypted);self.assertEqual(unseal(encrypted),'fixture-secret-key')
  self.assertNotIn(b'fixture-secret-key',self.client.get('/admin/installation').data)
 def test_failed_connection_not_saved(self):
  from unittest.mock import patch,Mock
  self.admin_session()
  with patch('requests.get',return_value=Mock(status_code=401)):
   result=self.client.post('/admin/installation/connection',data={'csrf':'fixture-csrf','service':'radarr','url':'http://localhost:7878','key':'fixture-secret-key'})
   self.assertEqual(result.status_code,400)
  with self.app.app_context():self.assertIsNone(db().get(Setting,'radarr_key'))

if __name__=='__main__':
 with open('installer-test-results.log','w',encoding='utf-8') as log:
  result=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(InstallerTests))
 print('TEST_RESULT tests=%d failures=%d errors=%d skipped=%d'%(result.testsRun,len(result.failures),len(result.errors),len(result.skipped)))
 sys.exit(not result.wasSuccessful())
