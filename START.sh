#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "[1/3] Installing backend dependencies..."
cd backend
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created backend/.env from .env.example — edit it to add real credentials."
fi

echo "[2/3] Starting backend on http://localhost:8502 ..."
(python -m uvicorn app.main:app --host 0.0.0.0 --port 8502 --reload &)

cd ../frontend
echo "[3/3] Starting frontend on http://localhost:8501 ..."
python3 -m http.server 8501
