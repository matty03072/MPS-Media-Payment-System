import io, secrets, time, re
import pyotp
import qrcode.image.svg
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for, Response
from sqlalchemy import select, update, delete
from werkzeug.security import check_password_hash,generate_password_hash
from .core import *
from .models import RecoveryCode,OneTimeToken
from .mail import token_email
from .emby import APIError
bp=Blueprint('auth',__name__)

def valid_password(value): return 12<=len(value)<=256

def authenticate_session(member,mfa=False):
    session.clear();session.update(uid=member.id,version=member.version,mfa_ok=mfa,csrf=secrets.token_urlsafe(32));session.permanent=True

def verify_factor(member,code):
    code=code.strip().replace(' ','')
    if re.fullmatch(r'\d{6}',code):
        totp=pyotp.TOTP(unseal(member.mfa_secret));counter=int(time.time()//30)
        for step in (counter,counter-1,counter+1):
            if secrets.compare_digest(totp.at(step*30),code):
                changed=db().execute(update(Member).where(Member.id==member.id,Member.mfa_counter<step).values(mfa_counter=step)).rowcount
                db().commit();return bool(changed)
        return False
    changed=db().execute(delete(RecoveryCode).where(RecoveryCode.member_id==member.id,RecoveryCode.digest==digest(code.lower()))).rowcount
    db().commit();return bool(changed)

@bp.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        email=request.form.get('email','').strip().lower()
        rate_limit('login-ip');rate_limit('login-account',10,email)
        member=db().scalar(select(Member).where(Member.email==email))
        if member and not member.disabled and check_password_hash(member.password_hash,request.form.get('password','')):
            if member.mfa_enabled:
                session.clear();session.update(pre_uid=member.id,pre_version=member.version,pre_expires=time.time()+300,csrf=secrets.token_urlsafe(32),setup_credential=seal(request.form.get('password','')))
                return redirect(url_for('auth.challenge'))
            authenticate_session(member)
            from .onboarding import enqueue
            enqueue(member,request.form.get('password',''));db().commit()
            return redirect(url_for('auth.security') if member.admin else url_for('portal.home'))
        flash('Sign-in failed. Check your email and password.','error')
    return render_template('login.html')

@bp.route('/mfa',methods=['GET','POST'])
def challenge():
    member=db().get(Member,session.get('pre_uid',''))
    if not member or member.disabled or member.version!=session.get('pre_version') or session.get('pre_expires',0)<time.time():
        session.clear();return redirect(url_for('auth.login'))
    if request.method=='POST':
        rate_limit('mfa-ip');rate_limit('mfa-account',8,member.id)
        if verify_factor(member,request.form.get('code','')):
            setup_password=unseal(session.pop('setup_credential',''))
            authenticate_session(member,True)
            if setup_password:
                from .onboarding import enqueue
                enqueue(member,setup_password)
            audit('Member completed MFA sign-in');db().commit()
            return redirect(url_for('portal.home'))
        flash('Invalid or already-used verification code.','error')
    return render_template('mfa.html')

@bp.post('/logout')
def logout():session.clear();return redirect(url_for('auth.login'))

@bp.route('/security',methods=['GET','POST'])
def security():
    member=current_member()
    if not member:return redirect(url_for('auth.login'))
    if member.mfa_enabled and not session.get('mfa_ok'):abort(403)
    codes=None
    if request.method=='POST':
        rate_limit('security',10,member.id)
        action=request.form.get('action')
        if not check_password_hash(member.password_hash,request.form.get('password','')):abort(400,'Current password is incorrect.')
        if action=='start' and not member.mfa_enabled:
            member.mfa_pending=seal(pyotp.random_base32());db().commit()
        elif action=='confirm' and not member.mfa_enabled and member.mfa_pending:
            totp=pyotp.TOTP(unseal(member.mfa_pending));code=request.form.get('code','').strip()
            counter=int(time.time()//30)
            matched=next((step for step in (counter,counter-1,counter+1) if secrets.compare_digest(totp.at(step*30),code)),None)
            if matched is None:abort(400,'Authenticator code did not match.')
            member.mfa_secret=member.mfa_pending;member.mfa_pending='';member.mfa_enabled=True;member.mfa_counter=matched
            member.version+=1;codes=[secrets.token_hex(8) for _ in range(10)]
            for code in codes:db().add(RecoveryCode(digest=digest(code),member_id=member.id))
            audit('MFA enabled');db().commit();authenticate_session(member,True)
        elif action=='disable':
            if member.admin:abort(403,'Administrator accounts must keep MFA enabled.')
            if not member.mfa_enabled or not verify_factor(member,request.form.get('code','')):abort(400,'A current MFA or unused recovery code is required.')
            member.mfa_enabled=False;member.mfa_secret='';member.mfa_pending='';member.version+=1
            db().execute(delete(RecoveryCode).where(RecoveryCode.member_id==member.id));db().commit();authenticate_session(member)
        elif action=='password':
            password=request.form.get('new_password','')
            if not valid_password(password) or password!=request.form.get('confirm'):abort(400,'Passwords must match and contain 12–256 characters.')
            with member_lock(member):
                db().refresh(member)
                if not check_password_hash(member.password_hash,request.form.get('password','')):abort(409,'Your password changed. Sign in again.')
                from .passwords import enqueue
                enqueue(member,password)
                member.password_hash=generate_password_hash(password);member.version+=1
                db().execute(delete(OneTimeToken).where(OneTimeToken.member_id==member.id,OneTimeToken.purpose=='reset'))
                db().commit()
            authenticate_session(member,member.mfa_enabled)
            flash('Password updated. Linked Emby servers and Ombi will sync automatically. Emby Connect must be changed separately.','success')
        else:abort(400,'Invalid security action.')
    from .passwords import status
    return render_template('security.html',password_sync=status(member),member=member,codes=codes,setup_key=unseal(member.mfa_pending) if member.mfa_pending and not member.mfa_enabled else '')

@bp.get('/security/qr')
def qr():
    member=current_member()
    if not member or not member.mfa_pending or member.mfa_enabled:abort(404)
    uri=pyotp.TOTP(unseal(member.mfa_pending)).provisioning_uri(name=member.email,issuer_name='MPS')
    image=qrcode.make(uri,image_factory=qrcode.image.svg.SvgPathImage);buffer=io.BytesIO();image.save(buffer)
    return Response(buffer.getvalue(),mimetype='image/svg+xml')

@bp.route('/forgot',methods=['GET','POST'])
def forgot():
    if request.method=='POST':
        email=request.form.get('email','').strip().lower();rate_limit('reset-ip',5);rate_limit('reset-email',3,email)
        member=db().scalar(select(Member).where(Member.email==email))
        if member and not member.disabled:
            try:token_email(member,'reset')
            except APIError:audit('Password reset email delivery failed');db().commit()
        flash('If that account exists, a password reset email has been sent.','success')
    return render_template('forgot.html')

@bp.route('/reset/<token>',methods=['GET','POST'])
def reset(token):
    item=db().get(OneTimeToken,digest(token))
    if not item or item.purpose!='reset' or item.expires<time.time():abort(400,'This reset link is invalid or expired.')
    if request.method=='POST':
        rate_limit('reset-complete',10)
        password=request.form.get('password','')
        if not valid_password(password) or password!=request.form.get('confirm'):abort(400,'Passwords must match and contain 12–256 characters.')
        member=db().get(Member,item.member_id)
        with member_lock(member):
            db().refresh(member)
            used=db().execute(delete(OneTimeToken).where(OneTimeToken.digest==digest(token),OneTimeToken.expires>time.time())).rowcount
            if not used:db().rollback();abort(409,'This link was already used.')
            from .passwords import enqueue
            enqueue(member,password)
            member.password_hash=generate_password_hash(password);member.version+=1
            db().execute(delete(OneTimeToken).where(OneTimeToken.member_id==member.id,OneTimeToken.purpose=='reset'))
            db().commit()
        session.clear();flash('Password reset. Linked Emby servers and Ombi will sync automatically; Emby Connect is separate. Sign in; MFA remains enabled.','success')
        return redirect(url_for('auth.login'))
    return render_template('reset.html')

@bp.route('/verify/<token>',methods=['GET','POST'])
def verify(token):
    item=db().get(OneTimeToken,digest(token))
    if not item or item.purpose!='verify' or item.expires<time.time():abort(400,'This verification link is invalid or expired.')
    if request.method=='POST':
        member=db().get(Member,item.member_id)
        used=db().execute(delete(OneTimeToken).where(OneTimeToken.digest==digest(token),OneTimeToken.expires>time.time())).rowcount
        if not used:db().rollback();abort(409)
        member.verified=True;db().commit();flash('Email verified. Your service accounts are being set up automatically.','success')
        return redirect(url_for('portal.home'))
    return render_template('verify.html')

@bp.post('/verify/resend')
@login_required
def resend():
    member=current_member();rate_limit('verify-email',3,member.id)
    if not member.verified:token_email(member,'verify')
    flash('Verification email sent.','success');return redirect(url_for('portal.home'))
