import os,json,secrets,hashlib,time
from pathlib import Path
from datetime import datetime,timezone
import click
from cryptography.fernet import Fernet
from flask import Flask,g,request,session,abort,render_template
from sqlalchemy import create_engine,select,event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash
from .models import Base,Member
from .core import db,current_member,setting,entitled
from .emby import APIError

def create_app(config=None):
    app=Flask(__name__,template_folder='../templates',static_folder='../static')
    data=Path(os.environ.get('DATA_DIR','./data')).resolve()
    app.config.update(DATA_DIR=str(data),PUBLIC_URL='http://localhost:8099',DATABASE_URL='',
      MAX_CONTENT_LENGTH=1048576,SESSION_COOKIE_NAME='mps_session',SESSION_COOKIE_HTTPONLY=True,
      SESSION_COOKIE_SAMESITE='Lax',PERMANENT_SESSION_LIFETIME=3600)
    config_file=os.environ.get('MPS_CONFIG')
    if config_file:app.config.update(json.loads(Path(config_file).read_text(encoding='utf-8-sig')))
    for name in ('DATA_DIR','PUBLIC_URL','DATABASE_URL'):
        if os.environ.get(name):app.config[name]=os.environ[name]
    if config:app.config.update(config)
    if app.config.get('TRUST_PROXY'):
        # Enable only with Waitress bound to loopback behind the bundled Caddy proxy.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1)
    app.config['PUBLIC_URL']=app.config['PUBLIC_URL'].rstrip('/')
    app.config['SESSION_COOKIE_SECURE']=app.config['PUBLIC_URL'].startswith('https://')
    data=Path(app.config['DATA_DIR']);data.mkdir(parents=True,exist_ok=True,mode=0o700)
    keypath=data/'master.key'
    try:
        fd=os.open(keypath,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key())
    except FileExistsError:pass
    key=keypath.read_bytes().strip();app.extensions['cipher']=Fernet(key);app.secret_key=hashlib.sha256(key+b'session-v2').digest()
    url=app.config['DATABASE_URL'] or 'sqlite:///'+str(data/'development.sqlite3')
    if not app.config.get('TESTING') and not app.config['DATABASE_URL'] and app.config['PUBLIC_URL'].startswith('https://'):
        raise RuntimeError('Choose an explicit database using the installer.')
    options={'pool_pre_ping':True,'hide_parameters':True}
    if url.startswith('sqlite:'):options['connect_args']={'check_same_thread':False,'timeout':20}
    engine=create_engine(url,**options)
    if engine.dialect.name=='sqlite':
        @event.listens_for(engine,'connect')
        def sqlite_setup(conn,record):
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('PRAGMA busy_timeout=20000')
            conn.execute('PRAGMA journal_mode=WAL')
    app.extensions['engine']=engine;app.extensions['sessions']=sessionmaker(engine,expire_on_commit=False)
    @app.teardown_appcontext
    def close(error=None):
        connection=g.pop('db',None)
        if connection:connection.close()
    @app.before_request
    def csrf():
        if request.path in ('/stripe/webhook','/paypal/webhook','/health'):return
        session.setdefault('csrf',secrets.token_urlsafe(32))
        if request.method=='POST' and not secrets.compare_digest(session['csrf'],request.form.get('csrf','')):abort(400,'This form expired. Reload the page and try again.')
    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer',
          'Content-Security-Policy':"default-src 'self'; script-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'self' https://checkout.stripe.com https://billing.stripe.com https://www.paypal.com https://www.sandbox.paypal.com; base-uri 'none'; frame-ancestors 'none'"})
        if app.config['SESSION_COOKIE_SECURE']:response.headers['Strict-Transport-Security']='max-age=31536000'
        return response
    from .branding import identity, bp as branding_bp
    app.register_blueprint(branding_bp)
    from .onboarding import status as onboarding_status
    app.jinja_env.globals['onboarding_status']=onboarding_status
    @app.context_processor
    def context():return {**identity(),'me':current_member(),'csrf':session.get('csrf',''),'setting':setting,'entitled':entitled,'now':time.time(),'json_load':json.loads,'public_url':app.config['PUBLIC_URL']}
    @app.template_filter('date')
    def date(value):return datetime.fromtimestamp(value,timezone.utc).strftime('%d %b %Y · %H:%M UTC') if value else '—'
    @app.template_filter('money')
    def money(value):return f'£{value/100:.2f}'
    @app.errorhandler(APIError)
    def apierror(error):db().rollback();return render_template('error.html',message=str(error)),502
    @app.errorhandler(IntegrityError)
    def conflict(error):db().rollback();return render_template('error.html',message='That record already exists or conflicts with another update. No duplicate was saved.'),409
    for code in (400,403,404,409,429,503):
        app.register_error_handler(code,lambda error:(render_template('error.html',message=error.description),error.code))
    @app.get('/health')
    def health():
        with engine.connect() as conn:conn.exec_driver_sql('SELECT 1')
        return {'status':'ok','application':'MPS','version':'0.3.0','installer_version':'0.1.0','database':engine.dialect.name}
    from . import auth,portal,admin,billing,management,paypal,revenue,plans,deletion,guides,availability,connected
    for blueprint in (auth.bp,portal.bp,admin.bp,billing.bp,management.bp,paypal.bp,revenue.bp,plans.bp,deletion.bp,guides.bp,availability.bp,connected.bp):app.register_blueprint(blueprint)
    from .setup import bp as setup_bp
    app.register_blueprint(setup_bp)
    @app.cli.command('init-db')
    def init_db():
        from .schema import migrate
        migrate(engine);click.echo('Versioned schema ready.')
    @app.cli.command('create-admin')
    @click.option('--email',prompt=True)
    @click.option('--username',prompt=True,default='administrator')
    @click.password_option(confirmation_prompt=True)
    def create_admin(email,username,password):
        if not auth.valid_password(password) or not portal.username_valid(username):raise click.ClickException('Use a 12+ character password and valid username.')
        member=db().scalar(select(Member).where(Member.email==email.strip().lower()))
        if member:raise click.ClickException('This email already exists. Use reset-admin for password recovery.')
        db().add(Member(email=email.strip().lower(),username=username,username_key=username.casefold(),password_hash=generate_password_hash(password),admin=True,verified=True));db().commit()
        click.echo('Administrator created. MFA enrolment is mandatory on first sign-in.')
    @app.cli.command('reset-admin')
    @click.option('--email',prompt=True)
    @click.password_option(confirmation_prompt=True)
    def reset_admin(email,password):
        member=db().scalar(select(Member).where(Member.email==email.strip().lower(),Member.admin==True))
        if not member or not auth.valid_password(password):raise click.ClickException('Valid administrator email and 12+ character password required.')
        member.password_hash=generate_password_hash(password);member.version+=1;db().commit();click.echo('Password reset. MFA remains enabled.')
    @app.cli.command('import-users')
    def import_users_cli():
        from .importing import import_users
        click.echo(json.dumps(import_users()))
    @app.cli.command('worker')
    @click.option('--once',is_flag=True)
    def worker(once):
        from .worker import tick
        while True:
            tick()
            if once:break
            time.sleep(30)
    return app
