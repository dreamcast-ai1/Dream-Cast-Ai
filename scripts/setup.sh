#!/usr/bin/env bash
# One-time setup: Python venv, deps, .env, DB migrations, frontend deps.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  # .env.example documents the deployed (production) values; a fresh local .env uses development ones.
  sed -i.bak -e 's|^APP_ENV=.*|APP_ENV=development|' -e 's|^CORS_ORIGINS=.*|CORS_ORIGINS=http://localhost:5173|' \
    -e 's|^FRONTEND_URL=.*|FRONTEND_URL=http://localhost:5173|' .env && rm -f .env.bak
  echo "Created .env from .env.example (development settings)"
fi
if grep -qE '^AUTH_SECRET_KEY=(REPLACE_WITH.*)?$' .env 2>/dev/null; then
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
