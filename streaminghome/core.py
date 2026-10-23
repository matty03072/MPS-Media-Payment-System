import functools, hashlib, json, secrets, time
from contextlib import contextmanager
from flask import abort, current_app, g, redirect, request, session, url_for
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from .models import Setting, Member, Audit, Limit

def db():
    if 'db' not in g:g.db=current_app.extensions['sessions']()
    return g.db

def seal(value): return current_app.extensions['cipher'].encrypt(value.encode()).decode()
def unseal(value): return current_app.extensions['cipher'].decrypt(value.encode()).decode() if value else ''
def digest(value): return hashlib.sha256(value.encode()).hexdigest()

def setting(key,default='',secret=False):
    row=db().get(Setting,key)
    return (unseal(row.value) if secret else row.value) if row else default

def put(key,value,secret=False):
    row=db().get(Setting,key)
    if not row: row=Setting(key=key);db().add(row)
    row.value=seal(value) if secret else str(value)

def audit(message): db().add(Audit(message=message[:400]))

def entitled(member,now=None):
    return not member.disabled and member.verified and (not member.requires_payment or member.paid_through > (now or time.time()))

def current_member():
    if hasattr(g,'member'): return g.member
    member=db().get(Member,session.get('uid',''))
    if not member or member.disabled or member.version!=session.get('version'):
        g.member=None;return None
    g.member=member;return member

def login_required(fn):
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        member=current_member()
        if not member:return redirect(url_for('auth.login'))
        if member.mfa_enabled and not session.get('mfa_ok'):return redirect(url_for('auth.challenge'))
        if member.admin and not member.mfa_enabled:return redirect(url_for('auth.security'))
        return fn(*args,**kwargs)
    return wrapped

def admin_required(fn):
    @login_required
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        if not current_member().admin: abort(403)
        return fn(*args,**kwargs)
    return wrapped

def active_required(fn):
    @login_required
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        if not entitled(current_member()):abort(403,'An active, verified membership is required.')
        return fn(*args,**kwargs)
    return wrapped

def rate_limit(kind,count=10,identity=''):
    key=digest(f'{kind}:{identity or request.remote_addr}')
    now=time.time()
    # A compare-and-update reservation works on both SQL Server and SQLite.
    db().execute(delete(Limit).where(Limit.key==key,Limit.expires<now));db().commit()
    row=db().get(Limit,key)
    if not row:
        try:db().add(Limit(key=key,count=0,expires=now+900));db().commit()
        except IntegrityError:db().rollback()
    changed=db().execute(update(Limit).where(Limit.key==key,Limit.count<count).values(count=Limit.count+1)).rowcount
    db().commit()
    if not changed:abort(429,'Too many attempts. Try again in 15 minutes.')

@contextmanager
def member_lock(member):
    token=secrets.token_hex(16)
    changed=db().execute(update(Member).where(Member.id==member.id,Member.operation_lock=='').values(operation_lock=token,operation_started=time.time())).rowcount
    db().commit()
    if not changed:abort(409,'Another account operation is running or needs administrator recovery.')
    try:yield
    except Exception:
        db().rollback();raise
    finally:
        db().execute(update(Member).where(Member.id==member.id,Member.operation_lock==token).values(operation_lock='',operation_started=0));db().commit()
