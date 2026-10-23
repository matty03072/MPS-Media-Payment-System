"""Read-only Arr progress; member-scoped cache and deduplicated post-import Emby scans."""
import json,time,hashlib,math
from urllib.parse import urlsplit
from flask import Blueprint,current_app,request,abort,redirect,flash
from sqlalchemy import select
from .core import db,setting,put,admin_required,audit,entitled,rate_limit,current_member
from .models import Member,Server
from .emby import APIError,request as remote_request
from .availability import requests_for_members,available,targets,api_for

bp=Blueprint('connected',__name__)
SERVICES=('sonarr','radarr')

def valid_url(value):
    try:
        p=urlsplit(value);p.port
        return len(value)<=350 and p.scheme in ('http','https') and p.hostname and not (p.username or p.password or p.query or p.fragment)
    except ValueError:return False

class Arr:
    def __init__(self,url,key):self.url=url.rstrip('/');self.key=key
    def get(self,path,**params):
        return remote_request('GET',self.url+'/api/v3/'+path,headers={'X-Api-Key':self.key},params=params)
    def test(self,service):
        data=self.get('system/status')
        if not isinstance(data,dict) or str(data.get('appName','')).lower()!=service:
            raise APIError('This address did not identify itself as '+service.title()+'.')
        # Confirm the exact read capabilities used by progress tracking.
        self.get('queue',page=1,pageSize=1)
        return data
    def snapshot(self,service):
        inventory=self.get('movie' if service=='radarr' else 'series')
        if not isinstance(inventory,list):raise APIError('Invalid service catalogue.')
        rows=[]
        for page in range(1,51):
            result=self.get('queue',page=page,pageSize=200,includeUnknownSeriesItems='false',includeUnknownMovieItems='false')
            if not isinstance(result,dict) or not isinstance(result.get('records'),list):raise APIError('Invalid service queue.')
            rows.extend(result['records'])
            if len(rows)>=result.get('totalRecords',len(rows)):break
        else:raise APIError('Service queue exceeded the supported size.')
        return inventory,rows

def api(service,url=None,key=None):
    return current_app.config.get('ARR_FACTORY',Arr)(url or setting(service+'_url'),key or setting(service+'_key',secret=True))

@bp.post('/admin/connected/<service>')
@admin_required
def configure(service):
    if service not in SERVICES:abort(404)
    rate_limit('connected-settings',15,current_member().id)
    url=request.form.get('url','').strip().rstrip('/');key=request.form.get('key','').strip() or setting(service+'_key',secret=True)
    action=request.form.get('action','save')
    if action=='disable':
        put(service+'_enabled','0');db().commit();flash(service.title()+' tracking disabled.','success');return redirect('/admin/settings/services')
    if action not in ('save','test') or not valid_url(url) or not key:abort(400,'Enter a valid HTTP(S) URL and API key. Leave the key blank only to keep an existing key.')
    try:ids=sorted(set(map(int,request.form.getlist('servers'))))
    except ValueError:abort(400,'Choose valid Emby servers.')
    if not set(ids)<=set(db().scalars(select(Server.id))):abort(400,'Choose valid Emby servers.')
    scans=request.form.get('scan')=='yes'
    if scans and not ids:abort(400,'Select at least one Emby server for automatic scans.')
    api(service,url,key).test(service)
    if action=='test':flash(service.title()+' connection and queue access verified. Changes have not been saved.','success')
    else:
        put(service+'_url',url);put(service+'_key',key,secret=True);put(service+'_enabled','1')
        put(service+'_scan','1' if scans else '0');put(service+'_servers',json.dumps(ids));put(service+'_checked',str(time.time()))
        audit('Updated '+service+' connected service');db().commit();flash(service.title()+' connected. Progress will appear after the next background check.','success')
    return redirect('/admin/settings/services')

def cache_key(member):return 'request_progress_'+member.id

def enrich(member,history):
    try:cached=json.loads(setting(cache_key(member),'{}'))
    except (ValueError,TypeError):return history
    if cached.get('owner')!=member.ombi_user_id:return history
    for item in history:
        progress=cached.get('items',{}).get(item.get('key'))
        if progress and item.get('status') not in ('Pending approval','Declined') and setting(('radarr' if item.get('kind')=='Movie' else 'sonarr')+'_enabled')=='1':
            item['progress']=dict(progress)
            if time.time()-cached.get('checked',0)>180:
                item['progress']={'status':'Progress temporarily unavailable','detail':'Last check is out of date. Please check again shortly.'}
    return history

def read_history(member):
    from .integrations import OmbiAPI
    from .core import unseal
    if not member.ombi_token or not member.ombi_user_id:return [],False
    try:return enrich(member,current_app.config.get('OMBI_FACTORY',OmbiAPI)(unseal(member.ombi_token)).request_history(member.ombi_user_id)),False
    except APIError:return [],True

def derive(req,service,client,inventory,queue,episodes):
    field='tmdbId' if service=='radarr' else 'tvdbId'
    matches=[x for x in inventory if str(x.get(field))==req['provider_id']]
    if len(matches)!=1:return {'status':'Waiting for '+service.title(),'detail':'No unique matching title in the service catalogue.'},[]
    media=matches[0];mid=media['id'];wanted=req['wanted'];imported=[]
    if service=='radarr':
        file=media.get('movieFile') or {}
        fourk=not req.get('four_k') or (file.get('mediaInfo') or {}).get('width',0)>=3800
        if media.get('hasFile') and file.get('id') and fourk:imported=[str(file['id'])]
        relevant=[q for q in queue if q.get('movieId')==mid]
        total=1;count=len(imported)
    else:
        if mid not in episodes:episodes[mid]=client.get('episode',seriesId=mid)
        if not isinstance(episodes[mid],list):raise APIError('Invalid episode catalogue.')
        selected=[e for e in episodes[mid] if f"{e.get('seasonNumber')}:{e.get('episodeNumber')}" in wanted]
        ids={e['id'] for e in selected};seasons={e['seasonNumber'] for e in selected}
        imported=[f"{e['id']}:{e['episodeFileId']}" for e in selected if e.get('hasFile') and e.get('episodeFileId')]
        relevant=[q for q in queue if q.get('seriesId')==mid and (q.get('episodeId') in ids or (not q.get('episodeId') and q.get('seasonNumber') in seasons))]
        total=len(wanted);count=len(imported)
    result={'status':'Waiting for download','detail':''}
    if service=='sonarr' and total:
        result.update(import_count=count,import_total=total,import_percent=round(count/total*100,1))
    if count and count==total:result['status']='Import completed'
    elif relevant:
        states={str(q.get('trackedDownloadState','')).lower() for q in relevant}
        statuses={str(q.get('status','')).lower() for q in relevant}
        if 'importfailed' in states:result['status']='Import failed';result['detail']='Administrator attention is required.'
        elif any(q.get('errorMessage') or str(q.get('trackedDownloadStatus','')).lower() in ('warning','error') for q in relevant):
            result['status']='Download / import needs attention';result['detail']='Administrator attention is required.'
        elif 'importing' in states:result['status']='Importing'
        elif 'importpending' in states or 'completed' in statuses:result['status']='Waiting for import'
        elif 'failed' in statuses:result['status']='Download failed'
        elif 'paused' in statuses:result['status']='Download paused'
        else:result['status']='Downloading'
        # Deduplicate season-pack queue entries; percent covers active matching downloads only.
        unique={str(q.get('downloadId') or q.get('id')):q for q in relevant}.values()
        parts=[];etas=[]
        for q in unique:
            size=q.get('size');left=q.get('sizeleft',q.get('sizeLeft'))
            if isinstance(size,(int,float)) and isinstance(left,(int,float)) and math.isfinite(size) and math.isfinite(left) and size>0:
                parts.append((size,max(0,min(size,left))))
            eta=q.get('estimatedCompletionTime')
            if eta:etas.append(str(eta))
        if parts and len(parts)==len(unique):result['percent']=round(100*(1-sum(x[1] for x in parts)/sum(x[0] for x in parts)),1)
        if etas and len(etas)==len(unique):result['eta']=max(etas)
    elif count:result['status']='Partially imported'
    return result,sorted(imported)

def scan_imports(member,req,service,imported):
    if not imported or setting(service+'_scan')!='1':return False
    selected=set(json.loads(setting(service+'_servers','[]')));ok=False
    for server,uid in targets(member):
        if server.id not in selected:continue
        key='arr_scan_'+hashlib.sha256(f'{service}:{setting(service+"_url")}:{req["provider_id"]}:{server.id}'.encode()).hexdigest()
        previous=json.loads(setting(key,'{}'))
        seen=set(previous.get('files',[]))
        if set(imported)<=seen:ok=True;continue
        throttle='arr_scan_time_'+str(server.id)
        if time.time()-float(setting(throttle,'0'))<60:continue
        # Reserve a bounded retry window before the remote write, including uncertain responses.
        put(throttle,time.time());db().commit()
        api_for(server).call('POST','/Library/Refresh')
        put(key,json.dumps({'files':sorted(seen|set(imported))}));db().commit();ok=True
    return ok

def tick(force=False):
    enabled=[s for s in SERVICES if setting(s+'_enabled')=='1']
    if not enabled:return
    now=time.time()
    if not force and now-float(setting('arr_checked','0'))<60:return
    put('arr_checked',now);db().commit()
    try:
        reqs=requests_for_members();snapshots={};clients={};episodes={};failures=0
        for service in enabled:
            try:
                clients[service]=api(service);snapshots[service]=clients[service].snapshot(service)
            except Exception:failures+=1
        members=db().scalars(select(Member).where(Member.disabled==False,Member.verified==True,Member.ombi_user_id!='')).all()
        owners={}
        for member in members:owners.setdefault(member.ombi_user_id,[]).append(member)
        for owner,matches in owners.items():
            if len(matches)!=1 or not entitled(matches[0]):continue
            member=matches[0];entries={}
            for req in (r for r in reqs if r['owner']==owner):
                service='radarr' if req['kind']=='movie' else 'sonarr'
                if service not in enabled or not req.get('approved',True):continue
                try:
                    if service not in snapshots:raise APIError('Unavailable')
                    progress,imported=derive(req,service,clients[service],*snapshots[service],episodes)
                    try:
                        found,_=available(member,req)
                        ready={'movie'} if req['kind']=='movie' else req['wanted']
                        if ready and ready<=found:progress['status']='Available to watch'
                        elif imported:
                            if scan_imports(member,req,service,imported):progress['status']='Emby scan queued — awaiting availability' if len(imported)==len(ready) else progress['status'];progress['detail']='Emby scan requested; availability is checked in your accessible libraries.'
                    except APIError:progress['detail']='Emby availability could not be verified. Will retry.'
                    entries[req['key']]=progress
                except Exception:
                    db().rollback();failures+=1;entries[req['key']]={'status':'Progress temporarily unavailable','detail':'Will retry on the next background check.'}
            put(cache_key(member),json.dumps({'owner':owner,'checked':now,'items':entries}));db().commit()
        put('arr_status',f'Last check completed; {failures} service/request checks need attention.');put('arr_completed',now);db().commit()
    except Exception:
        db().rollback();put('arr_status','Request inventory unavailable. Will retry on the next check.');db().commit()
