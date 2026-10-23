import json,re,secrets,time
from decimal import Decimal,InvalidOperation
from urllib.parse import urlsplit
from flask import Blueprint,abort,current_app,flash,redirect,render_template,request,url_for
from sqlalchemy import select,update
from .core import *
from .models import InviteDeletion,Server,Invite,InviteServer,Member,Account,Plan,Event,Audit,InvitePermission,AccountPermission
from .emby import Emby,APIError
from .provision import client,sync_access
from .integrations import OmbiAPI,stripe_key_mode
from .mail import send_mail
from .billing import api as stripe_api
from .models import InviteAttribution,InviteDispatch,InvitePlan,PayPalPlan,PayPalSubscription,PayPalEvent
from . import paypal
from .deletion import visible_members,requested
bp=Blueprint('admin',__name__,url_prefix='/admin')

def dashboard_context():
    people={m.id:m for m in db().scalars(select(Member).where(visible_members())).all()}
    return dict(people=people,admins=[m for m in people.values() if m.admin and m.verified and not m.disabled],
        invite_plans={i.invite_id:db().get(Plan,i.plan_id) for i in db().scalars(select(InvitePlan)).all()},
        attribution={a.invite_id:a for a in db().scalars(select(InviteAttribution)).all()},
        paypal_plans={p.plan_id for p in db().scalars(select(PayPalPlan).where(PayPalPlan.live==paypal.live_mode())).all()},servers=db().scalars(select(Server).order_by(Server.name)).all(),
        invites=db().scalars(select(Invite).where(~Invite.id.in_(select(InviteDeletion.invite_id))).order_by(Invite.created.desc()).limit(100)).all(),
        members=db().scalars(select(Member).where(visible_members()).order_by(Member.created.desc()).limit(100)).all(),
        plans=db().scalars(select(Plan).order_by(Plan.created.desc())).all(),
        events=db().scalars(select(Event).where(Event.state!='done').limit(20)).all()+db().scalars(select(PayPalEvent).where(PayPalEvent.state!='done').limit(20)).all(),
        activity=db().scalars(select(Audit).order_by(Audit.created.desc()).limit(12)).all(),
        worker_seen=float(setting('worker_seen','0')))

@bp.get('')
@admin_required
def dashboard():
    return render_template('admin.html',**dashboard_context())

@bp.get('/invitations')
@admin_required
def invitations():
    return render_template('invitations.html',**dashboard_context())

@bp.get('/activity')
@admin_required
def activity():
    return render_template('activity.html',activity=db().scalars(select(Audit).order_by(Audit.created.desc()).limit(100)).all())

@bp.post('/servers')
@admin_required
def add_server():
    name=request.form.get('name','').strip();url=request.form.get('url','').strip().rstrip('/');key=request.form.get('key','').strip()
    if not name or len(name)>80 or not key or len(url)>350:abort(400,'Server name, URL and API key are required.')
    api=current_app.config.get('EMBY_FACTORY',Emby)(url,key);libraries=api.libraries();api.users()
    db().add(Server(name=name,url=url,key=seal(key),libraries=json.dumps(libraries)));audit('Connected Emby server');db().commit()
    return redirect(url_for('admin.settings',category='emby',_anchor='servers'))

@bp.post('/servers/<int:sid>/refresh')
@admin_required
def refresh(sid):
    server=db().get(Server,sid)
    if not server:abort(404)
    name=request.form.get('name',server.name).strip()
    url=request.form.get('url',server.url).strip().rstrip('/')
    key=request.form.get('key','').strip()
    try:parsed=urlsplit(url);valid_url=parsed.scheme in ('http','https') and parsed.hostname and not (parsed.username or parsed.password or parsed.query or parsed.fragment);parsed.port
    except ValueError:valid_url=False
    if not name or len(name)>80 or len(url)>350 or not valid_url:abort(400,'Enter a name and a valid HTTP(S) server URL without credentials, query or fragment.')
    api=current_app.config.get('EMBY_FACTORY',Emby)(url,key or unseal(server.key))
    libraries=api.libraries();users=api.users()
    if url!=server.url:
        linked_ids=set(db().scalars(select(Account.remote_id).where(Account.server_id==sid,Account.remote_id!='')))
        if not linked_ids<={str(u.get('Id')) for u in users}:
            abort(400,'This address does not contain the linked member accounts. Use the new address of the same Emby server, or add a separate server.')
    # Commit connection details and the catalogue together only after successful checks.
    server.name=name;server.url=url;server.libraries=json.dumps(libraries)
    if key:server.key=seal(key)
    audit('Updated Emby server details and refreshed library catalogue');db().commit()
    flash('Server saved and libraries rescanned. New libraries are available in member permissions and new invitations. Existing access is unchanged.','success')
    return redirect(url_for('admin.settings',category='emby',_anchor='servers'))

@bp.post('/invites')
@admin_required
def invite():
    label=request.form.get('label','').strip();email=request.form.get('email','').strip().lower()
    try:days=int(request.form.get('days','7'));uses=int(request.form.get('uses','1'));ids=set(map(int,request.form.getlist('servers')))
    except ValueError:abort(400,'Invalid invite settings.')
    if not label or len(label)>120 or not 1<=days<=365 or not 1<=uses<=1000 or not 1<=len(ids)<=10:abort(400,'Check label, expiry, places and server selections.')
    if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):abort(400,'Invalid recipient email.')
    if email and uses!=1:abort(400,'Email-bound invitations must have one place.')
    selections=[]
    for sid in ids:
        server=db().get(Server,sid);folders=sorted(set(request.form.getlist(f'folders_{sid}')))
        if not server or (not folders and request.form.get(f'live_tv_{sid}')!='yes') or not set(folders)<={x['id'] for x in json.loads(server.libraries)}:abort(400,'Select valid libraries for every selected server.')
        selections.append((sid,folders))
    chosen_plan=request.form.get('plan_id','').strip()
    selected=db().get(Plan,chosen_plan) if chosen_plan else None
    if chosen_plan and (not selected or not selected.active):abort(400,'Choose an available monthly plan or no monthly plan.')
    paid=bool(selected) if 'plan_id' in request.form else request.form.get('paid')=='yes'
    owner=db().get(Member,request.form.get('attributed_admin') or current_member().id)
    if not owner or not owner.admin or owner.disabled or not owner.verified:abort(400,'Choose an active administrator for this invitation.')
    token=secrets.token_urlsafe(32)
    row=Invite(digest=digest(token),encrypted_token=seal(token),label=label,recipient=email,expires=time.time()+days*86400,max_uses=uses,requires_payment=paid)
    db().add(row);db().flush()
    if selected:db().add(InvitePlan(invite_id=row.id,plan_id=selected.id))
    db().add(InviteAttribution(invite_id=row.id,admin_id=owner.id,created_by=current_member().id))
    dispatch=InviteDispatch(invite_id=row.id,actor_id=current_member().id,recipient=email,action='create',status='pending' if email else 'link_created');db().add(dispatch)
    for sid,folders in selections:
        db().add(InviteServer(invite_id=row.id,server_id=sid,folders=json.dumps(folders)))
        db().add(InvitePermission(invite_id=row.id,server_id=sid,live_tv=request.form.get(f'live_tv_{sid}')=='yes'))
    audit('Created invitation');db().commit()
    membership_text=(f'{selected.name}: £{selected.amount/100:.2f} per month.' if selected else ('A monthly subscription is required.' if paid else 'No monthly plan — complimentary access.'))
    link=current_app.config['PUBLIC_URL']+'/join/'+token
    if email:
        try:
            send_mail(email,'You are invited to MPS',f'You are invited to {label}.\n\n{membership_text}\n\nAccept your invitation:\n{link}\n\nThis invitation expires in {days} days.\nMPS', heading="You're invited.", intro='Your invitation to MPS is ready. Create your account to get started. '+membership_text, action_url=link, action_label='Accept invitation', note=f'This invitation expires in {days} days. Only accept if you were expecting this invitation.')
            row.email_status='sent';dispatch.status='accepted_by_smtp'
        except APIError:row.email_status='failed';dispatch.status='failed';flash('Invitation saved, but email delivery failed. Check SMTP and resend.','error')
        db().commit()
    return render_template('invite_created.html',link=link,invite=row)

@bp.get('/invites/<iid>/delete')
@admin_required
def confirm_invite_delete(iid):
    invite=db().get(Invite,iid)
    if not invite or db().get(InviteDeletion,iid):abort(404)
    return render_template('invite_delete.html',invite=invite)

@bp.post('/invites/<iid>/<action>')
@admin_required
def invite_action(iid,action):
    invite=db().get(Invite,iid)
    if not invite:abort(404)
    if action=='delete':
        # Lock the invitation before adding its tombstone; concurrent signup uses
        # the same row and requires revoked=False. Retain financial/member FKs.
        db().execute(update(Invite).where(Invite.id==iid).values(revoked=True,encrypted_token=''))
        if not db().get(InviteDeletion,iid):
            db().add(InviteDeletion(invite_id=iid));audit('Deleted invitation')
        db().commit()
        return redirect(url_for('admin.invitations'))
    if db().get(InviteDeletion,iid):abort(404)
    if action=='revoke':invite.revoked=True;audit('Revoked invitation');db().commit()
    elif action=='resend':
        if not invite.recipient or invite.revoked or invite.expires<time.time():abort(400,'Only an active email-bound invitation can be resent.')
        rate_limit('resend-invite',3,iid)
        link=current_app.config['PUBLIC_URL']+'/join/'+unseal(invite.encrypted_token)
        dispatch=InviteDispatch(invite_id=invite.id,actor_id=current_member().id,recipient=invite.recipient,action='resend',status='pending');db().add(dispatch);db().commit()
        try:send_mail(invite.recipient,'Your MPS invitation','Accept your invitation:\n'+link, heading="Your invitation is waiting.", intro='Join MPS and bring your entertainment together in one place.', action_url=link, action_label='Accept invitation', note='This is a reminder of your existing invitation; its original expiry still applies.');invite.email_status='sent';dispatch.status='accepted_by_smtp';db().commit()
        except APIError:invite.email_status='failed';dispatch.status='failed';db().commit();raise
    else:abort(404)
    return redirect(url_for('admin.invitations'))

@bp.route('/settings',methods=['GET','POST'])
@bp.route('/settings/<category>',methods=['GET','POST'])
@admin_required
def settings(category=None):
    categories = {
        'emby': ('Emby servers', 'Connect your servers and refresh their libraries.'),
        'services': ('Connected services', 'Track requests through download, import and Emby availability.'),
        'smtp': ('SMTP settings', 'Manage outgoing email and request availability notifications.'),
        'payments': ('Payment settings', 'Configure Stripe and PayPal subscriptions.'),
        'ombi': ('Ombi settings', 'Connect your member request service.'),
        'system': ('System information', 'Site details, hosting and account policies.'),
    }
    if category is not None and category not in categories:abort(404)
    if request.method == 'GET' and category is None:
        return redirect(url_for('admin.settings', category='emby'))
    if request.method=='POST':
        section=request.form.get('section')
        if section=='smtp':
            host=request.form.get('smtp_host','').strip();sender=request.form.get('smtp_from','').strip();mode=request.form.get('smtp_mode')
            try:port=int(request.form.get('smtp_port','587'))
            except ValueError:abort(400,'Invalid SMTP port.')
            if not host or '\n' in host or not 1<=port<=65535 or mode not in ('ssl','starttls') or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',sender):abort(400,'Check SMTP settings. TLS is required.')
            if host.lower().endswith('.smtp2go.com') and ((mode=='ssl' and port in (25,2525,8025,587,80)) or (mode=='starttls' and port in (465,8465,443))):abort(400,'SMTP2GO requires STARTTLS on ports 25, 2525, 8025, 587 or 80; SSL uses 465, 8465 or 443.')
            for key,value in {'smtp_host':host,'smtp_port':port,'smtp_from':sender,'smtp_mode':mode,'smtp_username':request.form.get('smtp_username','').strip()}.items():put(key,value)
            if request.form.get('smtp_password'):put('smtp_password',request.form['smtp_password'],secret=True)
        elif section=='stripe':
            secret=request.form.get('stripe_secret','').strip();webhook=request.form.get('stripe_webhook','').strip()
            if secret and stripe_key_mode(secret) is None:abort(400,'Use a Stripe secret or restricted API key (sk_live_, rk_live_, sk_test_ or rk_test_).')
            if webhook and not webhook.startswith('whsec_'):abort(400,'Use the webhook endpoint signing secret.')
            if secret and setting('stripe_secret',secret=True) and stripe_key_mode(secret)!=stripe_key_mode(setting('stripe_secret',secret=True)) and db().scalar(select(Member).where(Member.stripe_customer!='')):
                abort(400,'Switching Stripe environments requires a separate deployment/database once customers exist.')
            if secret:put('stripe_secret',secret,secret=True)
            if webhook:put('stripe_webhook',webhook,secret=True)
            enabled=request.form.get('billing_enabled')=='yes'
            if enabled and (not (secret or setting('stripe_secret',secret=True)) or not (webhook or setting('stripe_webhook',secret=True))):abort(400,'Stripe API key and webhook signing secret are required.')
            if enabled and time.time()-float(setting('worker_seen','0'))>120:abort(400,'Start the background worker before enabling billing.')
            put('billing_enabled','1' if enabled else '0')
        elif section=='paypal':
            mode=request.form.get('paypal_mode','sandbox');client=request.form.get('paypal_client_id','').strip();secret=request.form.get('paypal_secret','').strip();hook=request.form.get('paypal_webhook_id','').strip()
            if mode not in ('live','sandbox') or len(client)>250 or len(hook)>120:abort(400,'Invalid PayPal configuration.')
            used=db().scalar(select(PayPalSubscription).limit(1)) or db().scalar(select(PayPalPlan).limit(1))
            if used and (mode!=setting('paypal_mode','sandbox') or (client and client!=setting('paypal_client_id'))):abort(400,'PayPal environment and merchant app cannot change after plans or checkout records exist. Use a separate sandbox installation.')
            put('paypal_mode',mode)
            if client:put('paypal_client_id',client)
            if secret:put('paypal_secret',secret,secret=True)
            if hook:put('paypal_webhook_id',hook)
            enabled=request.form.get('paypal_enabled')=='yes'
            if enabled:
                if not setting('paypal_client_id') or not setting('paypal_secret',secret=True) or not setting('paypal_webhook_id'):abort(400,'Client ID, secret and webhook ID are required.')
                if time.time()-float(setting('worker_seen','0'))>120:abort(400,'Start the background worker before enabling PayPal.')
                remote=paypal.api();details=remote.get('/v1/notifications/webhooks/'+setting('paypal_webhook_id'))
                if details.get('url')!=current_app.config['PUBLIC_URL']+'/paypal/webhook':abort(400,'The PayPal webhook URL must match this site.')
                configured={e['name'] for e in details.get('event_types',[])}
                if '*' not in configured and not paypal.EVENTS<=configured:abort(400,'Subscribe the PayPal webhook to every event listed below.')
            put('paypal_enabled','1' if enabled else '0')
        elif section=='ombi':
            url=request.form.get('ombi_url','').strip().rstrip('/');parsed=urlsplit(url)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:abort(400,'Enter the Ombi base URL without credentials or query parameters.')
            if setting('ombi_url') and setting('ombi_url')!=url and db().scalar(select(Member).where(Member.ombi_user_id!='')):abort(400,'Unlink existing Ombi memberships before changing the Ombi server.')
            put('ombi_url',url);put('ombi_create','1' if request.form.get('ombi_create')=='yes' else '0')
            if request.form.get('ombi_key'):put('ombi_key',request.form['ombi_key'].strip(),secret=True)
        else:abort(400,'Unknown settings section.')
        audit('Updated '+section+' configuration');db().commit();flash('Configuration saved. Secret values are not displayed.','success')
        return redirect(url_for('admin.settings',category={'stripe':'payments','paypal':'payments','smtp':'smtp','ombi':'ombi'}[section]))
    return render_template('settings.html',settings_page=category,settings_title=categories[category][0],settings_description=categories[category][1],servers=db().scalars(select(Server).order_by(Server.name)).all())

@bp.post('/smtp/test')
@admin_required
def smtp_test():
    rate_limit('smtp-test',5,current_member().id)
    try:
        send_mail(current_member().email,'MPS SMTP test','Your MPS SMTP connection is working.', heading='Looking good.', intro='Your MPS email connection is working. This is how your branded account emails will look.', action_url=current_app.config['PUBLIC_URL']+'/admin', action_label='Open admin dashboard', note='Test email requested from your administrator settings.')
        flash('SMTP server accepted the test email for your administrator address. Check your inbox and spam folder.','success')
    except APIError as error:
        flash(str(error),'error')
    return redirect(url_for('admin.settings',category='smtp'),303)

@bp.post('/plans')
@admin_required
def plan():
    from .plans import values,queue
    name,amount=values()
    providers=request.form.getlist('providers')
    if not set(providers)<={'stripe','paypal'}:abort(400,'Invalid payment provider.')
    plan=Plan(name=name,amount=amount,currency='gbp',active=True);db().add(plan);db().commit()
    for provider in providers:queue(plan,provider)
    audit('Administrator saved a monthly plan');db().commit()
    flash('Monthly plan saved. Any selected provider publication is queued; no payment service is needed to save pricing.','success')
    return redirect(url_for('plans.page'),303)

@bp.post('/plans/<pid>/archive')
@admin_required
def archive_plan(pid):
    plan=db().get(Plan,pid)
    if not plan:abort(404)
    plan.active=False;db().commit();return redirect(url_for('plans.page'),303)

@bp.get('/members')
@admin_required
def members():
    return render_template('members.html',members=db().scalars(select(Member).where(visible_members()).order_by(Member.created.desc())).all())

@bp.get('/members/<mid>')
@admin_required
def member_detail(mid):
    member=db().get(Member,mid)
    if not member or requested(member.id):abort(404)
    rows=db().execute(select(Account,Server).join(Server,Server.id==Account.server_id).where(Account.member_id==mid)).all()
    from .importing import linked_inventory
    return render_template('member_detail.html',member=member,accounts=rows,permissions={p.server_id:p for p in db().scalars(select(AccountPermission).where(AccountPermission.member_id==mid))},imported_accounts=linked_inventory(member.id))

@bp.post('/members/<mid>/permissions/<int:sid>')
@admin_required
def member_permissions(mid,sid):
    member=db().get(Member,mid);row=db().get(Account,(mid,sid));server=db().get(Server,sid)
    if not member or member.admin or requested(member.id) or not row or not server:abort(404)
    folders=sorted(set(request.form.getlist('folders')))
    enabled=request.form.get('enabled')=='yes';live_tv=request.form.get('live_tv')=='yes'
    with member_lock(member):
        # Fresh IDs on grant/edit; disabling must still be saveable during an outage.
        if enabled:
            try:libraries=client(server).libraries()
            except APIError:libraries=json.loads(server.libraries)
            if not set(folders)<={x['id'] for x in libraries}:abort(400,'A selected library is unavailable. Refresh and try again.')
            server.libraries=json.dumps(libraries)
        elif not set(folders)<={x['id'] for x in json.loads(server.libraries)}:abort(400,'Invalid library selection.')
        p=db().get(AccountPermission,(mid,sid))
        if not p:p=AccountPermission(member_id=mid,server_id=sid);db().add(p)
        p.enabled=enabled;p.live_tv=live_tv;p.pending=True;row.folders=json.dumps(folders)
        audit('Administrator updated server, library and Live TV permissions');db().commit()
        sync_access(member)
    flash('Permissions saved. Any pending server update will retry automatically.','success')
    return redirect(url_for('admin.member_detail',mid=mid))

@bp.post('/members/<mid>/access')
@admin_required
def member_access(mid):
    member=db().get(Member,mid)
    if not member or member.admin or requested(member.id):abort(400,'Administrators cannot be suspended through this control.')
    with member_lock(member):
        member.disabled=request.form.get('action')=='disable';member.version+=1;db().commit();sync_access(member)
    audit('Administrator updated member access');db().commit();return redirect(url_for('admin.member_detail',mid=mid))

@bp.post('/plans/<pid>/paypal')
@admin_required
def publish_paypal(pid):
    plan=db().get(Plan,pid)
    if not plan:abort(404)
    from .plans import queue
    queue(plan,'paypal')
    flash('Matching monthly price queued for PayPal publication.','success')
    return redirect(url_for('plans.page'),303)
