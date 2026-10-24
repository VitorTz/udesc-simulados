"""Apply immutable SQL migrations transactionally. python -m scripts.migrate"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
from db import database

MIGRATIONS = Path(__file__).resolve().parents[1] / 'migrations'


def run():
    with database(os.getenv('DATABASE_MIGRATION_URL') or None) as conn:
        # Serialize deploys running migrations concurrently.
        conn.execute('SELECT pg_advisory_xact_lock(1837459021)')
        conn.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )''')
        done = {r['name']: r['checksum'] for r in conn.execute(
            'SELECT name,checksum FROM schema_migrations')}
        for path in sorted(MIGRATIONS.glob('*.sql')):
            sql = path.read_text(encoding='utf-8')
            checksum = hashlib.sha256(sql.encode('utf-8')).hexdigest()
            if path.name in done:
                if done[path.name] != checksum:
                    raise RuntimeError(f'Migration changed after being applied: {path.name}')
                print(f'Already applied: {path.name}')
                continue
            conn.execute(sql)
            conn.execute('INSERT INTO schema_migrations(name,checksum) VALUES(%s,%s)',
                         (path.name, checksum))
            print(f'Applied migration: {path.name}')


if __name__ == '__main__':
    run()
