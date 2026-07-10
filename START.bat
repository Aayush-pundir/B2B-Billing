@echo off
cd /d "%~dp0"

echo [1/3] Installing backend dependencies...
cd backend
if not exist .env copy .env.example .env
py -3.13 -m pip install -r requirements.txt --only-binary :all: --upgrade

echo [2/3] Starting backend on http://localhost:8502 ...
start "Billing Backend" py -3.13 -m uvicorn app.main:app --host 0.0.0.0 --port 8502 --reload

cd ..\frontend
echo [3/3] Starting frontend on http://localhost:8501 ...
start "" http://localhost:8501
py -3.13 -m http.server 8501
