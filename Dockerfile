FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Scanned-PDF / photo OCR is optional because it adds PyTorch (several GB). Build with --build-arg WITH_OCR=1 to include it.
ARG WITH_OCR=0
COPY backend/requirements-ocr.txt backend/requirements-ocr.txt
RUN if [ "$WITH_OCR" = "1" ]; then pip install --no-cache-dir -r backend/requirements-ocr.txt; fi

COPY backend backend
COPY frontend frontend

ENV DATABASE_URL=sqlite:////app/data/billing.db
ENV UPLOAD_DIR=/app/data/uploads
RUN mkdir -p /app/data/uploads

WORKDIR /app/backend
EXPOSE 8000

# Most hosts (Render, Railway, Fly) inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
