import json,re,time
from flask import Blueprint,abort,current_app,flash,redirect,render_template,request,url_for
from sqlalchemy import select,update
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash,generate_password_hash
from .core import *
from .models import *
from .mail import token_email
from .auth import valid_password,authenticate_session
from .provision import provision,rename_servers,client
from .integrations import OmbiAPI
from .emby import APIError
bp=Blueprint('portal',__name__)

def username_valid(name):return bool(re.fullmatch(r'[A-Za-z0-9_.-]{3,40}',name))

@bp.get('/')
def root():return redirect(url_for('portal.home') if current_member() else url_for('auth.login'))

@bp.get('/member')
@login_required
def home():
    member=current_member()
    if member.admin:return redirect(url_for('admin.dashboard'))
    from .paypal import current_subscription,live_mode
    paypal_plans={p.plan_id for p in db().scalars(select(PayPalPlan).where(PayPalPlan.live==live_mode())).all()}
    assigned=db().get(MemberPlan,member.id)
    chosen=db().get(Plan,assigned.plan_id) if assigned else None
    offered=[chosen] if chosen else db().scalars(select(Plan).where(Plan.active==True)).all()
    failed=member.billing_status in ('payment_failed','past_due','unpaid')
    membership_status=('Active' if not member.requires_payment else 'Payment failed' if failed else
        'Active' if member.paid_through>time.time() else 'Waiting for subscription payment')
    history=[];history_error=False
    if member.ombi_token and member.ombi_user_id:
        try:history=current_app.config.get('OMBI_FACTORY',OmbiAPI)(unseal(member.ombi_token)).request_history(member.ombi_user_id)
        except APIError:history_error=True
    return render_template('member.html',assigned_plan=chosen,paypal_subscription=current_subscription(member),paypal_plans=paypal_plans,
        member=member,plans=offered,membership_status=membership_status,payment_failed=failed,request_history=history,history_error=history_error)

@bp.get('/linked-accounts')
@login_required
def linked_accounts():
    member=current_member()
    if member.admin:return redirect(url_for('admin.dashboard'))
    from .importing import linked_inventory
    rows=db().execute(select(Account,Server).join(Server,Server.id==Account.server_id).where(Account.member_id==member.id)).all()
    return render_template('linked_accounts.html',member=member,accounts=rows,imported_accounts=linked_inventory(member.id))

@bp.route('/join/<token>',methods=['GET','POST'])
def join(token):
    invite=db().scalar(select(Invite).where(Invite.digest==digest(token)))
    if not invite:abort(404,'Invitation not found.')
    if invite.revoked or invite.expires<time.time() or invite.uses>=invite.max_uses:abort(400,'This invitation has expired, been revoked or has no places remaining.')
    targets=db().execute(select(InviteServer,Server).join(Server,Server.id==InviteServer.server_id).where(InviteServer.invite_id==invite.id)).all()
    if request.method=='POST':
        rate_limit('join',10)
        email=request.form.get('email','').strip().lower();name=request.form.get('username','').strip();password=request.form.get('password','')
        if not username_valid(name) or len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):abort(400,'Enter a valid username and email.')
        if not valid_password(password) or password!=request.form.get('confirm') or request.form.get('consent')!='yes':abort(400,'Confirm your password (12–256 characters) and agree to account creation.')
        if invite.recipient and invite.recipient!=email:abort(400,'This invitation belongs to a different email address.')
        if not current_app.config.get('MAIL_SENDER') and not setting('smtp_host'):abort(503,'Email verification is not configured. Ask the administrator to configure SMTP.')
        changed=db().execute(update(Invite).where(Invite.id==invite.id,Invite.revoked==False,Invite.expires>time.time(),Invite.uses<Invite.max_uses).values(uses=Invite.uses+1)).rowcount
        if not changed:db().rollback();abort(409,'This invitation has no places remaining.')
        member=Member(email=email,username=name,username_key=name.casefold(),password_hash=generate_password_hash(password),requires_payment=invite.requires_payment)
        try:
            db().add(member);db().flush()
            selected=db().get(InvitePlan,invite.id)
            if selected:db().add(MemberPlan(member_id=member.id,plan_id=selected.plan_id))
            attribution=db().get(InviteAttribution,invite.id)
            if attribution:db().add(MemberReferral(member_id=member.id,invite_id=invite.id,admin_id=attribution.admin_id))
            for target,server in targets:
                db().add(Account(member_id=member.id,server_id=server.id,invite_id=invite.id,folders=target.folders))
                access=db().get(InvitePermission,(invite.id,server.id))
                db().add(AccountPermission(member_id=member.id,server_id=server.id,enabled=True,live_tv=bool(access and access.live_tv)))
            from .onboarding import enqueue
            enqueue(member,password)
            audit('New member accepted an invitation');db().commit()
        except IntegrityError:db().rollback();abort(409,'An account already uses that email or username. Sign in or ask the administrator for help.')
        authenticate_session(member)
        try:token_email(member,'verify')
        except APIError:flash('Account created, but verification email could not be sent. Use resend after SMTP is repaired.','error')
        return redirect(url_for('portal.home'))
    selected=db().get(InvitePlan,invite.id)
    return render_template('join.html',invite=invite,assigned_plan=db().get(Plan,selected.plan_id) if selected else None,servers=[s for _,s in targets])

@bp.post('/account/provision')
@active_required
def connect():
    member=current_member();rate_limit('provision',5,member.id)
    password=request.form.get('password','');mode=request.form.get('mode')
    if not check_password_hash(member.password_hash,password) or mode not in ('new','existing') or request.form.get('consent')!='yes':abort(400,'Verify your portal password and consent to Connect/server setup.')
    with member_lock(member):provision(member,password,request.form.get('connect_password','') or password,mode)
    flash('Emby accounts configured. Any pending Connect verification is shown below.','success');return redirect(url_for('portal.linked_accounts'))

@bp.post('/account/username')
@login_required
def rename():
    member=current_member();name=request.form.get('username','').strip();rate_limit('rename',5,member.id)
    if not username_valid(name) or not check_password_hash(member.password_hash,request.form.get('password','')):abort(400,'Enter a valid username and your current password.')
    existing=db().scalar(select(Member).where(Member.username_key==name.casefold(),Member.id!=member.id))
    if existing:abort(409,'That username is already in use.')
    with member_lock(member):rename_servers(member,name)
    flash('Your portal and linked Emby server usernames are synchronised.','success');return redirect(url_for('portal.linked_accounts'))

@bp.post('/account/links/refresh')
@login_required
def refresh_links():
    member=current_member()
    with member_lock(member):
        for account in db().scalars(select(Account).where(Account.member_id==member.id,Account.state=='ready',Account.link_pending==True)).all():
            account.link_pending=client(db().get(Server,account.server_id)).link(account.remote_id,member.connect_email);db().commit()
    return redirect(url_for('portal.linked_accounts'))

@bp.route('/requests',methods=['GET','POST'])
@active_required
def requests_page():
    member=current_member();results=[];kind=request.args.get('kind','movie');query=request.args.get('q','').strip()
    factory=current_app.config.get('OMBI_FACTORY',OmbiAPI)
    if request.method=='POST':
        action=request.form.get('action');rate_limit('ombi-action',15,member.id)
        if action in ('link','create'):
            if not check_password_hash(member.password_hash,request.form.get('portal_password','')):abort(400,'Your portal password is incorrect.')
            remote=factory()
            if action=='create':
                if setting('ombi_create','0')!='1':abort(403,'Ombi account creation is not enabled.')
                name=member.username;password=request.form.get('portal_password','');remote.create(name,member.email,password)
            else:name=request.form.get('ombi_username','');password=request.form.get('ombi_password','')
            token,identity=remote.authenticate(name,password)
            if any(c.get('enabled') and c.get('value','').lower() in ('admin','poweruser') for c in identity.get('claims',[])):
                abort(400,'Link a regular Ombi member account, not an administrator account.')
            member.ombi_username=identity['userName'];member.ombi_user_id=identity['id'];member.ombi_token=seal(token);db().commit()
            flash('Ombi linked. You can now search and request here without leaving StreamingHome.','success')
        elif action=='unlink':member.ombi_token='';member.ombi_username='';member.ombi_user_id='';db().commit()
        elif action=='request':
            if not member.ombi_token:abort(400,'Link your Ombi account first.')
            kind=request.form.get('kind')
            try:media_id=int(request.form.get('media_id','0'))
            except ValueError:abort(400,'Invalid media ID.')
            if kind not in ('movie','tv') or media_id<=0:abort(400,'Invalid media request.')
            remote=factory(unseal(member.ombi_token));remote.make_request(kind,media_id)
            flash('Request sent to Ombi using your account and its request permissions.','success')
        else:abort(400,'Unknown action.')
        return redirect(url_for('portal.requests_page'))
    if query:
        if not member.ombi_token:abort(400,'Link your Ombi account first.')
        if kind not in ('movie','tv') or len(query)>100:abort(400,'Invalid search.')
        rate_limit('ombi-search',60,member.id)
        results=factory(unseal(member.ombi_token)).search(kind,query)
    return render_template('requests.html',member=member,results=results,kind=kind,query=query)
