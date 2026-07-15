@echo off
cd /d "%~dp0"

echo [1/2] Installing dependencies...
cd backend
if not exist .env copy .env.example .env
py -3.13 -m pip install -r requirements.txt --only-binary :all: --upgrade

echo [2/2] Starting app on http://localhost:8000 (frontend + API, one process) ...
start "" http://localhost:8000
py -3.13 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
