"""Read secrets from environment or a local .env. No secrets committed to Git."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env', override=False)


def required(name: str) -> str:
    value = os.getenv(name, '').strip()
    if not value:
        raise RuntimeError(f'Missing required environment variable: {name}. See .env.example')
    return value


def database_url() -> str:
    value = required('DATABASE_URL')
    if not value.startswith(('postgresql://', 'postgres://')):
        raise RuntimeError('DATABASE_URL must be a PostgreSQL connection string')
    return value


def r2_config() -> dict:
    endpoint = os.getenv('R2_ENDPOINT', '').strip()
    if not endpoint:
        endpoint = f"https://{required('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com"
    if not endpoint.startswith('https://'):
        raise RuntimeError('R2_ENDPOINT must use HTTPS')
    return {
        'endpoint': endpoint.rstrip('/'),
        'bucket': required('R2_BUCKET_NAME'),
        'key_id': required('R2_ACCESS_KEY_ID'),
        'secret': required('R2_SECRET_ACCESS_KEY'),
    }
