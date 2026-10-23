"""Durable, password-confirmed member erasure with independent retryable operations."""
import json,time
from urllib.parse import quote,urlencode
from flask import Blueprint,abort,current_app,render_template,request,session,g
from sqlalchemy import select,delete,update
from werkzeug.security import check_password_hash
from .core import db,seal,unseal,current_member,login_required,admin_required,member_lock,rate_limit,audit
from .models import *
from .emby import APIError
from .integrations import OmbiAPI
from .provision import accounts,client
from .mail import send_mail
bp=Blueprint('deletion',__name__)

def requested(member_id):return db().get(DeletionJob,member_id) is not None

def visible_members():return ~Member.id.in_(select(DeletionJob.member_id))

@bp.route('/account/delete',methods=['GET','POST'])
@login_required
def delete_account():
    member=current_member()
    if member.admin:abort(403,'Administrator accounts cannot use member self-deletion.')
    if request.method=='GET':return render_template('delete_account.html')
    rate_limit('delete-account',5,member.id)
    if not check_password_hash(member.password_hash,request.form.get('password','')):abort(400,'Your password was not accepted. No deletion was requested.')
    if request.form.get('confirm')!='yes':abort(400,'Confirm that you want to permanently delete your account.')
    with member_lock(member):
        if requested(member.id):abort(409,'Deletion has already been requested.')
        targets=[]
        for a in accounts(member):
            if not a.remote_id and a.state in ('creating','review'):abort(409,'An unfinished server operation needs administrator review before deletion.')
            if a.remote_id:targets.append({'server':a.server_id,'id':a.remote_id,'name':a.remote_name or member.username,'done':False})
        from .importing import linked_inventory
        for identity,server in linked_inventory(member.id):
            if identity.remote_admin or identity.issue:abort(409,'A linked server identity needs administrator review before deletion.')
            if not any(t['server']==server.id and t['id']==identity.remote_id for t in targets):
                targets.append({'server':server.id,'id':identity.remote_id,'name':identity.name,'done':False})
        for target in targets:
            shared=db().scalar(select(Account).where(Account.server_id==target['server'],Account.remote_id==target['id'],Account.member_id!=member.id))
            imported=db().scalar(select(ImportedProfile).join(ImportedIdentity,ImportedIdentity.profile_id==ImportedProfile.id).where(ImportedIdentity.server_id==target['server'],ImportedIdentity.remote_id==target['id'],ImportedProfile.member_id!=None,ImportedProfile.member_id!=member.id))
            if shared or imported:abort(409,'A server identity is associated with another membership; administrator review is required.')
        onboarding=db().get(OnboardingJob,member.id)
        if onboarding and not member.ombi_user_id and onboarding.ombi_state in ('creating','created'):
            abort(409,'An unfinished Ombi operation needs administrator review before deletion.')
        job=DeletionJob(member_id=member.id,email=seal(member.email),targets=seal(json.dumps(targets)),ombi_id=member.ombi_user_id,
            ombi_name=member.ombi_username,ombi_done=not bool(member.ombi_user_id))
        db().add(job);member.disabled=True;member.version+=1
        if onboarding:onboarding.credential='';onboarding.state='deleting'
        password_job=db().get(PasswordJob,member.id)
        if password_job:password_job.credential='';password_job.state='cancelled'
        db().execute(delete(OneTimeToken).where(OneTimeToken.member_id==member.id))
        db().execute(delete(RecoveryCode).where(RecoveryCode.member_id==member.id))
        audit('Member confirmed permanent account deletion');db().commit()
    session.clear();g.member=None
    return render_template('deletion_requested.html'),202

def stripe_cancel(member):
    from .billing import api,ident,verified_subscription
    if not (member.stripe_customer or member.stripe_subscription or member.checkout_id):return
    remote=api();ids=set()
    if member.checkout_id:
        path='/checkout/sessions/'+quote(member.checkout_id,safe='')
        checkout=remote.get(path)
        if checkout.get('client_reference_id')!=member.id or ident(checkout.get('customer'))!=member.stripe_customer:
            raise APIError('Stripe checkout ownership requires review.')
        if checkout.get('status')=='open':
            remote.post(path+'/expire',{},'delete-expire-'+member.checkout_id)
            checkout=remote.get(path)
        if checkout.get('status') not in ('expired','complete'):raise APIError('Stripe checkout is not closed yet.')
        if ident(checkout.get('subscription')):ids.add(ident(checkout['subscription']))
    if member.stripe_subscription:ids.add(member.stripe_subscription)
    if member.stripe_customer:
        cursor=''
        while True:
            page=remote.get('/subscriptions?'+urlencode({'customer':member.stripe_customer,'status':'all','limit':100,**({'starting_after':cursor} if cursor else {})}))
            if not isinstance(page.get('data'),list):raise APIError('Stripe subscription list was invalid.')
            for sub in page.get('data',[]):
                if sub.get('metadata',{}).get('member_id')==member.id:ids.add(sub['id'])
            if not page.get('has_more'):break
            if not page.get('data'):raise APIError('Stripe subscription list was incomplete.')
            cursor=page['data'][-1]['id']
    for sid in ids:
        sub=verified_subscription(member,sid)
        if sub.get('status') not in ('canceled','incomplete_expired'):
            remote.delete('/subscriptions/'+quote(sid,safe=''),{'invoice_now':'false','prorate':'false'},'delete-member-'+sid)
            sub=verified_subscription(member,sid)
        if sub.get('status') not in ('canceled','incomplete_expired'):raise APIError('Stripe cancellation has not been confirmed.')
    member.checkout_url='';member.cancel_pending=True;db().commit()

def paypal_cancel(member):
    from . import paypal
    remote=None
    for row in db().scalars(select(PayPalSubscription).where(PayPalSubscription.member_id==member.id)).all():
        if not row.remote_id:
            if row.status not in paypal.TERMINAL:raise APIError('An unfinished PayPal checkout requires administrator reconciliation.')
            continue
        if row.status in paypal.TERMINAL:continue
        remote=remote or paypal.api();sub=paypal.verified(row,remote)
        if sub.get('status') not in paypal.TERMINAL:
            remote.post('/v1/billing/subscriptions/'+quote(row.remote_id,safe='')+'/cancel',{'reason':'Member requested permanent account deletion'},'delete-'+row.id)
            sub=paypal.verified(row,remote)
        if sub.get('status') not in paypal.TERMINAL:raise APIError('PayPal cancellation has not been confirmed.')
        row.status=sub['status'];row.approval_url='';db().commit()

def remove_emby(job):
    from .importing import import_lock
    with import_lock():_remove_emby(job)

def _remove_emby(job):
    targets=json.loads(unseal(job.targets)) if job.targets else []
    errors=[]
    for target in targets:
        if target['done']:continue
        try:
            server=db().get(Server,target['server'])
            if not server:raise APIError('A linked Emby server configuration is missing.')
            api=client(server);users=api.users()
            if not isinstance(users,list):raise APIError('Emby identity list needs review.')
            matches=[u for u in users if str(u.get('Id'))==target['id']]
            if matches:
                user=matches[0]
                if (user.get('Name') or '').casefold()!=target['name'].casefold() or user.get('Policy',{}).get('IsAdministrator') is not False:
                    raise APIError('An Emby identity changed or is an administrator; deletion was stopped for review.')
                if user.get('ConnectUserId') or user.get('ConnectUserName'):api.unlink(target['id'])
                api.delete(target['id'])
                if any(str(u.get('Id'))==target['id'] for u in api.users()):raise APIError('Emby has not confirmed account removal.')
            target['done']=True;job.targets=seal(json.dumps(targets));db().commit()
        except APIError as exc:errors.append(str(exc))
    if errors:raise APIError(' '.join(errors)[:400])
    job.emby_done=True;db().commit()

def remove_ombi(job):
    if not job.ombi_id:job.ombi_done=True;return
    remote=current_app.config.get('OMBI_FACTORY',OmbiAPI)()
    matches=[u for u in remote.users() if str(u.get('id'))==job.ombi_id]
    if matches:
        user=matches[0]
        if (user.get('userName') or '').casefold()!=job.ombi_name.casefold() or any(c.get('enabled') and c.get('value','').lower() in ('admin','poweruser') for c in (user.get('claims') or [])):
            raise APIError('An Ombi identity changed or is an administrator; deletion was stopped for review.')
        remote.delete(job.ombi_id)
        if any(str(u.get('id'))==job.ombi_id for u in remote.users()):raise APIError('Ombi has not confirmed account removal.')
    job.ombi_done=True;db().commit()

def erase_member(member,job):
    original_email=member.email
    for model in (AccountPermission,Account,OnboardingJob,OneTimeToken,RecoveryCode,MemberPlan):
        db().execute(delete(model).where(model.member_id==member.id))
    # Remove linked inventory plus any discovery copies of our known remote IDs.
    targets=json.loads(unseal(job.targets)) if job.targets else []
    profile_ids=set()
    for target in targets:
        identity=db().get(ImportedIdentity,(target['server'],target['id']))
        if identity:profile_ids.add(identity.profile_id)
        db().execute(delete(ImportedIdentity).where(ImportedIdentity.server_id==target['server'],ImportedIdentity.remote_id==target['id']))
    for profile in db().scalars(select(ImportedProfile).where(ImportedProfile.member_id==member.id)).all():
        db().execute(delete(ImportedIdentity).where(ImportedIdentity.profile_id==profile.id));db().delete(profile)
    for profile in db().scalars(select(ImportedProfile).where(ImportedProfile.id.in_(profile_ids),~ImportedProfile.id.in_(select(ImportedIdentity.profile_id)))).all():db().delete(profile)
    db().execute(update(PayPalSubscription).where(PayPalSubscription.member_id==member.id).values(approval_url=''))
    # Retain immutable financial rows and attribution; erase identifying invitation recipients.
    db().execute(update(InviteDispatch).where(InviteDispatch.recipient==original_email).values(recipient=''))
    db().execute(update(Invite).where(Invite.recipient==original_email).values(recipient='',revoked=True))
    member.email='deleted-'+member.id+'@deleted.invalid';member.username='Deleted member';member.username_key='deleted-'+member.id.replace('-','')
    member.password_hash='';member.mfa_secret='';member.mfa_pending='';member.mfa_enabled=False
    member.connect_email='';member.ombi_token='';member.ombi_user_id='';member.ombi_username=''
    member.checkout_url='';member.disabled=True;member.verified=False;member.paid_through=0;member.billing_status='deleted';member.cancel_pending=True
    # Billing IDs are retained only to reconcile late receipts/refunds to the existing ledger.
    job.ombi_id='';job.ombi_name='';job.targets='';job.erased=True;db().commit()

def tick():
    for job in db().scalars(select(DeletionJob).where(DeletionJob.state!='done',DeletionJob.next_attempt<=time.time()).limit(25)).all():
        member=db().get(Member,job.member_id)
        if not member or member.operation_lock:continue
        try:
            with member_lock(member):
                errors=[]
                for field,operation in [('stripe_done',lambda:stripe_cancel(member)),('paypal_done',lambda:paypal_cancel(member)),('emby_done',lambda:remove_emby(job)),('ombi_done',lambda:remove_ombi(job))]:
                    if getattr(job,field):continue
                    try:operation();setattr(job,field,True);db().commit()
                    except APIError as exc:errors.append(str(exc))
                if job.stripe_done and job.paypal_done and job.emby_done and job.ombi_done:
                    if not job.erased:erase_member(member,job)
                    if not job.email_sent:
                        try:
                            send_mail(unseal(job.email),'Your MPS account has been removed',
                                'Your MPS account and its linked Ombi and Emby server accounts have been removed. Your MPS subscriptions have been cancelled.\n\nYour global Emby Connect account is not deleted by MPS. Contact Emby if you also want that separate account removed.\n\nHistorical payment and administrator revenue records are retained without your member profile.',
                                heading='Your account has been removed.',intro='Your MPS membership, linked Ombi account and linked Emby server accounts have been removed. Your MPS subscriptions have been cancelled.',
                                note='Your separate global Emby Connect account is not deleted by MPS. Historical payment and administrator revenue records are retained without your member profile.')
                            job.email_sent=True;job.email='';db().commit()
                        except APIError:errors.append('Removal completed; confirmation email is awaiting SMTP retry.')
                job.state='done' if job.erased and job.email_sent else 'retry'
                job.error=' '.join(errors)[:500];job.attempts+=1;job.next_attempt=time.time()+min(3600,30*2**min(job.attempts,7));db().commit()
        except Exception:
            db().rollback();job=db().get(DeletionJob,member.id);job.state='retry';job.error='Account removal needs administrator attention.';job.next_attempt=time.time()+300;db().commit()

@bp.get('/admin/deletions')
@admin_required
def queue():
    return render_template('deletion_queue.html',jobs=db().scalars(select(DeletionJob).where(DeletionJob.state!='done').order_by(DeletionJob.created)).all())

@bp.post('/admin/deletions/<mid>/retry')
@admin_required
def retry(mid):
    job=db().get(DeletionJob,mid)
    if not job or job.state=='done':abort(404)
    job.next_attempt=0;db().commit()
    from flask import redirect
    return redirect('/admin/deletions')
