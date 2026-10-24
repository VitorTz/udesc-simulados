"""Cookie authentication: PBKDF2-SHA256 passwords, opaque database sessions and CSRF tokens."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from db import database

COOKIE_NAME = 'udesc_session'
SESSION_DAYS = 30
PBKDF2_ITERATIONS = 600_000
COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'true').strip().lower() != 'false'
EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', re.ASCII)


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise HTTPException(422, 'Informe um e-mail válido')
    return email


def validate_password(password: str) -> None:
    if not 12 <= len(password) <= 128:
        raise HTTPException(422, 'A senha deve ter entre 12 e 128 caracteres')


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ITERATIONS)
    return f'pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}'


def verify_password(password: str, encoded: str | None) -> bool:
    try:
        _, count, salt, expected = (encoded or '').split('$')
        if not 200_000 <= int(count) <= 2_000_000:
            return False
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(count))
        return hmac.compare_digest(digest, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_session(user_id: int) -> tuple[str, str, datetime]:
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_hex(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    with database() as db:
        db.execute('DELETE FROM user_sessions WHERE expires_at<=now()')
        db.execute('''INSERT INTO user_sessions(token_hash, user_id, csrf_token, expires_at)
                      VALUES (%s, %s, %s, %s)''', (token_digest(token), user_id, csrf, expires))
    return token, csrf, expires


def current_session(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    with database() as db:
        row = db.execute('''SELECT s.user_id, s.csrf_token, u.email
            FROM user_sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=%s AND s.expires_at>now()''', (token_digest(token),)).fetchone()
    return dict(row) if row else None


def user_id(request: Request) -> int:
    owner = getattr(request.state, 'user_id', None)
    if owner is None:
        raise HTTPException(401, 'Faça login para continuar')
    return owner


def issue_cookie(response, token: str, expires: datetime):
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_DAYS * 86400, expires=expires,
                        secure=COOKIE_SECURE, httponly=True, samesite='lax', path='/')
    response.headers['Cache-Control'] = 'no-store'


def clear_cookie(response):
    response.delete_cookie(COOKIE_NAME, secure=COOKIE_SECURE, httponly=True,
                           samesite='lax', path='/')
    response.headers['Cache-Control'] = 'no-store'
