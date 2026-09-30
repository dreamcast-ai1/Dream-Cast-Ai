#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../backend"
.venv/bin/pytest -q
cd ../frontend && npm run typecheck && npm run build
