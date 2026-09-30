#!/usr/bin/env bash
# One-time setup: Python venv, deps, .env, DB migrations, frontend deps.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example"; }
if grep -q '^AUTH_SECRET_KEY=$' .env 2>/dev/null; then
  KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
  sed -i.bak "s|^AUTH_SECRET_KEY=.*|AUTH_SECRET_KEY=$KEY|" .env && rm -f .env.bak
  echo "Generated a random AUTH_SECRET_KEY in .env"
fi

python3 -m venv backend/.venv
backend/.venv/bin/pip install -q --upgrade pip
backend/.venv/bin/pip install -q -r backend/requirements-dev.txt
(cd backend && .venv/bin/alembic upgrade head)
(cd frontend && npm install --no-audit --no-fund)
echo "Setup complete. Run ./scripts/dev.sh to start."
