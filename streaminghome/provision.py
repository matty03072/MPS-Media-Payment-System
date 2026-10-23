import json
from flask import current_app
from sqlalchemy import select
from .core import *
from .models import Account,Server,AccountPermission
from .emby import Emby,Connect,APIError

def client(server):return current_app.config.get('EMBY_FACTORY',Emby)(server.url,unseal(server.key))
def accounts(member):return db().scalars(select(Account).where(Account.member_id==member.id)).all()

def permission(row):return db().get(AccountPermission,(row.member_id,row.server_id))
def server_enabled(row):
    p=permission(row)
    return p.enabled if p else True

def apply_policy(api,row,disabled=False):
    p=permission(row)
    api.policy(row.remote_id,json.loads(row.folders),disabled=disabled or not server_enabled(row),live_tv=bool(p and p.live_tv))
    if p:p.pending=False

def provision(member,portal_password,connect_password,mode):
    rows=accounts(member)
    if not rows:raise APIError('No invited servers are associated with this membership.')
    for row in rows:
        api=client(db().get(Server,row.server_id));folders=json.loads(row.folders)
        if row.state in ('creating','review'):raise APIError('An uncertain server operation needs administrator review before retrying.')
        if not set(folders)<={x['id'] for x in api.libraries()}:raise APIError('An invited library is no longer available.')
        if not row.remote_id and any(u.get('Name','').casefold()==member.username.casefold() for u in api.users()):
            raise APIError('This username already exists on an invited server. Existing accounts will not be overwritten.')
    connect=current_app.config.get('CONNECT_FACTORY',Connect)()
    if mode=='new':connect.register(member.username,member.email,portal_password)
    connect.authenticate(member.email,connect_password if mode=='existing' else portal_password)
    member.connect_email=member.email;db().commit()
    for row in rows:
        if row.state=='ready':continue
        api=client(db().get(Server,row.server_id));folders=json.loads(row.folders)
        if not row.remote_id:
            row.state='creating';db().commit()
            row.remote_id=api.create(member.username);row.remote_name=member.username;row.state='created';db().commit()
        try:
            apply_policy(api,row,disabled=True);row.access_enabled=False
            api.password(row.remote_id,portal_password);row.state='secured';db().commit()
        except APIError:
            try:api.delete(row.remote_id);row.remote_id='';row.state='not_created'
            except APIError:row.state='review'
            db().commit();raise APIError('Password or permission setup failed. Review this account before continuing.') from None
        row.link_pending=api.link(row.remote_id,member.email)
        apply_policy(api,row,disabled=not entitled(member))
        row.access_enabled=entitled(member) and server_enabled(row);row.state='ready';row.sync_error='';db().commit()
    audit('Completed member Emby provisioning');db().commit()

def sync_access(member,force=False):
    for row in accounts(member):
        wanted=entitled(member) and server_enabled(row)
        p=permission(row)
        if row.state!='ready' or not row.remote_id:continue
        if not force and row.access_enabled==wanted and not row.sync_error and not (p and p.pending):continue
        try:
            apply_policy(client(db().get(Server,row.server_id)),row,disabled=not wanted)
            row.access_enabled=wanted;row.sync_error='Username update needs retry.' if row.remote_name!=member.username else ''
        except APIError:
            row.sync_error='Access update failed; the worker will retry. Check server connectivity and selected libraries.'
            if p:p.pending=True
        db().commit()

def rename_servers(member,new_name):
    rows=accounts(member)
    for row in rows:
        if not row.remote_id:continue
        api=client(db().get(Server,row.server_id))
        if any(u.get('Name','').casefold()==new_name.casefold() and str(u.get('Id'))!=row.remote_id for u in api.users()):
            raise APIError('That username is already used on a linked server. No username was changed.')
    # Reserve the portal username first. Remote outcomes remain visible per server and retries are idempotent by ID.
    member.username=new_name;member.username_key=new_name.casefold();db().commit()
    failed=False
    for row in rows:
        if not row.remote_id or row.remote_name==new_name:continue
        try:client(db().get(Server,row.server_id)).rename(row.remote_id,new_name);row.remote_name=new_name;row.sync_error=''
        except APIError:row.sync_error='Username update needs retry.';failed=True
        db().commit()
    if failed:raise APIError('Your portal username was saved, but a server update failed. Retry the same username to finish synchronising.')
