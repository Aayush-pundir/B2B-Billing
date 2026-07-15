#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "[1/2] Installing dependencies..."
cd backend
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created backend/.env from .env.example — edit it to add real credentials."
fi

PORT="${PORT:-8000}"
echo "[2/2] Starting app on http://localhost:${PORT} (frontend + API, one process) ..."
python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload
