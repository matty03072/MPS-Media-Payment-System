import time,unittest
from unittest.mock import patch
import test_mps as base
from mps.core import db,put,seal
from mps.models import Member,Plan,MemberPlan
from mps.integrations import OmbiAPI
from mps.emby import APIError

class MemberHomeTests(unittest.TestCase):
 setUp=base.Tests.setUp
 tearDown=base.Tests.tearDown
 as_user=base.Tests.as_user
 post=base.Tests.post
 change_member=base.Tests.change_member
 def paid(self,**values):
  self.change_member(requires_payment=True,**values)
  with self.app.app_context():
   plan=Plan(name='Monthly',amount=500,stripe_price='price_test');db().add(plan);db().flush();db().add(MemberPlan(member_id=self.member.id,plan_id=plan.id));put('billing_enabled','1');db().commit()
 def test_free_and_page_separation(self):
  page=self.client.get('/member').text
  self.assertIn('Your account is on the house',page);self.assertIn('<h2>Active</h2>',page)
  self.assertNotIn('<h2>Emby accounts</h2>',page);self.assertNotIn('Add another layer',page)
  self.assertNotIn('name="username"',page)
  linked=self.client.get('/linked-accounts');self.assertEqual(linked.status_code,200);self.assertIn('name="username"',linked.text)
  security=self.client.get('/security').text;self.assertIn('six-digit code',security)
 def test_pending_and_failed_payment_actions(self):
  self.paid()
  self.assertIn('Pay subscription with Stripe',self.client.get('/member').text)
  self.change_member(billing_status='payment_failed',stripe_subscription='sub_failed',cancel_pending=True)
  page=self.client.get('/member').text;self.assertIn('<h2>Payment failed</h2>',page);self.assertIn('Retry payment with Stripe',page)
 def test_active_cancel_and_no_duplicate_checkout(self):
  self.paid(paid_through=time.time()+86400,stripe_subscription='sub_active',billing_status='active')
  page=self.client.get('/member').text;self.assertIn('Cancel subscription',page);self.assertNotIn('action="/billing/checkout"',page)
  self.change_member(cancel_pending=True);page=self.client.get('/member').text
  self.assertIn('Renewal is cancelled',page);self.assertNotIn('>Cancel subscription<',page)
 def test_history_failure_does_not_hide_membership(self):
  with self.app.app_context():token=seal('fixture-token')
  self.change_member(ombi_user_id='mine',ombi_token=token)
  with patch.object(base.FakeOmbi,'request_history',create=True,side_effect=APIError('private provider detail')):
   page=self.client.get('/member');self.assertEqual(page.status_code,200);self.assertIn('temporarily unavailable',page.text);self.assertNotIn('private provider',page.text)
 def test_linked_page_guards_and_refresh_return(self):
  self.assertEqual(self.app.test_client().get('/linked-accounts').status_code,302)
  self.assertEqual(self.post('/account/links/refresh').location,'/linked-accounts')
  self.as_user(self.admin);self.assertEqual(self.client.get('/linked-accounts').location,'/admin')
 def test_request_history_filters_other_members_and_tv_children(self):
  with self.app.app_context(),patch('mps.integrations.setting',return_value='https://ombi.invalid'):
   api=OmbiAPI('member-token')
   movie=[{'title':'Mine','requestedUserId':'mine','available':True},{'title':'Private','requestedUserId':'other','approved':True}]
   tv=[{'title':'Show','childRequests':[{'requestedUserId':'mine','approved':True,'seasonRequests':[{'seasonNumber':1}]},{'requestedUserId':'other','available':True}]}]
   with patch.object(api,'call',side_effect=[movie,tv]) as call:
    rows=api.request_history('mine');self.assertEqual(len(rows),2);self.assertEqual(rows[0]['status'],'Available');self.assertEqual(rows[1]['status'],'Approved — queued / processing');self.assertEqual(rows[1]['detail'],'Seasons 1');self.assertNotIn('Private',str(rows));self.assertNotIn('admin',str(call.call_args_list))
 def test_pending_declined_and_missing_identity(self):
  with self.app.app_context(),patch('mps.integrations.setting',return_value='https://ombi.invalid'):
   api=OmbiAPI('member-token')
   with patch.object(api,'call',side_effect=[[{'title':'Pending','requestedUserId':'mine'},{'title':'Denied','requestedUserId':'mine','denied':True},{'title':'No owner'}],[]]):
    self.assertEqual([x['status'] for x in api.request_history('mine')],['Pending approval','Declined'])
 def test_existing_unpaid_subscription_uses_billing_not_checkout(self):
  self.paid(stripe_customer='cus_fixture',stripe_subscription='sub_pending',billing_status='incomplete')
  page=self.client.get('/member').text;self.assertIn('Complete payment / billing details',page);self.assertNotIn('action="/billing/checkout"',page)
