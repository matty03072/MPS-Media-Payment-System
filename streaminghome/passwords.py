"""Retryable password propagation to verified linked identities; never Connect."""
import json,time
from urllib.parse import quote
from flask import current_app,abort
from sqlalchemy import select,delete
from .core import db,seal,unseal,member_lock,audit
from .models import Member,Account,Server,OnboardingJob,PasswordJob,OneTimeToken
from .provision import accounts,client
from .integrations import OmbiAPI
from .emby import APIError

TTL=86400

def status(member):return db().get(PasswordJob,member.id)

def enqueue(member,password):
    # Caller holds the member operation lock and commits with the new portal hash.
    from .deletion import requested
    if member.disabled or requested(member.id):abort(409,'This account is unavailable.')
    targets=[{'server':a.server_id,'id':a.remote_id,'name':a.remote_name or member.username,'done':False} for a in accounts(member) if a.remote_id and not member.admin]
    from .importing import linked_inventory
    for identity,server in ([] if member.admin else linked_inventory(member.id)):
        if identity.remote_admin or identity.issue:continue
        if not any(t['server']==server.id and t['id']==identity.remote_id for t in targets):
            targets.append({'server':server.id,'id':identity.remote_id,'name':identity.name,'done':False})
    job=status(member)
    if not job:job=PasswordJob(member_id=member.id);db().add(job)
    job.credential=seal(password);job.expires=time.time()+TTL;job.targets=json.dumps(targets)
    job.ombi_id=member.ombi_user_id;job.ombi_done=not bool(member.ombi_user_id)
    job.state='pending';job.error='';job.attempts=0;job.next_attempt=0
    # Incomplete provisioning must never later install an obsolete password.
    setup=db().get(OnboardingJob,member.id)
    if setup and setup.state!='done':
        setup.credential=seal(password);setup.expires=time.time()+TTL;setup.next_attempt=0
    db().flush()

def tick():
    from .deletion import requested
    for job in db().scalars(select(PasswordJob).where(PasswordJob.state.in_(['pending','retry'])).limit(50)).all():
        member=db().get(Member,job.member_id)
        if member.operation_lock:continue
        try:
            with member_lock(member):
                db().refresh(job);db().refresh(member)
                if job.state not in ('pending','retry'):continue
                if member.disabled or requested(member.id) or job.expires<time.time():
                    job.credential='';job.state='expired';job.error='Change your password again to restart linked-service syncing.';db().commit();continue
                if job.next_attempt>time.time():continue
                password=unseal(job.credential);targets=json.loads(job.targets);errors=[]
                for target in targets:
                    if target['done']:continue
                    try:
                        # Validate the stored remote ID, not merely a matching email/name.
                        shared=db().scalar(select(Account).where(Account.server_id==target['server'],Account.remote_id==target['id'],Account.member_id!=member.id))
                        from .models import ImportedIdentity,ImportedProfile
                        imported=db().scalar(select(ImportedProfile).join(ImportedIdentity,ImportedIdentity.profile_id==ImportedProfile.id).where(ImportedIdentity.server_id==target['server'],ImportedIdentity.remote_id==target['id'],ImportedProfile.member_id!=None,ImportedProfile.member_id!=member.id))
                        if shared or imported:raise APIError('Shared identity')
                        api=client(db().get(Server,target['server']))
                        user=api.call('GET','/Users/'+quote(target['id'],safe=''))
                        if str(user.get('Id'))!=target['id'] or user.get('Name')!=target['name'] or user.get('Policy',{}).get('IsAdministrator') is not False:raise APIError('Identity requires review')
                        api.password(target['id'],password);target['done']=True
                        job.targets=json.dumps(targets);db().commit()
                    except APIError:errors.append('An Emby password update is awaiting retry or administrator review.')
                if not job.ombi_done:
                    try:
                        if job.ombi_id!=member.ombi_user_id:raise APIError('Linked identity changed')
                        api=current_app.config.get('OMBI_FACTORY',OmbiAPI)()
                        token=api.password(job.ombi_id,member.ombi_username or member.username,member.email,password)
                        member.ombi_token=seal(token);job.ombi_done=True;db().commit()
                    except APIError:errors.append('Ombi password update is awaiting retry or administrator review.')
                job.state='done' if all(t['done'] for t in targets) and job.ombi_done else 'retry'
                job.error=' '.join(errors);job.attempts+=1;job.next_attempt=time.time()+min(3600,60*2**min(job.attempts,6))
                if job.state=='done':job.credential='';audit('Linked-service password sync completed')
                db().commit()
        except Exception:
            db().rollback();job=db().get(PasswordJob,job.member_id)
            job.state='retry';job.error='Linked-service password sync needs administrator attention.';job.next_attempt=time.time()+300;db().commit()
