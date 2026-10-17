FROM python:3.11-slim

WORKDIR /app

# Deliberately installs only the lightweight core requirements — the
# optional OCR extras (PyMuPDF + easyocr, which pull in torch/torchvision)
# live in backend/requirements-ocr.txt and are NOT installed here. They're
# multi-gigabyte and blow past free-tier build memory/disk/time on hosts
# like Render. DOCX and digital-PDF extraction work fine without them;
# scanned-PDF/image OCR returns a clear "not installed" message instead of
# crashing. See backend/requirements-ocr.txt for how to add them on a host
# with real build resources.
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
