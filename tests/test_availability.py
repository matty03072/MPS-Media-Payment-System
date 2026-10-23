import json,unittest
from unittest.mock import patch,Mock
from sqlalchemy import select
import test_mps as base
from mps.core import db,put
from mps.models import Member
from mps.availability import *

class AvailabilityTests(unittest.TestCase):
    tearDown=base.Tests.tearDown
    as_user=base.Tests.as_user
    post=base.Tests.post
    def setUp(self):
        base.Tests.setUp(self)
        self.req={'key':'movie:9','owner':'ombi-a','kind':'movie','provider':'Tmdb','provider_id':'123','wanted':set(),'title':'Test movie','four_k':False}
        self.choice=(self.server,'user-a',{'Id':'10','Name':'Test movie'})
    def test_baseline_then_new_episode_and_duplicate_suppression(self):
        self.req.update(key='tv:9',kind='tv')
        with self.app.app_context(),patch('mps.availability.available') as available_mock,patch('mps.availability.send_available') as send:
            member=db().get(Member,self.member.id)
            available_mock.return_value=({'1:1'},self.choice)
            process(member,self.req,True);send.assert_not_called()
            process(member,self.req);send.assert_not_called()
            available_mock.return_value=({'1:1','1:2'},self.choice)
            process(member,self.req);self.assertEqual(send.call_args.args[2],{'1:2'})
            process(member,self.req);self.assertEqual(send.call_count,1)
            self.assertEqual(db().scalar(select(AvailabilityDelivery)).state,'accepted_by_smtp')
    def test_uncertain_smtp_is_not_automatically_resent(self):
        with self.app.app_context(),patch('mps.availability.available',return_value=({'movie'},self.choice)),patch('mps.availability.send_available',side_effect=APIError('SMTP timeout')) as send:
            member=db().get(Member,self.member.id)
            process(member,self.req);process(member,self.req)
            self.assertEqual(send.call_count,1)
            self.assertEqual(db().scalar(select(AvailabilityDelivery)).state,'needs_review')
    def test_opt_out_consumes_arrivals_without_sending(self):
        with self.app.app_context(),patch('mps.availability.available',return_value=({'movie'},self.choice)),patch('mps.availability.send_available') as send:
            member=db().get(Member,self.member.id);put('availability_email_'+member.id,'0');db().commit()
            process(member,self.req);put('availability_email_'+member.id,'1');db().commit();process(member,self.req);send.assert_not_called()
    def test_movie_matching_rejects_wrong_provider_and_virtual(self):
        api=Mock();api.call.return_value={'Policy':{'IsDisabled':False}}
        wrong={'Id':'10','ProviderIds':{'Tmdb':'999'},'MediaSources':[{}]}
        with self.app.app_context(),patch('mps.availability.targets',return_value=[(self.server,'a')]),patch('mps.availability.api_for',return_value=api),patch('mps.availability.items',return_value={'Items':[wrong]}) as it:
            member=db().get(Member,self.member.id)
            self.assertEqual(available(member,self.req)[0],set())
            wrong['ProviderIds']['Tmdb']='123';wrong['IsVirtualItem']=True
            self.assertEqual(available(member,self.req)[0],set())
            wrong['IsVirtualItem']=False
            self.assertEqual(available(member,self.req)[0],{'movie'})
            self.assertEqual(it.call_args.args[1],'a')
    def test_tv_only_requested_playable_episodes_including_multi_episode_file(self):
        req={**self.req,'kind':'tv','provider':'Tvdb','wanted':{'1:1','1:2','1:4'}}
        api=Mock();api.call.return_value={'Policy':{}}
        series={'Id':'10','ProviderIds':{'Tvdb':'123'}}
        eps=[{'ParentIndexNumber':1,'IndexNumber':1,'IndexNumberEnd':2,'MediaSources':[{}]}, {'ParentIndexNumber':1,'IndexNumber':3,'MediaSources':[{}]}, {'ParentIndexNumber':1,'IndexNumber':4,'MediaSources':[]}]
        with self.app.app_context(),patch('mps.availability.targets',return_value=[(self.server,'a')]),patch('mps.availability.api_for',return_value=api),patch('mps.availability.items',side_effect=[{'Items':[series]},{'Items':eps,'TotalRecordCount':3}]):
            self.assertEqual(available(db().get(Member,self.member.id),req)[0],{'1:1','1:2'})
    def test_outage_does_not_create_observation(self):
        with self.app.app_context(),patch('mps.availability.available',side_effect=APIError('offline')):
            with self.assertRaises(APIError):process(db().get(Member,self.member.id),self.req,True)
            self.assertEqual(list(db().scalars(select(AvailabilityState))),[])
    def test_four_k_requires_four_k_media(self):
        self.assertFalse(playable({'MediaSources':[{'MediaStreams':[{'Type':'Video','Width':1920}]}]},True))
        self.assertTrue(playable({'MediaSources':[{'MediaStreams':[{'Type':'Video','Width':3840}]}]},True))
    def test_request_inventory_preserves_tv_requester_and_episode_scope(self):
        api=Mock();api.call.side_effect=[[{'id':1,'requestedUserId':'a','theMovieDbId':123}], [{'id':8,'tvDbId':456,'childRequests':[{'id':2,'requestedUserId':'b','seasonRequests':[{'seasonNumber':2,'episodes':[{'episodeNumber':1,'requested':True},{'episodeNumber':2,'requested':False},{'episodeNumber':3,'requested':True,'denied':True}]}]}]}]]
        with self.app.app_context():
            self.app.config['AVAILABILITY_OMBI_FACTORY']=lambda:api
            result=requests_for_members();self.assertEqual(result[1]['owner'],'b');self.assertEqual(result[1]['wanted'],{'2:1'})
    def test_ai_only_accepts_candidate_ids_and_excludes_identity(self):
        api=Mock();api.call.return_value={'Items':[{'Id':'20','Type':'Movie'}]}
        response=Mock();response.json.return_value={'message':{'content':json.dumps({'recommendations':[{'id':'invented','reason':'Nope'},{'id':'20','reason':'Shared themes'}]})}}
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        with self.app.app_context(),patch('mps.availability.items',return_value={'Items':[{'Id':'20','Type':'Movie','Name':'Real title','MediaSources':[{}]}]}),patch('mps.availability.requests.post',return_value=response) as post:
            put('availability_ai_provider','ollama');put('availability_ai_url','http://ai:11434');put('availability_ai_model','local');db().commit()
            rows,ai=recommendations(api,'a',{'Id':'10','Name':'Original'},db().get(Member,self.member.id))
            self.assertTrue(ai);self.assertEqual([r['title'] for r in rows],['Real title'])
            self.assertNotIn(self.member.email,json.dumps(post.call_args.kwargs['json']))
    def test_email_has_cid_poster_plaintext_and_escaped_recommendations(self):
        from mps.mail import build_message
        with self.app.app_context():
            msg=build_message('a@example.com','b@example.com','Available','Plain fallback',content_poster=(b'\xff\xd8\xffdemo','jpeg'),content_title='<Film>',recommendations=[{'title':'<script>','reason':'<img onerror=x>'}])
            html=msg.get_body(preferencelist=('html',)).get_content()
            self.assertIn('cid:content-poster',html);self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>',html)
            self.assertTrue(any(p['Content-ID']=='<content-poster>' for p in msg.walk()))
            self.assertIn('Plain fallback',msg.get_body(preferencelist=('plain',)).get_content())
    def test_recommendation_posters_have_unique_cids_and_optional_plot(self):
        from mps.mail import build_message
        rows=[{'title':'Film '+str(i),'reason':'Similar','plot':'<Plot>','poster':(b'\xff\xd8\xffdemo','jpeg')} for i in range(3)]
        with self.app.app_context():
            msg=build_message('a@example.com','b@example.com','Available','Plain',recommendations=rows)
            html=msg.get_body(preferencelist=('html',)).get_content()
            for i in range(1,4):
                self.assertIn('cid:recommendation-poster-'+str(i),html)
                self.assertEqual(sum(p['Content-ID']=='<recommendation-poster-'+str(i)+'>' for p in msg.walk()),1)
            self.assertIn('&lt;Plot&gt;',html)
            self.assertNotIn('poster_cid',rows[0])
            rows[0]['poster']=None
            msg=build_message('a@example.com','b@example.com','Available','Plain',recommendations=rows)
            self.assertNotIn('cid:recommendation-poster-1',msg.get_body(preferencelist=('html',)).get_content())
    def test_fallback_recommendations_include_library_plot_and_artwork(self):
        api=Mock();api.call.return_value={'Items':[{'Id':'20','Type':'Movie'}]}
        row={'Id':'20','Type':'Movie','Name':'Real title','Overview':'Library plot','ImageTags':{'Primary':'tag'},'MediaSources':[{}]}
        with self.app.app_context(),patch('mps.availability.items',return_value={'Items':[row]}),patch('mps.availability.poster',return_value=(b'poster','jpeg')) as art:
            rows,ai=recommendations(api,'a',{'Id':'10'},db().get(Member,self.member.id))
            self.assertFalse(ai);self.assertEqual(rows[0]['plot'],'Library plot');self.assertEqual(rows[0]['poster'],(b'poster','jpeg'))
            self.assertEqual(art.call_args.args[1],row)
    def test_settings_admin_only_and_member_preferences(self):
        self.assertEqual(self.post('/admin/availability/settings',{'provider':'off'}).status_code,403)
        self.assertEqual(self.post('/account/request-emails',{'emails':'yes'}).status_code,302)
        with self.app.app_context():self.assertEqual(setting('availability_suggestions_'+self.member.id),'0')
        self.as_user(self.admin)
        self.assertEqual(self.post('/admin/availability/settings',{'provider':'ollama','ai_url':'http://local:11434','model':'local','enabled':'yes'}).status_code,302)
    def test_tick_does_not_notify_other_requesters_or_unverified_members(self):
        with self.app.app_context(),patch('mps.availability.requests_for_members',return_value=[self.req]),patch('mps.availability.process') as proc:
            m=db().get(Member,self.member.id);m.ombi_user_id='another';db().commit()
            tick(force=True,baseline=True);proc.assert_not_called()
