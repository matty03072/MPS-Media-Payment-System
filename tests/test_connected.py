import json,time,unittest
from unittest.mock import patch,Mock
import test_mps as base
from mps.connected import derive,configure,tick,enrich,scan_imports,Arr,cache_key
from mps.core import db,put,setting
from mps.emby import APIError

class FakeArr:
 def __init__(self,url,key):self.url=url;self.key=key
 def test(self,service):return {'appName':service}
 def snapshot(self,service):return ([{'id':1,'tmdbId':42,'hasFile':True,'movieFile':{'id':2}}],[])

def movie():return dict(key='movie:7',owner='owner',kind='movie',provider='Tmdb',provider_id='42',wanted=set(),four_k=False,title='Example')

class ConnectedTests(unittest.TestCase):
 setUp=base.Tests.setUp
 tearDown=base.Tests.tearDown
 as_user=base.Tests.as_user
 post=base.Tests.post
 def enable(self):
  self.app.config['ARR_FACTORY']=FakeArr
  with self.app.app_context():put('radarr_enabled','1');put('radarr_scan','1');put('radarr_servers',json.dumps([self.server.id]));db().commit()
 def test_settings_guards_secrets_and_test_only(self):
  self.app.config['ARR_FACTORY']=FakeArr
  path='/admin/connected/radarr';data={'url':'http://radarr:7878','key':'private-key'}
  self.assertEqual(self.post(path,data).status_code,403)
  self.as_user(self.admin,mfa=False);self.assertEqual(self.post(path,data).status_code,302)
  self.as_user(self.admin)
  self.assertEqual(self.post(path,{**data,'action':'test'}).status_code,302)
  with self.app.app_context():self.assertEqual(setting('radarr_url'),'')
  self.assertEqual(self.post(path,data).status_code,302)
  with self.app.app_context():self.assertEqual(setting('radarr_key',secret=True),'private-key');self.assertNotEqual(setting('radarr_key'),'private-key')
  self.assertNotIn('private-key',self.client.get('/admin/settings/services').text)
  self.assertEqual(self.post(path,{'url':'http://radarr:7878'}).status_code,302)
  self.assertEqual(self.post(path,{'url':'http://radarr:7878','scan':'yes'}).status_code,400)
  self.assertEqual(self.post(path,{'url':'http://name:password@radarr:7878'}).status_code,400)
 def test_failed_connection_keeps_existing_settings(self):
  self.as_user(self.admin);self.enable()
  with self.app.app_context():put('radarr_url','http://original');db().commit()
  with patch.object(FakeArr,'test',side_effect=APIError('Unavailable')):
   self.assertGreaterEqual(self.post('/admin/connected/radarr',{'url':'http://new','key':'new-key'}).status_code,400)
  with self.app.app_context():self.assertEqual(setting('radarr_url'),'http://original')
 def test_movie_percent_eta_and_import_state(self):
  inventory=[{'id':1,'tmdbId':42,'hasFile':False}]
  queue=[{'movieId':1,'id':8,'size':1000,'sizeleft':250,'status':'downloading','estimatedCompletionTime':'2026-10-23T12:00:00Z'}]
  result,files=derive(movie(),'radarr',None,inventory,queue,{})
  self.assertEqual(result['percent'],75);self.assertEqual(result['status'],'Downloading');self.assertIn('eta',result);self.assertEqual(files,[])
  queue[0]['trackedDownloadState']='importFailed';self.assertEqual(derive(movie(),'radarr',None,inventory,queue,{})[0]['status'],'Import failed')
  queue[0]['trackedDownloadState']='importing';self.assertEqual(derive(movie(),'radarr',None,inventory,queue,{})[0]['status'],'Importing')
  inventory[0].update(hasFile=True,movieFile={'id':99})
  result,files=derive(movie(),'radarr',None,inventory,[],{});self.assertEqual(result['status'],'Import completed');self.assertNotIn('import_percent',result);self.assertEqual(files,['99'])
 def test_tv_requested_episodes_only_and_pack_deduplication(self):
  req={**movie(),'kind':'tv','wanted':{'1:1','1:2'}};api=Mock();api.get.return_value=[{'id':1,'seasonNumber':1,'episodeNumber':1,'hasFile':True,'episodeFileId':11},{'id':2,'seasonNumber':1,'episodeNumber':2,'hasFile':False},{'id':3,'seasonNumber':2,'episodeNumber':1,'hasFile':True,'episodeFileId':12}]
  queue=[{'id':1,'seriesId':4,'episodeId':2,'downloadId':'pack','size':100,'sizeleft':25},{'id':2,'seriesId':4,'episodeId':2,'downloadId':'pack','size':100,'sizeleft':25},{'id':3,'seriesId':4,'episodeId':3,'downloadId':'other','size':1000,'sizeleft':1000}]
  result,files=derive(req,'sonarr',api,[{'id':4,'tvdbId':42}],queue,{})
  self.assertEqual(result['import_count'],1);self.assertEqual(result['import_total'],2);self.assertEqual(result['import_percent'],50);self.assertEqual(result['percent'],75);self.assertEqual(files,['1:11'])
 def test_unknown_size_no_invented_percent_and_ambiguous_mapping(self):
  result,_=derive(movie(),'radarr',None,[{'id':1,'tmdbId':42}],[{'movieId':1,'id':1,'status':'downloading'}],{})
  self.assertNotIn('percent',result)
  result,_=derive(movie(),'radarr',None,[{'id':1,'tmdbId':42},{'id':2,'tmdbId':42}],[],{})
  self.assertIn('No unique',result['detail'])
 def test_cache_owner_staleness_and_disabled_service(self):
  self.enable();self.member.ombi_user_id='owner'
  with self.app.app_context():
   put(cache_key(self.member),json.dumps({'owner':'other','checked':time.time(),'items':{'movie:7':{'status':'SECRET'}}}));db().commit()
   self.assertNotIn('progress',enrich(self.member,[{'key':'movie:7','kind':'Movie'}])[0])
   put(cache_key(self.member),json.dumps({'owner':'owner','checked':time.time()-200,'items':{'movie:7':{'status':'Downloading'}}}));db().commit()
   self.assertIn('unavailable',enrich(self.member,[{'key':'movie:7','kind':'Movie'}])[0]['progress']['status'])
   put('radarr_enabled','0');db().commit();self.assertNotIn('progress',enrich(self.member,[{'key':'movie:7','kind':'Movie'}])[0])
 def test_scans_deduplicate_across_members_and_subset_requests(self):
  self.enable();remote=Mock()
  with self.app.app_context(),patch('mps.connected.targets',return_value=[(self.server,'remote')]),patch('mps.connected.api_for',return_value=remote):
   self.assertTrue(scan_imports(self.member,movie(),'radarr',['a','b']))
   self.assertTrue(scan_imports(self.admin,movie(),'radarr',['a']))
   self.assertFalse(scan_imports(self.admin,movie(),'radarr',['c']))
   remote.call.assert_called_once_with('POST','/Library/Refresh')
   put('arr_scan_time_'+str(self.server.id),'0');db().commit()
   self.assertTrue(scan_imports(self.member,movie(),'radarr',['c']))
   self.assertTrue(scan_imports(self.member,movie(),'radarr',['b']))
   self.assertEqual(remote.call.call_count,2)
 def test_worker_matches_exact_owner_checks_emby_and_retries(self):
  self.enable()
  from mps.models import Member
  with self.app.app_context():
   m=db().get(Member,self.member.id);m.ombi_user_id='owner';db().commit()
   with patch('mps.connected.requests_for_members',return_value=[movie(),{**movie(),'key':'movie:8','owner':'someone-else'}]),patch('mps.connected.available',return_value=(set(),None)),patch('mps.connected.scan_imports',return_value=True):
    tick(force=True)
   cache=json.loads(setting(cache_key(m)));self.assertEqual(set(cache['items']),{'movie:7'});self.assertIn('Emby scan queued',cache['items']['movie:7']['status'])
   with patch('mps.connected.requests_for_members',return_value=[movie()]),patch('mps.connected.available',return_value=({'movie'},None)),patch('mps.connected.scan_imports') as scan:
    tick(force=True);scan.assert_not_called()
   self.assertEqual(json.loads(setting(cache_key(m)))['items']['movie:7']['status'],'Available to watch')
   with patch('mps.connected.requests_for_members',return_value=[movie()]),patch.object(FakeArr,'snapshot',side_effect=APIError('offline')):tick(force=True)
   self.assertIn('unavailable',json.loads(setting(cache_key(m)))['items']['movie:7']['status'])
 def test_adapter_never_sends_key_in_url(self):
  with patch('mps.connected.remote_request',return_value={'appName':'Radarr'}) as send:
   Arr('http://radarr','secret').test('radarr')
   for call in send.call_args_list:self.assertNotIn('secret',call.args[1]);self.assertEqual(call.kwargs['headers'],{'X-Api-Key':'secret'})
