import json,time,unittest
from unittest.mock import patch
from sqlalchemy import select
import test_mps as base
from mps.core import db
from mps.models import Account,AccountPermission,Invite,InvitePermission,Member
from mps.emby import Emby,APIError
from mps.provision import sync_access

class AdapterTests(unittest.TestCase):
 def test_numeric_ids_resolved_to_guids_and_read_back(self):
  api=Emby('http://example.invalid','not-a-real-key');guid='a'*32;calls=[]
  def call(method,path,**kwargs):
   calls.append((method,path,kwargs))
   if path=='/Library/VirtualFolders':return [{'ItemId':'42','Guid':guid}]
   if method=='GET':return {'Policy':{'IsDisabled':False,'IsAdministrator':False,'EnableAllFolders':False,'EnabledFolders':[guid],'EnableLiveTvAccess':True}}
  with patch.object(api,'call',side_effect=call):api.policy('user',['42'],live_tv=True)
  body=next(c[2]['json'] for c in calls if c[0]=='POST')
  self.assertEqual(body['EnabledFolders'],[guid]);self.assertTrue(body['EnableAllChannels']);self.assertFalse(body['EnableLiveTvManagement'])
 def test_disable_does_not_depend_on_existing_libraries(self):
  api=Emby('http://example.invalid','key')
  with patch.object(api,'call',side_effect=[{}, {'Policy':{'IsDisabled':True,'IsAdministrator':False,'EnableAllFolders':False,'EnabledFolders':[],'EnableLiveTvAccess':False}}]) as call:
   api.policy('user',['deleted-library'],disabled=True)
   self.assertEqual(call.call_args_list[0].args[0],'POST')
   self.assertTrue(call.call_args_list[0].kwargs['json']['IsDisabled'])
 def test_missing_guid_does_not_send_invalid_permission(self):
  api=Emby('http://example.invalid','key')
  with patch.object(api,'call',return_value=[{'ItemId':'42'}]) as call:
   with self.assertRaises(APIError):api.policy('user',['42'])
   self.assertEqual(call.call_count,1)
 def test_success_response_with_wrong_policy_is_not_success(self):
  api=Emby('http://example.invalid','key')
  with patch.object(api,'call',side_effect=[[{'ItemId':'42','Guid':'a'*32}],{}, {'Policy':{}}]):
   with self.assertRaises(APIError):api.policy('user',['42'])

class PermissionTests(unittest.TestCase):
 tearDown=base.Tests.tearDown
 as_user=base.Tests.as_user
 post=base.Tests.post
 change_member=base.Tests.change_member
 invite=base.Tests.invite
 add_account=base.Tests.add_account
 def setUp(self):
  base.Tests.setUp(self);self.add_account();self.as_user(self.admin)
  with self.app.app_context():
   a=db().scalar(select(Account));a.remote_id='owned';a.remote_name=self.member.username;a.state='ready';a.access_enabled=True;db().commit()
  self.path=f'/admin/members/{self.member.id}/permissions/{self.server.id}'
 def test_admin_can_edit_media_and_live_tv_without_password(self):
  with patch.object(base.FakeEmby,'policy') as policy:
   r=self.post(self.path,{'enabled':'yes','folders':['tv'],'live_tv':'yes'})
   self.assertEqual(r.status_code,302);policy.assert_called_once_with('owned',['tv'],disabled=False,live_tv=True)
  with self.app.app_context():
   p=db().get(AccountPermission,(self.member.id,self.server.id));self.assertTrue(p.enabled);self.assertTrue(p.live_tv);self.assertFalse(p.pending)
   self.assertEqual(json.loads(db().scalar(select(Account)).folders),['tv'])
  page=self.client.get(f'/admin/members/{self.member.id}').text
  self.assertIn('Save Cinema permissions',page);self.assertIn('Allow Live TV',page)
 def test_server_disable_survives_worker_and_reenable_preserves_payment(self):
  self.post(self.path,{'folders':['movies']})
  with self.app.app_context():
   sync_access(db().get(Member,self.member.id));self.assertFalse(db().scalar(select(Account)).access_enabled)
  self.change_member(requires_payment=True,paid_through=0)
  self.post(self.path,{'enabled':'yes','folders':['movies'],'live_tv':'yes'})
  with self.app.app_context():self.assertFalse(db().scalar(select(Account)).access_enabled)
 def test_failed_remote_save_stays_pending_and_retries(self):
  with patch.object(base.FakeEmby,'policy',side_effect=APIError('offline')):
   self.assertEqual(self.post(self.path,{'enabled':'yes','folders':['tv']}).status_code,302)
  with self.app.app_context():
   p=db().get(AccountPermission,(self.member.id,self.server.id));self.assertTrue(p.pending)
   sync_access(db().get(Member,self.member.id));self.assertFalse(p.pending);self.assertEqual(db().scalar(select(Account)).sync_error,'')
 def test_member_cannot_change_permissions(self):
  self.as_user(self.member);self.assertEqual(self.post(self.path,{'enabled':'yes','folders':['tv']}).status_code,403)
 def test_invalid_library_does_not_change_saved_selections(self):
  self.assertEqual(self.post(self.path,{'enabled':'yes','folders':['unknown']}).status_code,400)
  with self.app.app_context():self.assertEqual(json.loads(db().scalar(select(Account)).folders),['movies'])
 def test_invite_live_tv_transfers_to_member(self):
  r=self.post('/admin/invites',{'label':'Live TV','days':'7','uses':'1','servers':str(self.server.id),'folders_'+str(self.server.id):['movies'],'live_tv_'+str(self.server.id):'yes','plan_id':''})
  self.assertEqual(r.status_code,200)
  with self.app.app_context():
   inv=db().scalar(select(Invite).where(Invite.label=='Live TV'))
   self.assertTrue(db().get(InvitePermission,(inv.id,self.server.id)).live_tv)
   from mps.core import unseal
   token=unseal(inv.encrypted_token)
  self.client.get('/logout')
  self.assertEqual(self.post('/join/'+token,{'username':'newviewer','email':'newviewer@example.com','password':'test-password-123','confirm':'test-password-123','consent':'yes'}).status_code,302)
  with self.app.app_context():
   member=db().scalar(select(Member).where(Member.username=='newviewer'))
   self.assertTrue(db().get(AccountPermission,(member.id,self.server.id)).live_tv)
