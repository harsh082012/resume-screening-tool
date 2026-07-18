# Dockerfile for the HTTP-mode Slack bot on Render.
# Render's native Python runtime can't install system packages like Tesseract,
# so we use Docker to get the OCR binaries (same ones nixpacks installed on Railway).

FROM python:3.10-slim

# System dependencies for OCR: Tesseract (image text) + poppler (PDF->image).
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project.
COPY . .

# Render provides $PORT at runtime. Single worker (shared in-memory JD store),
# multiple threads so the instant-ack + background screening works.
CMD gunicorn slack_bot:flask_app --workers 1 --threads 8 --bind 0.0.0.0:${PORT:-10000}