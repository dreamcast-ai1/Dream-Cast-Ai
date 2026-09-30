#!/usr/bin/env bash
# Starts backend (http://localhost:8000) and frontend (http://localhost:5173).
set -euo pipefail
cd "$(dirname "$0")/.."
(cd backend && .venv/bin/alembic upgrade head)
(cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000) &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT
(cd frontend && npm run dev)
