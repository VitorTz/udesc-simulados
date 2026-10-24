"""One-time migration of local SQLite content + local PDFs to PostgreSQL + R2.

Run ONLY after `python -m scripts.migrate`. Target PostgreSQL must be empty.
Old local SQLite / PDF files are read-only and never deleted.
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import uuid
from pathlib import Path
from db import database
from storage import upload_file, delete_object

TABLES = ('editions', 'documents', 'questions', 'notes', 'attempts', 'attempt_items')


def migrate(sqlite_path: Path, pdf_dir: Path):
    if not sqlite_path.is_file():
        raise SystemExit(f'Old SQLite database does not exist: {sqlite_path}')
    old = sqlite3.connect(f'file:{sqlite_path.resolve()}?mode=ro', uri=True)
    old.row_factory = sqlite3.Row
    objects = []
    try:
        data = {table: [dict(r) for r in old.execute(f'SELECT * FROM {table}')]
                for table in TABLES}
        print('Rows found:', {table: len(rows) for table, rows in data.items()})
        # Validate paths, upload originals. Do not read arbitrary locations from SQLite.
        for row in data['documents']:
            old_key = row['path']
            path = (pdf_dir / old_key).resolve()
            if not path.is_relative_to(pdf_dir.resolve()) or not path.is_file():
                raise SystemExit(f'PDF missing/unsafe: {old_key}. No data was deleted.')
            new_key = f'migrated/{uuid.uuid4().hex}/{row["role"]}.pdf'
            row['path'] = new_key
            objects.append((path, new_key))
        # No external writes until we verified that the target is empty.
        with database(os.getenv('DATABASE_MIGRATION_URL') or None) as conn:
            for table in ('editions', 'documents', 'questions', 'notes', 'attempts'):
                count = conn.execute(f'SELECT COUNT(*) AS count FROM {table}').fetchone()['count']
                if count:
                    raise SystemExit('PostgreSQL target is not empty. Migration aborted; no rows overwritten.')
        uploaded = []
        try:
            for path, remote in objects:
                upload_file(path, remote)
                uploaded.append(remote)
            with database(os.getenv('DATABASE_MIGRATION_URL') or None) as conn:
                # Serializable guarding is useful if another instance is importing simultaneously.
                conn.execute('SELECT pg_advisory_xact_lock(1837459022)')
                if conn.execute('SELECT COUNT(*) AS count FROM editions').fetchone()['count']:
                    raise RuntimeError('PostgreSQL target changed during migration')
                for table in TABLES:
                    for row in data[table]:
                        cols = list(row.keys())
                        sql = f'INSERT INTO {table} (' + ','.join(cols) + ') VALUES (' + ','.join(['%s']*len(cols)) + ')'
                        conn.execute(sql, tuple(row[c] for c in cols))
                for table in ('editions', 'documents', 'questions', 'attempts'):
                    conn.execute(f'''SELECT setval(pg_get_serial_sequence('{table}', 'id'),
                        GREATEST(COALESCE((SELECT MAX(id) FROM {table}),0),1),
                        (SELECT COUNT(*) > 0 FROM {table}))''')
            print('Migration completed. Your old SQLite and PDFs were preserved.')
        except Exception:
            for remote in uploaded:
                try:
                    delete_object(remote)
                except Exception:
                    print(f'WARNING: R2 orphan object cleanup required: {remote}')
            raise
    finally:
        old.close()


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description='SQLite -> PostgreSQL & Cloudflare R2')
    cli.add_argument('--sqlite', type=Path, default=Path('data/simulados.sqlite3'))
    cli.add_argument('--pdf-dir', type=Path, default=Path('data/pdfs'))
    args = cli.parse_args()
    migrate(args.sqlite, args.pdf_dir)
