import unittest
import test_mps as base
class AdminNavigationTests(unittest.TestCase):
 setUp=base.Tests.setUp
 tearDown=base.Tests.tearDown
 as_user=base.Tests.as_user
 invite=base.Tests.invite
 post=base.Tests.post
 def test_pages_and_actions(self):
  invite=self.invite();self.as_user(self.admin)
  overview=self.client.get('/admin').text
  for text in ['id="invites"','id="create-invite"','<h2>Connected Emby servers</h2>','<h2>Recent activity</h2>']:self.assertNotIn(text,overview)
  invites=self.client.get('/admin/invitations');self.assertEqual(invites.status_code,200);self.assertIn('id="create-invite"',invites.text);self.assertIn('id="invites"',invites.text)
  self.assertIn('id="servers"',self.client.get('/admin/settings/emby').text)
  self.assertIn('<h1>Recent activity</h1>',self.client.get('/admin/activity').text)
  self.assertIn('href="/admin/invitations"',self.client.get('/admin/invites/'+invite.id+'/delete').text)
  self.assertTrue(self.post('/admin/invites/'+invite.id+'/revoke').location.endswith('/admin/invitations'))
  self.assertTrue(self.post('/admin/servers/'+str(self.server.id)+'/refresh').location.endswith('/admin/settings/emby#servers'))
 def test_access(self):
  for route in ['/admin/invitations','/admin/activity']:
   with self.client.session_transaction() as s:s.clear()
   self.assertEqual(self.client.get(route).status_code,302)
   self.as_user(self.member);self.assertEqual(self.client.get(route).status_code,403)
   self.as_user(self.admin,mfa=False);self.assertEqual(self.client.get(route).status_code,302)
