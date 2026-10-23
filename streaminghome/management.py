import time,secrets,re
from flask import Blueprint,request,abort,render_template,redirect,url_for,flash,current_app,session
from sqlalchemy import select,delete
from werkzeug.security import check_password_hash,generate_password_hash
from .core import db,current_member,admin_required,rate_limit,audit,digest
from .models import Member,ImportedProfile,ImportedIdentity,OneTimeToken
from .auth import verify_factor,valid_password,authenticate_session
from .portal import username_valid
from .importing import import_users,inventory,linked_inventory,import_lock
from .mail import send_mail
from .emby import APIError
from .deletion import visible_members,requested
bp=Blueprint('management',__name__)

def reauthenticate(factor=False):
    member=current_member();rate_limit('admin-sensitive',10,member.id)
    if not check_password_hash(member.password_hash,request.form.get('password','')):abort(400,'Your current administrator password is required.')
    if factor and not verify_factor(member,request.form.get('code','')):abort(400,'Enter a fresh authenticator code or an unused recovery code.')

def activation(member):
    db().execute(delete(OneTimeToken).where(OneTimeToken.member_id==member.id,OneTimeToken.purpose=='activate'))
    token=secrets.token_urlsafe(32);db().add(OneTimeToken(digest=digest(token),member_id=member.id,purpose='activate',expires=time.time()+86400));db().commit()
    link=current_app.config['PUBLIC_URL']+'/activate/'+token
    sent=False
    if request.form.get('send_email')=='yes':
        try:send_mail(member.email,'Set up your StreamingHome account','Set your password using this single-use link within 24 hours:\n'+link+'\nAdministrator accounts must then enrol MFA.', heading='Make yourself at home.', intro='Your StreamingHome account is ready to set up. Choose your password to get started.', action_url=link, action_label='Set up your account', note='This single-use link expires in 24 hours. Administrator accounts must also set up multi-factor authentication.');sent=True
        except APIError:flash('Account and setup link saved, but email delivery failed. Share the link privately or configure SMTP.','error')
    return render_template('activation_created.html',member=member,link=link,sent=sent)

def new_member(admin=False):
    email=request.form.get('email','').strip().lower();name=request.form.get('username','').strip()
    if not username_valid(name) or len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):abort(400,'Enter a valid email and a 3–40 character username using letters, numbers, dots, hyphens or underscores.')
    if db().scalar(select(Member).where((Member.email==email)|(Member.username_key==name.casefold()))):abort(409,'That email or username already exists. Link or promote the existing member instead.')
    member=Member(email=email,username=name,username_key=name.casefold(),password_hash=generate_password_hash(secrets.token_urlsafe(48)),admin=admin,verified=False)
    db().add(member);db().flush();return member

@bp.route('/admin/administrators',methods=['GET','POST'])
@admin_required
def administrators():
    if request.method=='POST':
        reauthenticate(factor=True)
        action=request.form.get('action')
        if action=='create':
            member=new_member(admin=True);audit('Administrator created an additional admin requiring password setup and MFA');db().commit();return activation(member)
        member=db().get(Member,request.form.get('member_id',''))
        if not member or member.disabled or requested(member.id):abort(400,'Select an enabled account.')
        if action=='promote':
            if member.admin or not member.verified:abort(400,'Select an existing verified non-admin member.')
            member.admin=True;member.version+=1;audit('Administrator promoted a verified member; MFA remains mandatory');db().commit();flash('Administrator role granted. The member must sign in again and enable MFA before accessing administration.','success')
        elif action=='resend':
            if not member.admin or member.verified:abort(400,'Only pending administrators can receive a new setup link.')
            return activation(member)
        else:abort(400,'Unknown administrator action.')
        return redirect(url_for('management.administrators'))
    members=db().scalars(select(Member).where(visible_members()).order_by(Member.username)).all()
    return render_template('administrators.html',admins=[m for m in members if m.admin],members=[m for m in members if not m.admin and m.verified and not m.disabled])

@bp.route('/activate/<token>',methods=['GET','POST'])
def activate(token):
    item=db().get(OneTimeToken,digest(token))
    if not item or item.purpose!='activate' or item.expires<time.time():abort(400,'This setup link is invalid or expired. Ask an administrator for a new one.')
    member=db().get(Member,item.member_id)
    if not member or member.disabled or member.verified:abort(400,'This setup link is no longer available.')
    if request.method=='POST':
        rate_limit('activate',10);password=request.form.get('password','')
        if not valid_password(password) or password!=request.form.get('confirm'):abort(400,'Passwords must match and contain 12–256 characters.')
        used=db().execute(delete(OneTimeToken).where(OneTimeToken.digest==digest(token),OneTimeToken.expires>time.time())).rowcount
        if not used:db().rollback();abort(409,'This setup link was already used.')
        member.password_hash=generate_password_hash(password);member.verified=True;member.version+=1
        from .onboarding import enqueue
        enqueue(member,password)
        audit('Account setup completed');db().commit();authenticate_session(member)
        return redirect(url_for('auth.security') if member.admin else url_for('portal.home'))
    return render_template('activate.html',member=member)

@bp.get('/admin/imports')
@admin_required
def imports():
    profiles,rows,suggestions=inventory()
    members=db().scalars(select(Member).where(visible_members()).order_by(Member.username)).all()
    return render_template('imports.html',profiles=profiles,rows=rows,suggestions=suggestions,members=members,member_map={m.id:m for m in members})

@bp.post('/admin/imports/refresh')
@admin_required
def refresh():
    rate_limit('import',10,current_member().id);result=import_users()
    flash(f"Imported/refreshed {result['accounts']} server accounts into {result['profiles']} identity groups; {result['review']} need review. No Emby accounts were changed.",'success')
    return redirect(url_for('management.imports'))

@bp.post('/admin/imports/<pid>/link')
@admin_required
def link(pid):
    reauthenticate()
    with import_lock():
        profile=db().get(ImportedProfile,pid)
        if not profile:abort(404)
        if profile.member_id:abort(409,'This identity group is already linked. It cannot be silently reassigned.')
        identities=db().scalars(select(ImportedIdentity).where(ImportedIdentity.profile_id==pid)).all()
        if not identities or any(i.issue or not i.present for i in identities):abort(409,'Resolve missing or conflicting identities before linking this group.')
        if len({i.server_id for i in identities})!=len(identities):abort(409,'Duplicate accounts on one server require review.')
        if request.form.get('action')=='create':member=new_member(admin=False)
        else:
            member=db().get(Member,request.form.get('member_id',''))
            if not member or member.disabled or requested(member.id):abort(400,'Select an enabled portal account.')
        existing=linked_inventory(member.id)
        if {r.server_id for r,_ in existing}&{r.server_id for r in identities}:abort(409,'This portal member already has an imported account on one of these servers.')
        profile.member_id=member.id;audit('Administrator associated imported Emby identities with a portal member; remote access unchanged');db().commit()
        if request.form.get('action')=='create':return activation(member)
    flash('Existing identities linked to the portal member. Emby permissions, passwords and Connect links are unchanged.','success')
    return redirect(url_for('management.imports'))

@bp.post('/admin/imports/<pid>/setup')
@admin_required
def setup(pid):
    reauthenticate();profile=db().get(ImportedProfile,pid)
    member=db().get(Member,profile.member_id) if profile and profile.member_id else None
    if not member or member.verified or member.disabled:abort(400,'Only pending imported members can receive a new setup link.')
    return activation(member)
