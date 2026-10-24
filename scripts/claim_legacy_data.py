"""Assign pre-migration editions/attempts to an existing account, once.

Usage: python -m scripts.claim_legacy_data --email you@example.com

Only rows without an owner are claimed; existing tenant data is never reassigned.
Requires DATABASE_MIGRATION_URL (or DATABASE_URL) with administrative permission.
"""
from __future__ import annotations
import argparse
import os
from db import database


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--email', required=True)
    parser.add_argument('--execute', action='store_true', help='Actually perform changes (default: preview)')
    args = parser.parse_args()
    email = args.email.strip().lower()
    with database(os.getenv('DATABASE_MIGRATION_URL') or None) as db:
        user = db.execute('SELECT id FROM users WHERE email=%s', (email,)).fetchone()
        if not user:
            parser.error('Account not found; register on the site first')
        editions = list(db.execute('SELECT id, label FROM editions WHERE user_id IS NULL ORDER BY id'))
        attempts = db.execute('SELECT count(*) AS n FROM attempts WHERE user_id IS NULL').fetchone()['n']
        print(f'Unassigned editions: {[r["label"] for r in editions]}')
        print(f'Unassigned attempts: {attempts}')
        if not args.execute:
            print('Preview only. Re-run with --execute after verifying destination account.')
            return
        db.execute('UPDATE editions SET user_id=%s WHERE user_id IS NULL', (user['id'],))
        db.execute('UPDATE attempts SET user_id=%s WHERE user_id IS NULL', (user['id'],))
        print('Legacy data assigned to account:', email)


if __name__ == '__main__':
    main()
