"""GitHub MPS client licensing. This module is not installed on the owner's site."""
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit
import requests
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from .core import db, setting, put, admin_required, rate_limit, current_member
from .models import Member, Setting
from .license_tokens import verify

bp = Blueprint('licensing', __name__)
FREE_MEMBERS = 10


def prepare():
    # Initialise only durable identifiers, before any request operation begins.
    for key, value in (('mps_installation_id', str(uuid.uuid4())), ('mps_admission_lock', '1')):
        if not db().get(Setting, key):
            db().add(Setting(key=key, value=value))
            try:db().commit()
            except IntegrityError:db().rollback()


def issuer():
    path = Path(__file__).with_name('licensing_public.json')
    return json.loads(path.read_text()) if path.exists() else {'url':'', 'public_key':''}


def lease():
    code = setting('mps_licence_lease')
    if not code:return None
    try:
        result = verify(code, issuer()['public_key'], setting('mps_installation_id'),
                        allow_test=bool(current_app.config.get('TESTING')))
        # Subscription leases must expire; perpetual/surprisingly long codes do not unlock.
        if not result['expires'] or result['expires'] > result['issued'] + 3600:
            return None
        return result
    except (ValueError, KeyError):return None


def unlimited():
    entitlement = lease()
    return bool(entitlement and entitlement['member_limit'] == 0)


def blocked():
    # A signed registration removal remains effective after its lease expires.
    # Expiry must not turn a remotely disabled application back into a free one.
    try:
        token = setting('mps_licence_lease')
        if not token:return False
        result = verify(token, issuer()['public_key'], setting('mps_installation_id'),
                        allow_test=bool(current_app.config.get('TESTING')), allow_expired=True)
        return result['member_limit'] == -1
    except (ValueError, KeyError):return False


def enforce_revocation():
    endpoint = request.endpoint or ''
    if endpoint in ('static', 'health', 'licensing.page') or endpoint.startswith(('auth.', 'branding.')):
        return
    if blocked():
        abort(403, 'This application registration has been removed. Its data is retained. An administrator can sign in and open /admin/licence for licence recovery.')


def eligible_members():
    from .deletion import visible_members
    return select(Member.id).where(Member.admin == False, Member.disabled == False, visible_members()).order_by(Member.created, Member.id)


def allowed(member):
    if blocked():return False
    if member.admin or unlimited():return True
    return member.id in set(db().scalars(eligible_members().limit(FREE_MEMBERS)))


def reserve_member():
    # The write lock and member INSERT belong to the SAME transaction. Concurrent
    # signups cannot both claim the final free place.
    changed = db().execute(update(Setting).where(Setting.key == 'mps_admission_lock').values(value=Setting.value)).rowcount
    if not changed:abort(503, 'Licence state is not initialised. Reload and try again.')
    if unlimited():return
    from .deletion import visible_members
    count = db().scalar(select(func.count()).select_from(Member).where(Member.admin == False, Member.disabled == False, visible_members()))
    if count >= FREE_MEMBERS:
        abort(403, 'This installation has reached its ten-member allowance. The administrator can unlock more members from Licence.')


def refresh(force=False, activation_code=None):
    if not force and float(setting('mps_licence_next_check', '0')) > time.time():return
    code = activation_code or setting('mps_activation_code', secret=True)
    if not code:return
    authority = issuer();url = authority['url'].rstrip('/')
    if urlsplit(url).scheme != 'https' or not authority['public_key']:
        put('mps_licence_status', 'Licence service is not configured.');db().commit();return
    put('mps_licence_next_check', str(time.time()+900));db().commit()
    transport = current_app.config.get('LICENCE_HTTP_POST', requests.post)
    try:
        response = transport(url+'/mps/api/check-in', json={'code':code, 'installation_id':setting('mps_installation_id')}, timeout=10, allow_redirects=False)
        if response.status_code == 403:
            if not blocked() and not activation_code:put('mps_licence_lease', '')
            put('mps_licence_status', 'Activation code was not accepted.');db().commit();return
        if response.status_code == 409:
            put('mps_licence_status', 'All three application slots are in use. Remove a registration in your licence account first.');db().commit();return
        response.raise_for_status()
        signed = response.json()['lease']
        verified = verify(signed, authority['public_key'], setting('mps_installation_id'),
                          allow_test=bool(current_app.config.get('TESTING')))
        if not verified['expires'] or verified['expires'] > verified['issued'] + 3600:
            raise ValueError('Invalid lease period')
        put('mps_licence_lease', signed)
        if activation_code:put('mps_activation_code', activation_code, secret=True)
        put('mps_licence_status', 'Subscription active.' if verified['member_limit'] == 0 else 'This application registration has been removed and normal use is disabled. Account data is retained.' if verified['member_limit'] == -1 else 'Subscription inactive. Ten-member tier applies; all account information is retained.')
        put('mps_licence_last_check', str(time.time()));db().commit()
    except (requests.RequestException, ValueError, KeyError, TypeError):
        db().rollback()
        put('mps_licence_status', 'Licence service could not be reached or verified. The last valid check remains usable until its expiry; account information is retained.')
        db().commit()


@bp.route('/admin/licence', methods=['GET', 'POST'])
@admin_required
def page():
    if request.method == 'POST':
        rate_limit('licence-activation', 10, current_member().id)
        code = request.form.get('code', '').strip()
        if len(code) > 200:abort(400, 'Invalid activation code.')
        refresh(force=True, activation_code=code or None)
        flash(setting('mps_licence_status'), 'success' if unlimited() else 'error')
        return redirect(url_for('licensing.page'))
    authority = issuer()
    purchase_url = (authority['url'].rstrip('/')+'/mps?'+urlencode({'installation':setting('mps_installation_id')})) if authority['url'] else ''
    return render_template('licence.html', unlimited=unlimited(), entitlement=lease(), purchase_url=purchase_url,
                           installation_id=setting('mps_installation_id'))
