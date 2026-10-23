"""Explicit schema baseline; refuses unrelated or unversioned databases."""
from sqlalchemy import inspect, text
from .models import Base
VERSION = 1

def migrate(engine):
    existing = set(inspect(engine).get_table_names())
    if existing and 'sh_schema_version' not in existing:
        raise RuntimeError('Database is not an installer-managed database. Use an empty dedicated database; existing deployments require a reviewed baseline migration.')
    if existing:
        with engine.connect() as c:
            version = c.execute(text('SELECT version FROM sh_schema_version WHERE id=1')).scalar_one()
        if version != VERSION:
            raise RuntimeError('Unsupported schema version. Restore a compatible release; no schema was changed.')
        expected = set(Base.metadata.tables)
        if not expected.issubset(existing):
            raise RuntimeError('Database schema is incomplete. Restore a backup before continuing.')
        return
    with engine.begin() as c:
        Base.metadata.create_all(c)
        c.execute(text('CREATE TABLE sh_schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)'))
        c.execute(text('INSERT INTO sh_schema_version (id,version) VALUES (1,1)'))
