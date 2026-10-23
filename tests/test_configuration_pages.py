import unittest
import test_mps as base
from mps.core import db,put,setting
class ConfigurationTests(unittest.TestCase):
 setUp=base.Tests.setUp
 tearDown=base.Tests.tearDown
 as_user=base.Tests.as_user
 post=base.Tests.post
 def test_pages_and_guards(self):
  for category,heading in [('emby','Connected Emby servers'),('smtp','Email / SMTP'),('payments','Stripe subscriptions'),('ombi','Ombi requests'),('system','Hosting & account policy')]:
   path='/admin/settings/'+category
   self.as_user(self.member);self.assertEqual(self.client.get(path).status_code,403)
   self.as_user(self.admin,mfa=False);self.assertEqual(self.client.get(path).status_code,302)
   with self.client.session_transaction() as s:s.clear()
   self.assertEqual(self.client.get(path).status_code,302)
   self.as_user(self.admin);r=self.client.get(path);self.assertEqual(r.status_code,200);self.assertIn(heading,r.text)
   self.assertEqual(r.text.count('aria-current="page"'),2)
   if category!='emby':self.assertNotIn('Connected Emby servers',r.text)
   if category!='payments':self.assertNotIn('Stripe subscriptions',r.text)
  self.assertEqual(self.client.get('/admin/settings').location,'/admin/settings/emby')
  self.assertEqual(self.client.get('/admin/settings/unknown').status_code,404)
 def test_saves_return_and_keep_secrets(self):
  self.as_user(self.admin)
  with self.app.app_context():put('smtp_password','preserved',secret=True);db().commit()
  r=self.post('/admin/settings/smtp',{'section':'smtp','smtp_host':'smtp.example.com','smtp_port':'587','smtp_mode':'starttls','smtp_from':'test@example.com'})
  self.assertEqual(r.location,'/admin/settings/smtp')
  with self.app.app_context():self.assertEqual(setting('smtp_password',secret=True),'preserved')
  for section in ['stripe','paypal']:self.assertEqual(self.post('/admin/settings',{'section':section}).location,'/admin/settings/payments')
  self.assertEqual(self.post('/admin/smtp/test').location,'/admin/settings/smtp')
  self.assertEqual(self.post('/admin/servers/'+str(self.server.id)+'/refresh').location,'/admin/settings/emby#servers')
