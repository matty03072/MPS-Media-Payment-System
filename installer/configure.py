"""Host-side database wizard; configuration never silently falls back."""
import argparse, getpass, hashlib, json, os, secrets, sys, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text, select
from sqlalchemy.engine import URL
from streaminghome import create_app
from streaminghome.schema import migrate
from streaminghome.core import db, put
from streaminghome.models import Member

def write_private(path, content):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(str(path)+'.new',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(content)
    os.replace(str(path)+'.new',path)

def prepare(config_path, data_dir, database='sqlite', public_url='http://localhost:8099'):
    config_path=Path(config_path);data=Path(data_dir).resolve();data.mkdir(parents=True,exist_ok=True)
    if config_path.exists():
        config=json.loads(config_path.read_text(encoding='utf-8-sig'))
        if config.get('INSTALLER_ID')!='members-portal':
            raise RuntimeError('Existing configuration is not managed by this installer.')
    else:
        if database=='sqlite':
            url=URL.create('sqlite',database=str(data/'members.sqlite3')).render_as_string(hide_password=False)
        elif database=='postgres-local':
            if os.name=='nt':raise RuntimeError('Local PostgreSQL management is Linux only.')
            url='postgresql+psycopg://membersportal@/membersportal?host=/var/run/postgresql'
        else:
            engine='postgresql+psycopg' if database=='postgres' else 'mssql+pyodbc'
            host=input('Database hostname: ').strip()
            port=int(input('Port [5432]: ') or '5432') if database=='postgres' else 1433
            name=input('Dedicated empty database name: ').strip()
            user=input('Database username: ').strip()
            password=getpass.getpass('Database password (hidden): ')
            query={'sslmode':'verify-full'} if database=='postgres' else {'driver':'ODBC Driver 18 for SQL Server','Encrypt':'yes','TrustServerCertificate':'no'}
            if database=='postgres':
                ca=input('CA certificate path (blank = system/default CA): ').strip()
                if ca:query['sslrootcert']=ca
            url=URL.create(engine,username=user,password=password,host=host,port=port,database=name,query=query).render_as_string(hide_password=False)
        config={'INSTALLER_ID':'members-portal','DATA_DIR':str(data),'DATABASE_URL':url,'PUBLIC_URL':public_url,'TRUST_PROXY':False}
    # No credentials appear in errors or logs, and failed DB tests do not commit config.
    try:
        engine=create_engine(config['DATABASE_URL'],hide_parameters=True)
        with engine.connect() as c:c.execute(text('SELECT 1'))
        migrate(engine);engine.dispose()
    except Exception as exc:
        raise RuntimeError('Database validation/migration failed ('+type(exc).__name__+'). Check credentials, TLS, driver and empty/managed schema; config was not replaced.') from None
    if not config_path.exists():write_private(config_path,json.dumps(config,indent=2))
    app=create_app(config)
    with app.app_context():
        if not db().scalar(select(Member.id).where(Member.admin==True).limit(1)):
            token=secrets.token_urlsafe(32)
            put('setup_token_hash',hashlib.sha256(token.encode()).hexdigest());put('setup_expires',str(time.time()+3600));db().commit()
            write_private(data/'setup-code.txt',token+'\n')
            print('Owner setup: '+config['PUBLIC_URL']+'/setup')
            print('One-hour setup code is in the protected file: '+str(data/'setup-code.txt'))
        else: print('Existing owner preserved; anonymous setup stays closed.')
    app.extensions['engine'].dispose()
    return config

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--data',required=True);p.add_argument('--database',choices=['sqlite','postgres-local','postgres','sqlserver'],default=None);p.add_argument('--public-url',default='http://localhost:8099')
    a=p.parse_args()
    if a.database is None:
        print('Database choices: sqlite (automatic), postgres-local (Linux automatic), postgres (existing), sqlserver (existing).')
        a.database=input('Database [sqlite]: ').strip() or 'sqlite'
        if a.database not in ('sqlite','postgres-local','postgres','sqlserver'):p.error('Invalid database choice')
    prepare(a.config,a.data,a.database,a.public_url)
