#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
exec gunicorn --config gunicorn.conf.py wsgi:app
