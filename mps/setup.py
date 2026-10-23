"""One-time owner claim and authenticated setup guide."""
import hashlib, secrets, time, os
from flask import Blueprint, abort, flash, redirect, render_template, request, session, Response
from sqlalchemy import select, update
from werkzeug.security import generate_password_hash
from .core import db, setting, put, admin_required, rate_limit
from .models import Member, Setting, Server
from .auth import valid_password
from .portal import username_valid
bp = Blueprint('setup', __name__)

@bp.route('/setup', methods=['GET','POST'])
def owner():
    if db().scalar(select(Member.id).where(Member.admin == True).limit(1)):
        return redirect('/login')
    bootstrap = os.environ.get('UMBREL_BOOTSTRAP_PASSWORD', '')
    if bootstrap:
        # Authenticate inside the app too: the shared container network is untrusted.
        auth = request.authorization
        if not auth or not secrets.compare_digest(auth.username or '', 'owner') or not secrets.compare_digest(auth.password or '', bootstrap):
            rate_limit('umbrel-bootstrap', 10)
            return Response('Use the owner credentials shown by umbrelOS.', 401,
                            {'WWW-Authenticate': 'Basic realm="MPS setup"'})
    if request.method == 'POST':
        rate_limit('owner-claim', 10)
        supplied = hashlib.sha256(request.form.get('token','').encode()).hexdigest()
        stored = setting('setup_token_hash')
        if not stored or (not bootstrap and (not secrets.compare_digest(supplied, stored) or time.time() > float(setting('setup_expires','0')))):
            abort(403, 'Setup code is invalid or expired. Run the installer setup-code command on the server.')
        email=request.form.get('email','').strip().lower()
        username=request.form.get('username','').strip()
        password=request.form.get('password','')
        name=request.form.get('site_name','').strip()
        if not valid_password(password) or not username_valid(username) or '@' not in email or len(email)>254 or not 1<=len(name)<=80 or any(ord(c)<32 or ord(c)==127 for c in name):
            abort(400,'Enter a site name, valid email, username, and a password of at least 12 characters.')
        claimed=db().execute(update(Setting).where(Setting.key=='setup_token_hash',Setting.value==stored).values(value='')).rowcount
        if claimed != 1:
            db().rollback();abort(409,'Another owner already completed setup.')
        db().add(Member(email=email,username=username,username_key=username.casefold(),password_hash=generate_password_hash(password),admin=True,verified=True))
        put('site_name',name);put('setup_stage','integrations');db().commit()
        session.clear()
        flash('Owner created. Sign in and enrol MFA, then open Installation guide.','success')
        return redirect('/login')
    return render_template('setup_owner.html', umbrel_setup=bool(bootstrap))

CARDS = [
 ('Emby','https://emby.media/support/articles/Api-Key-Authentication.html','In the Emby server dashboard, open Advanced → API Keys. Create a key for this portal. Enter the server URL and key in Configuration → Add a server. Test & connect discovers its libraries. Select access libraries when creating invitations.','/admin/settings#servers'),
 ('Ombi','https://docs.ombi.app/','In Ombi Settings → Configuration, locate the API key. Enter the base URL and key in Configuration. Member accounts are provisioned using their signup details; do not use an admin login for member requests.','/admin/settings'),
 ('Email / SMTP','https://docs.python.org/3/library/smtplib.html','Use your email provider’s SMTP hostname, port, username and password. Choose STARTTLS (usually 587) or SSL (usually 465), and a verified sender address. Save, then explicitly send a test to your admin email. SMTP acceptance does not prove inbox delivery.','/admin/settings'),
 ('Stripe (optional)','https://docs.stripe.com/keys','Start in test mode. Create an API key and a webhook for the endpoint shown in Configuration. Copy its signing secret and subscribe to the listed events. Keep billing disabled until prices, checkout and webhook delivery have been tested.','/admin/settings'),
 ('PayPal (optional)','https://developer.paypal.com/api/rest/','Create a sandbox REST app, copy its client ID and secret, and register the webhook URL shown in Configuration. Save the webhook ID. Keep billing disabled until sandbox subscription tests pass.','/admin/settings'),
 ('AI recommendations (optional)','https://platform.openai.com/api-keys','Create a project API key and configure a supported model in Availability settings. API usage may cost money. Leave recommendations disabled until you choose to run a test.','/admin/settings'),
 ('Sonarr / Radarr','https://wiki.servarr.com/','In Settings → General, copy the API key and enter the base URL below. Test & save performs a read-only connection check. Download/import tracking is not implemented in this release; saving the connection does not enable it. Emby availability notifications remain separate.',''),
 ('HTTPS','https://caddyserver.com/docs/quick-starts/https','Keep setup on localhost or an SSH tunnel. Before public use, point your domain to the server and configure an existing HTTPS reverse proxy to the loopback web port. Set PUBLIC_URL in the protected config to your HTTPS address and restart both services. Never expose the HTTP backend directly.',''),
]
@bp.route('/admin/installation',methods=['GET','POST'])
@admin_required
def guide():
    if request.method=='POST':
        put('setup_stage','ready');db().commit();flash('Setup checklist acknowledged. Optional integrations remain disabled unless configured.','success')
    cards = CARDS
    if os.environ.get('UMBREL_BOOTSTRAP_PASSWORD'):
        cards = [card for card in CARDS if card[0] != 'HTTPS'] + [('umbrelOS access', 'https://umbrel.com/support', 'Open this app through Umbrel. Umbrel login remains enabled. Invitees need access through Umbrel too; public invitations and billing callbacks require a separately configured public HTTPS deployment. This preview does not configure public access automatically.', '')]
    return render_template('installation.html', cards=cards, worker_seen=float(setting('worker_seen','0')), configured_servers=db().scalar(select(Server.id).limit(1)))

@bp.post('/admin/installation/connection')
@admin_required
def connection():
    """Owner-initiated, read-only connection check; never submit media requests."""
    import requests
    from urllib.parse import urlparse
    from .core import audit
    service=request.form.get('service','')
    if service not in ('sonarr','radarr','ombi'):abort(400,'Unknown integration.')
    url=request.form.get('url','').strip().rstrip('/')
    key=request.form.get('key','').strip() or setting(service+'_key',secret=True)
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not key:
        abort(400,'Enter an HTTP(S) base URL without credentials and an API key.')
    endpoint='/api/v1/Settings/ombi' if service=='ombi' else '/api/v3/system/status'
    try:
        response=requests.get(url+endpoint,headers={'ApiKey':key} if service=='ombi' else {'X-Api-Key':key},timeout=10,allow_redirects=False)
        if response.status_code!=200:raise ValueError('Connection not accepted')
        data=response.json()
        if not isinstance(data,dict) or not data:raise ValueError('Invalid service response')
        if service!='ombi' and not data.get('version'):raise ValueError('Invalid service response')
    except (requests.RequestException,ValueError):
        abort(400,'Connection test failed. Check the URL, API key, TLS certificate and network access. Nothing was saved.')
    if service=='ombi' and setting('ombi_url') and setting('ombi_url')!=url and db().scalar(select(Member.id).where(Member.ombi_user_id!='').limit(1)):
        abort(400,'Unlink existing Ombi memberships before changing the Ombi server.')
    put(service+'_url',url);put(service+'_key',key,secret=True);put(service+'_connection_checked',str(time.time()))
    audit(service+' connection checked by administrator');db().commit()
    flash(service.title()+' connection tested and saved. Download/import tracking is not enabled by this check.','success')
    return redirect('/admin/installation')
