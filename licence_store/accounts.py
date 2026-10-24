"""Separate licence customers: never create media members or grant media access."""
import functools
import hashlib
import secrets
import time
import re
import io
import base64
import pyotp
import qrcode
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import select, delete, update
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from .models import Customer, CustomerToken, RecoveryCode, Order, Activation
from .service import remove_activation


def install(app, core, mail, remote):
    bp = Blueprint('mps_accounts', __name__, template_folder='templates')
    def digest(value):return hashlib.sha256(value.encode()).hexdigest()
    def current():
        customer = core.db().get(Customer, session.get('mps_customer', ''))
        if not customer or customer.version != session.get('mps_customer_version'):return None
        return customer
    def login_required(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            customer = current()
            if not customer:return redirect(url_for('mps_accounts.login'))
            if not customer.verified:return redirect(url_for('mps_accounts.verification'))
            if not customer.mfa_secret:return redirect(url_for('mps_accounts.mfa_setup'))
            if not session.get('mps_mfa'):return redirect(url_for('mps_accounts.challenge'))
            return fn(*args, **kwargs)
        return wrapped
    def password_valid(value):return 12 <= len(value) <= 256
    def mail_token(customer, purpose):
        raw = secrets.token_urlsafe(32)
        core.db().execute(delete(CustomerToken).where(CustomerToken.customer_id == customer.id, CustomerToken.purpose == purpose))
        core.db().add(CustomerToken(digest=digest(raw), customer_id=customer.id, purpose=purpose, expires=time.time()+1800))
        core.db().commit()
        endpoint = 'mps_accounts.verify_email' if purpose == 'verify' else 'mps_accounts.reset'
        link = app.config['PUBLIC_URL']+url_for(endpoint, token=raw)
        mail.send_mail(customer.email, 'MPS licence account: '+('verify email' if purpose == 'verify' else 'reset password'),
                       'Use this single-use link within 30 minutes:\n'+link+'\n\nIf you did not request this, ignore this email.')
    def factor(customer, code):
        code = code.strip().replace(' ', '')
        if customer.mfa_secret and re.fullmatch(r'\d{6}', code):
            totp = pyotp.TOTP(core.unseal(customer.mfa_secret))
            counter = int(time.time()//30)
            for candidate in (counter, counter-1, counter+1):
                if secrets.compare_digest(totp.at(candidate*30), code):
                    changed = core.db().execute(update(Customer).where(Customer.id==customer.id, Customer.mfa_counter<candidate).values(mfa_counter=candidate)).rowcount
                    core.db().commit()
                    return bool(changed)
        changed = core.db().execute(delete(RecoveryCode).where(RecoveryCode.customer_id==customer.id, RecoveryCode.digest==digest(code))).rowcount
        core.db().commit()
        return bool(changed)

    @bp.route('/mps/register', methods=['GET','POST'])
    def register():
        if request.method=='POST':
            core.rate_limit('mps-register', 5)
            email=request.form.get('email','').strip().lower(); password=request.form.get('password','')
            if len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):abort(400,'Enter a valid email address.')
            if not password_valid(password) or password!=request.form.get('confirm'):abort(400,'Use matching passwords of 12–256 characters.')
            customer=Customer(email=email,password_hash=generate_password_hash(password))
            core.db().add(customer)
            try:core.db().commit()
            except IntegrityError:
                core.db().rollback();abort(409,'Unable to create this account. Try signing in or resetting your password.')
            session['mps_customer']=customer.id;session['mps_customer_version']=customer.version;session['mps_mfa']=False
            try:mail_token(customer,'verify')
            except Exception:flash('Account saved, but the verification email could not be sent. Please retry.','error')
            return redirect(url_for('mps_accounts.verification'))
        return render_template('mps_store/account_form.html', kind='register')

    @bp.route('/mps/login', methods=['GET','POST'])
    def login():
        if request.method=='POST':
            email=request.form.get('email','').strip().lower()
            core.rate_limit('mps-login-ip',20)
            core.rate_limit('mps-login-account',10,email)
            customer=core.db().scalar(select(Customer).where(Customer.email==email))
            if not customer or not check_password_hash(customer.password_hash,request.form.get('password','')):abort(400,'Email or password was not accepted.')
            session['mps_customer']=customer.id;session['mps_customer_version']=customer.version;session['mps_mfa']=False
            return redirect(url_for('mps_accounts.dashboard'))
        return render_template('mps_store/account_form.html', kind='login')

    @bp.post('/mps/logout')
    def logout():
        for name in ('mps_customer','mps_customer_version','mps_mfa'):session.pop(name,None)
        return redirect(url_for('mps_store.shop'))

    @bp.route('/mps/verify', methods=['GET','POST'])
    def verification():
        customer=current()
        if not customer:return redirect(url_for('mps_accounts.login'))
        if customer.verified:return redirect(url_for('mps_accounts.dashboard'))
        if request.method=='POST':
            core.rate_limit('mps-verify-mail',3,customer.id)
            try:mail_token(customer,'verify');flash('Verification email sent.','success')
            except Exception:flash('Verification email could not be sent. Please retry later.','error')
        return render_template('mps_store/account_form.html',kind='verify')

    @bp.route('/mps/verify/<token>',methods=['GET','POST'])
    def verify_email(token):
        item=core.db().get(CustomerToken,digest(token))
        if not item or item.purpose!='verify' or item.expires<time.time():abort(400,'Verification link is invalid or expired.')
        if request.method=='POST':
            customer=core.db().get(Customer,item.customer_id)
            changed=core.db().execute(delete(CustomerToken).where(CustomerToken.digest==item.digest,CustomerToken.expires>time.time())).rowcount
            if not changed:abort(409,'This link was already used.')
            customer.verified=True;core.db().commit()
            flash('Email verified. Sign in to continue.','success');return redirect(url_for('mps_accounts.login'))
        return render_template('mps_store/account_form.html',kind='verify-confirm')

    @bp.route('/mps/mfa/setup',methods=['GET','POST'])
    def mfa_setup():
        customer=current()
        if not customer:return redirect(url_for('mps_accounts.login'))
        if not customer.verified:return redirect(url_for('mps_accounts.verification'))
        if customer.mfa_secret:return redirect(url_for('mps_accounts.challenge'))
        if not customer.mfa_pending:
            customer.mfa_pending=core.seal(pyotp.random_base32());core.db().commit()
        secret=core.unseal(customer.mfa_pending)
        if request.method=='POST':
            core.rate_limit('mps-mfa-setup',10,customer.id)
            code=request.form.get('code','').strip()
            if not pyotp.TOTP(secret).verify(code,valid_window=0):abort(400,'Enter the current authenticator code.')
            customer.mfa_secret=customer.mfa_pending;customer.mfa_pending='';customer.mfa_counter=int(time.time()//30)
            recovery=[secrets.token_hex(8) for _ in range(10)]
            for value in recovery:core.db().add(RecoveryCode(digest=digest(value),customer_id=customer.id))
            core.db().commit();session['mps_mfa']=True
            return render_template('mps_store/account_form.html',kind='recovery',codes=recovery)
        uri=pyotp.TOTP(secret).provisioning_uri(customer.email,issuer_name='MPS Licences')
        image=qrcode.make(uri);buffer=io.BytesIO();image.save(buffer,format='PNG')
        return render_template('mps_store/account_form.html',kind='mfa-setup',secret=secret,qr=base64.b64encode(buffer.getvalue()).decode())

    @bp.route('/mps/mfa',methods=['GET','POST'])
    def challenge():
        customer=current()
        if not customer:return redirect(url_for('mps_accounts.login'))
        if not customer.verified:return redirect(url_for('mps_accounts.verification'))
        if not customer.mfa_secret:return redirect(url_for('mps_accounts.mfa_setup'))
        if request.method=='POST':
            core.rate_limit('mps-mfa',10,customer.id)
            if not factor(customer,request.form.get('code','')):abort(400,'Authenticator or recovery code was not accepted.')
            session['mps_mfa']=True;return redirect(url_for('mps_accounts.dashboard'))
        return render_template('mps_store/account_form.html',kind='mfa')

    @bp.route('/mps/forgot',methods=['GET','POST'])
    def forgot():
        if request.method=='POST':
            core.rate_limit('mps-forgot',5)
            customer=core.db().scalar(select(Customer).where(Customer.email==request.form.get('email','').strip().lower()))
            if customer:
                try:mail_token(customer,'reset')
                except Exception:pass
            flash('If this account exists, a password-reset email has been requested.','success')
        return render_template('mps_store/account_form.html',kind='forgot')

    @bp.route('/mps/reset/<token>',methods=['GET','POST'])
    def reset(token):
        item=core.db().get(CustomerToken,digest(token))
        if not item or item.purpose!='reset' or item.expires<time.time():abort(400,'Password-reset link is invalid or expired.')
        if request.method=='POST':
            core.rate_limit('mps-reset',10)
            password=request.form.get('password','')
            if not password_valid(password) or password!=request.form.get('confirm'):abort(400,'Use matching passwords of 12–256 characters.')
            customer=core.db().get(Customer,item.customer_id)
            changed=core.db().execute(delete(CustomerToken).where(CustomerToken.digest==item.digest,CustomerToken.expires>time.time())).rowcount
            if not changed:abort(409,'This link was already used.')
            customer.password_hash=generate_password_hash(password);customer.version+=1;core.db().commit()
            flash('Password reset. Your existing MFA remains required.','success');return redirect(url_for('mps_accounts.login'))
        return render_template('mps_store/account_form.html',kind='reset')

    @bp.get('/mps/account')
    @login_required
    def dashboard():
        customer=current()
        orders=core.db().scalars(select(Order).where(Order.customer_id==customer.id).order_by(Order.created.desc())).all()
        registrations={order.id:core.db().scalars(select(Activation).where(Activation.order_id==order.id).order_by(Activation.registered)).all() for order in orders}
        return render_template('mps_store/customer.html',customer=customer,orders=orders,registrations=registrations)

    @bp.post('/mps/account/remove')
    @login_required
    def remove():
        customer=current();core.rate_limit('mps-remove',10,customer.id)
        order=core.db().get(Order,request.form.get('order_id',''))
        if not order or order.customer_id!=customer.id:abort(404)
        if not check_password_hash(customer.password_hash,request.form.get('password','')):abort(400,'Confirm your password to remove this application.')
        try:remove_activation(core.db(),order.id,request.form.get('application_id',''),request.remote_addr or '')
        except ValueError:abort(404)
        flash('Registration removed. The application will be disabled on its next check or when its current check expires.','success')
        return redirect(url_for('mps_accounts.dashboard'))

    @bp.post('/mps/account/billing')
    @login_required
    def billing():
        customer=current();core.rate_limit('mps-manage-billing',10,customer.id)
        order=core.db().get(Order,request.form.get('order_id',''))
        if not order or order.customer_id!=customer.id or not order.subscription_id:abort(404)
        sub=remote().get('/subscriptions/'+order.subscription_id)
        if sub.get('metadata',{}).get('mps_order_id')!=order.id:abort(409,'Subscription ownership needs review.')
        result=remote().post('/billing_portal/sessions',{'customer':sub['customer'],'return_url':app.config['PUBLIC_URL']+'/mps/account'},'mps-portal-'+order.id+'-'+str(int(time.time()//30)))
        return redirect(result['url'],303)

    @bp.post('/mps/account/resend')
    @login_required
    def resend():
        customer=current();core.rate_limit('mps-resend-code',3,customer.id)
        order=core.db().get(Order,request.form.get('order_id',''))
        if not order or order.customer_id!=customer.id or not order.encrypted_code:abort(404)
        order.email_state='pending';order.email_next=0;core.db().commit()
        flash('Your unlock-code email has been queued.','success');return redirect(url_for('mps_accounts.dashboard'))

    app.register_blueprint(bp)
    return current, login_required
