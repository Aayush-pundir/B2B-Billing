FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY frontend frontend

ENV DATABASE_URL=sqlite:////app/data/billing.db
ENV UPLOAD_DIR=/app/data/uploads
RUN mkdir -p /app/data/uploads

WORKDIR /app/backend
EXPOSE 8000

# Most hosts (Render, Railway, Fly) inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
