import json,unittest
from unittest.mock import patch
from sqlalchemy import select
import test_mps as base
import test_permissions
from mps.core import db,unseal
from mps.models import Server,Account,InviteServer,Invite
from mps.emby import APIError
class ServerEditTests(unittest.TestCase):
 setUp=test_permissions.PermissionTests.setUp
 tearDown=base.Tests.tearDown
 as_user=base.Tests.as_user
 post=base.Tests.post
 add_account=base.Tests.add_account
 invite=base.Tests.invite
 def test_edit_rescan_and_assign_new_library(self):
  libs=[{'id':'movies','name':'Movies'},{'id':'new','name':'New library'}]
  path=f'/admin/servers/{self.server.id}/refresh'
  with patch.object(base.FakeEmby,'libraries',return_value=libs),patch.object(base.FakeEmby,'users',return_value=[{'Id':'owned'}]):
   r=self.post(path,{'name':'Renamed server','url':'https://emby.example.com/'});self.assertEqual(r.status_code,302)
   with self.app.app_context():
    s=db().get(Server,self.server.id);self.assertEqual(s.name,'Renamed server');self.assertEqual(s.url,'https://emby.example.com');self.assertEqual(unseal(s.key),'emby-admin-key');self.assertEqual(json.loads(db().scalar(select(Account)).folders),['movies'])
   self.assertIn('New library',self.client.get('/admin/invitations').text)
   self.assertIn('New library',self.client.get(f'/admin/members/{self.member.id}').text)
   with patch.object(base.FakeEmby,'policy') as policy:
    self.assertEqual(self.post(self.path,{'enabled':'yes','folders':['movies','new']}).status_code,302)
    policy.assert_called_once_with('owned',['movies','new'],disabled=False,live_tv=False)
   self.assertEqual(self.post('/admin/invites',{'label':'New catalogue','days':'7','uses':'1','servers':str(self.server.id),'folders_'+str(self.server.id):['new']}).status_code,200)
   with self.app.app_context():self.assertEqual(json.loads(db().scalar(select(InviteServer).join(Invite,Invite.id==InviteServer.invite_id).where(Invite.label=="New catalogue")).folders),['new'])
 def test_failure_does_not_save_partial_changes(self):
  with patch.object(base.FakeEmby,'libraries',side_effect=APIError('Unavailable')):
   self.assertGreaterEqual(self.post(f'/admin/servers/{self.server.id}/refresh',{'name':'Not saved','url':'https://new.example.com','key':'replacement'}).status_code,400)
  with self.app.app_context():
   s=db().get(Server,self.server.id);self.assertEqual(s.name,'Cinema');self.assertEqual(s.url,'http://emby:8096');self.assertEqual(unseal(s.key),'emby-admin-key')
 def test_validation_and_wrong_server(self):
  path=f'/admin/servers/{self.server.id}/refresh'
  for data in [{'name':''},{'url':'https://user:password@example.com'},{'url':'https://example.com?key=bad'},{'url':'file:///tmp'}]:self.assertEqual(self.post(path,data).status_code,400)
  self.assertEqual(self.post(path,{'url':'https://other.example.com'}).status_code,400)
  self.as_user(self.member);self.assertEqual(self.post(path,{}).status_code,403)
