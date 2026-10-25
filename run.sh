#!/usr/bin/env sh
set -eu
# Run migrations before web startup. Never print secrets.
# python -m scripts.migrate
exec uvicorn app:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}" --proxy-headers
