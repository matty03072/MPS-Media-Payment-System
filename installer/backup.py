"""Consistent SQLite backup and protected configuration/key bundle."""
import argparse, json, os, shutil, sqlite3
from pathlib import Path
from sqlalchemy.engine import make_url
p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--destination',required=True);a=p.parse_args()
os.umask(0o077)
config=json.loads(Path(a.config).read_text(encoding='utf-8-sig'));url=make_url(config['DATABASE_URL'])
if url.get_backend_name()!='sqlite':raise SystemExit('Use pg_dump or SQL Server BACKUP DATABASE, and separately preserve config.json and master.key. No backup created.')
dest=Path(a.destination);dest.mkdir(mode=0o700,parents=True,exist_ok=False)
with sqlite3.connect(url.database) as source, sqlite3.connect(dest/'members.sqlite3') as target:source.backup(target)
shutil.copy2(a.config,dest/'config.json');shutil.copy2(Path(config['DATA_DIR'])/'master.key',dest/'master.key')
print('Consistent SQLite backup complete; contains private credentials and encryption key. Keep protected.')
