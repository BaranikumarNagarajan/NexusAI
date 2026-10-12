# NexusAI in one container: Agent B + Agent A (A2A) + API + web app, with Tesseract OCR.
# Runs unchanged on Hugging Face Spaces (port 7860), Render, Railway, Fly.io (they set PORT).
FROM python:3.11-slim

# OCR engine: English + orientation detection (for scans and sideways phone photos).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# OMP_THREAD_LIMIT=1: one Tesseract thread is ~4x faster on small shared hosting CPUs (63 s -> 17 s a page).
# MALLOC_ARENA_MAX=2: stops each Python thread keeping its own memory pool (fits 512 MB plans).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    API_HOST=0.0.0.0 \
    PORT=7860 \
    TRUST_PROXY_HEADERS=true \
    OMP_THREAD_LIMIT=1 \
    MALLOC_ARENA_MAX=2

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend backend
COPY frontend frontend

# Run as a normal user (uid 1000, as Hugging Face Spaces expects); it owns the Kaggle download folder.
RUN useradd --create-home --uid 1000 nexus \
    && mkdir -p backend/samples/kaggle \
    && chown -R nexus backend/samples
USER nexus

EXPOSE 7860
CMD ["python", "backend/run.py", "--no-browser"]
