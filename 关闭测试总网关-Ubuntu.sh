#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
exec .venv/bin/python -X utf8 scripts/gateway.py stop
