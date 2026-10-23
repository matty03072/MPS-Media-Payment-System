"""Durable automatic account setup. Passwords are encrypted, bounded and erased on completion."""
import json,time
from flask import current_app
from sqlalchemy import select
from .core import db,seal,unseal,setting,entitled,member_lock,audit
from .models import Member,Account,Server,OnboardingJob,ImportedProfile
from .emby import Connect,APIError
from .integrations import OmbiAPI
from .provision import accounts,client,apply_policy,server_enabled

TTL=7*86400

def status(member):return db().get(OnboardingJob,member.id)

def enqueue(member,password):
    if member.disabled:return
    if not member.admin and not accounts(member) and not db().scalar(select(ImportedProfile.id).where(ImportedProfile.member_id==member.id)):return
    job=status(member)
    if job and job.state=='done' and member.ombi_token:return
    if not job:
        job=OnboardingJob(member_id=member.id,connect_state='done' if member.connect_email else 'pending',ombi_state='done' if member.ombi_token else 'pending')
        db().add(job)
    # Never replace credentials underneath a claimed worker operation.
    if member.operation_lock:return
    if job.credential and job.expires>time.time():return
    job.credential=seal(password);job.expires=time.time()+TTL
    job.state='pending';job.next_attempt=0
    if not member.ombi_token and job.ombi_state=='done':job.ombi_state='pending'
    db().flush()

def ombi_candidate(member,identities):
    # Email chooses an existing account; a successful password login proves ownership.
    # A different account sharing the portal username must never be overwritten.
    matches=[u for u in identities if (u.get('emailAddress') or '').casefold()==member.email.casefold()]
    if len(matches)>1:
        raise APIError('Multiple Ombi identities share this email; administrator review is required.')
    if matches:
        user=matches[0]
        if not user.get('id') or not user.get('userName'):
            raise APIError('The matching Ombi identity is incomplete; administrator review is required.')
        if any(c.get('enabled') and (c.get('value') or '').lower() in ('admin','poweruser') for c in (user.get('claims') or [])):
            raise APIError('An administrator Ombi identity cannot be linked as a member.')
        if db().scalar(select(Member.id).where(Member.ombi_user_id==str(user['id']),Member.id!=member.id)):
            raise APIError('This Ombi identity is already linked to another portal account.')
        return user
    if any((u.get('userName') or '').casefold()==member.username.casefold() for u in identities):
        raise APIError('An existing Ombi identity needs administrator review; it was not changed.')
    return None

def link_ombi(member,password,job):
    if member.ombi_token:job.ombi_state='done';return
    if not entitled(member):job.ombi_state='waiting_payment';return
    if not setting('ombi_url') or not setting('ombi_key',secret=True):
        raise APIError('Ombi setup is waiting for administrator configuration.')
    remote=current_app.config.get('OMBI_FACTORY',OmbiAPI)()
    candidate=ombi_candidate(member,remote.users())
    if candidate is None:
        # An uncertain create must be reconciled through the identity list, never replayed blindly.
        if job.ombi_state in ('creating','created'):raise APIError('An uncertain Ombi creation needs administrator review.')
        job.ombi_state='creating';db().commit()
        remote.create(member.username,member.email,password)
        job.ombi_state='created';db().commit()
        candidate=ombi_candidate(member,remote.users())
        if candidate is None:
            raise APIError('Ombi creation is awaiting identity confirmation; administrator review is required.')
    try:
        token,identity=remote.authenticate(candidate['userName'],password)
    except APIError:
        raise APIError('The matching Ombi account could not be signed in. Check its sign-in details and service connection; no password was changed.') from None
    if (str(identity.get('id') or '')!=str(candidate['id']) or
        (identity.get('userName') or '').casefold()!=candidate['userName'].casefold() or
        (identity.get('emailAddress') or '').casefold()!=member.email.casefold()):
        raise APIError('Ombi returned a different identity; administrator review is required.')
    if any(c.get('enabled') and (c.get('value') or '').lower() in ('admin','poweruser') for c in (identity.get('claims') or [])):
        raise APIError('An administrator Ombi identity cannot be linked as a member.')
    if not token:raise APIError('Ombi sign-in was not confirmed.')
    member.ombi_username=identity['userName'];member.ombi_user_id=str(identity['id']);member.ombi_token=seal(token);job.ombi_state='done';db().commit()

def connect_account(member,password,job):
    if member.connect_email:job.connect_state='done';return
    remote=current_app.config.get('CONNECT_FACTORY',Connect)()
    # First adopt only an identity authenticated with the supplied signup credentials.
    try:remote.authenticate(member.email,password)
    except APIError:
        if job.connect_state=='email_conflict':
            raise APIError('Emby Connect email is already registered, but signup credentials were not accepted. Administrator review is required; Emby server and Ombi access are independent.')
        if job.connect_state=='registering':
            raise APIError('Emby Connect is awaiting verification or administrator review; registration will not be duplicated.')
        job.connect_state='registering';db().commit()
        remote.register(member.username,member.email,password)
        # Registration success can precede Emby's email verification. Server linking reports pending.
    member.connect_email=member.email;job.connect_state='done';db().commit()

def servers(member,password):
    errors=[]
    for row in accounts(member):
        if row.state=='ready' and not (row.link_pending and member.connect_email):continue
        try:
            api=client(db().get(Server,row.server_id));folders=json.loads(row.folders)
            if row.state in ('creating','review'):raise APIError('An uncertain Emby operation needs administrator review.')
            if not set(folders)<={x['id'] for x in api.libraries()}:raise APIError('An invited library is unavailable.')
            if not row.remote_id:
                if any((u.get('Name') or '').casefold()==member.username.casefold() for u in api.users()):raise APIError('An existing Emby username needs administrator review; it was not changed.')
                row.state='creating';db().commit()
                row.remote_id=api.create(member.username);row.remote_name=member.username;row.state='created';db().commit()
            if row.state=='created':
                try:
                    apply_policy(api,row,disabled=True);row.access_enabled=False
                    api.password(row.remote_id,password);row.state='secured';db().commit()
                except APIError:
                    try:api.delete(row.remote_id);row.remote_id='';row.state='not_created'
                    except APIError:row.state='review'
                    db().commit();raise APIError('Emby password or permissions setup needs retry or administrator review.') from None
            # Connect is optional discovery, not an entitlement prerequisite.
            apply_policy(api,row,disabled=not entitled(member))
            row.access_enabled=entitled(member) and server_enabled(row);row.state='ready';row.link_pending=True
            row.sync_error='';db().commit()
            if member.connect_email:
                row.link_pending=api.link(row.remote_id,member.connect_email);db().commit()
        except APIError as exc:
            row.sync_error=str(exc)[:250];db().commit();errors.append('An Emby server needs setup attention.')
    return errors

def tick():
    now=time.time()
    jobs=db().scalars(select(OnboardingJob).where(OnboardingJob.state!='done').limit(50)).all()
    for job in jobs:
        member=db().get(Member,job.member_id)
        from .deletion import requested
        if requested(member.id):continue
        if member.operation_lock:continue
        if member.disabled or job.expires<now:
            job.credential='';job.state='signin_required';db().commit();continue
        if not member.verified or not job.credential or job.next_attempt>now:continue
        try:
            with member_lock(member):
                password=unseal(job.credential);errors=[]
                # Imported Emby identities remain read-only; only provision their Ombi account.
                managed=bool(accounts(member))
                for operation in ((connect_account,link_ombi) if managed else (link_ombi,)):
                    try:operation(member,password,job)
                    except APIError as exc:errors.append(str(exc))
                if managed:errors+=servers(member,password)
                else:job.connect_state='not_required'
                complete=(not managed or job.connect_state=='done') and job.ombi_state=='done' and all(a.state=='ready' for a in accounts(member))
                job.state='done' if complete else 'retry'
                job.error=' '.join(errors)[:500]
                job.attempts+=1;job.next_attempt=now+min(3600,60*2**min(job.attempts,6))
                if complete:
                    job.credential='';audit('Automatic service account setup completed')
                db().commit()
        except Exception:
            db().rollback();job=db().get(OnboardingJob,member.id)
            job.state='retry';job.error='Automatic setup needs administrator attention.';job.next_attempt=now+300;db().commit()

def refresh_pending_links(member):
    job=status(member)
    if not job or job.state!='done' or job.next_attempt>time.time():return
    for row in accounts(member):
        if row.state=='ready' and row.link_pending:
            try:row.link_pending=client(db().get(Server,row.server_id)).link(row.remote_id,member.connect_email)
            except APIError:pass
    job.next_attempt=time.time()+300;db().commit()
