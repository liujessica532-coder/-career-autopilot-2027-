#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test -f .env || { echo 'Copy .env.example to .env and set POSTGRES_PASSWORD'; exit 1; }
python3 deploy/init_secrets.py
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d --build --wait
docker compose ps
curl --fail --silent http://127.0.0.1:8000/health
