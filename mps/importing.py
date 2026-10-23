"""Read-only remote discovery. Imported identities never enter the billing/access worker."""
import time
from collections import Counter,defaultdict
from contextlib import contextmanager
from sqlalchemy import select,update
from sqlalchemy.exc import IntegrityError
from .core import db,digest,audit,put
from .models import Server,Setting,ImportedProfile,ImportedIdentity,ImportControl,DeletionJob
from .provision import client
from .emby import APIError
import json,secrets

@contextmanager
def import_lock():
    if not db().get(ImportControl,1):
        try:db().add(ImportControl(id=1,lock=''));db().commit()
        except IntegrityError:db().rollback()
    token=secrets.token_hex(16)
    changed=db().execute(update(ImportControl).where(ImportControl.id==1,ImportControl.lock=='').values(lock=token)).rowcount;db().commit()
    if not changed:raise APIError('Another import is running or was interrupted; an operator must review it before retrying.')
    try:yield
    except Exception:db().rollback();raise
    finally:db().execute(update(ImportControl).where(ImportControl.id==1,ImportControl.lock==token).values(lock=''));db().commit()

def discover():
    snapshots=[]
    servers=db().scalars(select(Server).order_by(Server.id)).all()
    if not servers:raise APIError('Configure an Emby server first.')
    for server in servers:
        rows=client(server).users()
        if isinstance(rows,dict):
            total=rows.get('TotalRecordCount');rows=rows.get('Items')
            if total is not None and (not isinstance(rows,list) or total!=len(rows)):raise APIError('Emby returned a partial user list; nothing was imported.')
        if not isinstance(rows,list) or len(rows)>10000:raise APIError('Emby returned an invalid user list.')
        seen=set()
        for u in rows:
            if not isinstance(u,dict):raise APIError('Invalid Emby user record.')
            rid=str(u.get('Id',''));name=u.get('Name','');connect=str(u.get('ConnectUserName') or '').strip();cid=str(u.get('ConnectUserId') or '')
            if not rid or len(rid)>120 or rid in seen or not isinstance(name,str) or not name or len(name)>120 or len(connect)>254 or len(cid)>120:raise APIError('Invalid or duplicate Emby identity; nothing was imported.')
            seen.add(rid);policy=u.get('Policy') or {}
            if not isinstance(policy,dict):raise APIError('Invalid Emby policy record.')
            snapshots.append({'server_id':server.id,'remote_id':rid,'name':name,'connect_name':connect,'connect_id':cid,'link_type':str(u.get('ConnectLinkType') or '')[:40],
              'remote_admin':bool(policy.get('IsAdministrator')),'remote_disabled':bool(policy.get('IsDisabled')),'all_folders':bool(policy.get('EnableAllFolders')),
              'folders':json.dumps(policy.get('EnabledFolders') or [])})
    return snapshots

def import_users():
    with import_lock():
        rows=discover() # all servers must succeed before any inventory is changed
        counts=Counter((r['server_id'],r['connect_name'].casefold()) for r in rows if r['connect_name'])
        cids=defaultdict(set)
        for r in rows:
            if r['connect_name'] and r['connect_id']:cids[r['connect_name'].casefold()].add(r['connect_id'])
        existing={(r.server_id,r.remote_id):r for r in db().scalars(select(ImportedIdentity)).all()}
        for r in existing.values():r.present=False
        stamp=time.time()
        for data in rows:
            namekey=data['connect_name'].casefold();issue=''
            if namekey and (counts[(data['server_id'],namekey)]>1 or len(cids[namekey])>1):
                issue='Conflicting Connect identity; kept separate for administrator review.'
            basis='Emby Connect' if namekey and not issue else 'Server-local'
            key='connect:'+namekey if basis=='Emby Connect' else f"local:{data['server_id']}:{data['remote_id']}"
            pid=digest(key);identity=existing.get((data['server_id'],data['remote_id']))
            if identity and identity.profile_id!=pid:
                pid=identity.profile_id;issue='Connect identity changed since import; existing association preserved for review.'
            profile=db().get(ImportedProfile,pid)
            if not profile:
                profile=ImportedProfile(id=pid,name=data['name'],connect_name=data['connect_name'] if basis=='Emby Connect' else '',match_basis=basis);db().add(profile);db().flush()
            if not identity:identity=ImportedIdentity(server_id=data['server_id'],remote_id=data['remote_id'],profile_id=pid);db().add(identity)
            for key,value in data.items():setattr(identity,key,value)
            identity.present=True;identity.issue=issue;identity.last_seen=stamp
        put('import_seen',str(stamp));audit(f'Imported/refreshed {len(rows)} Emby identities without changing remote accounts');db().commit()
        return {'accounts':len(rows),'profiles':len(set(r.profile_id for r in db().scalars(select(ImportedIdentity).where(ImportedIdentity.present==True)).all())),'review':sum(bool(r.issue) for r in db().scalars(select(ImportedIdentity).where(ImportedIdentity.present==True)).all())}

def inventory():
    profiles=db().scalars(select(ImportedProfile).where((ImportedProfile.member_id==None)|(~ImportedProfile.member_id.in_(select(DeletionJob.member_id)))).order_by(ImportedProfile.name)).all()
    identities=db().execute(select(ImportedIdentity,Server).join(Server,Server.id==ImportedIdentity.server_id)).all()
    by_profile=defaultdict(list)
    by_name=defaultdict(set)
    for row,server in identities:
        by_profile[row.profile_id].append((row,server))
        if row.present:by_name[row.name.casefold()].add(row.profile_id)
    suggestions={p.id:sorted({other for row,_ in by_profile[p.id] for other in by_name[row.name.casefold()] if other!=p.id}) for p in profiles}
    return profiles,by_profile,suggestions

def linked_inventory(member_id):
    return db().execute(select(ImportedIdentity,Server).join(Server,Server.id==ImportedIdentity.server_id).join(ImportedProfile,ImportedProfile.id==ImportedIdentity.profile_id).where(ImportedProfile.member_id==member_id)).all()
