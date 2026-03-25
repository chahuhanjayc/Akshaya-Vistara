# ─────────────────────────────────────────────────────────────────────────────
# TallyPro — Dockerfile
# Base: Python 3.11 slim (Debian Bookworm)
# Includes: Tesseract OCR + Poppler (for PDF) installed via apt
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Prevent .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory inside the container
WORKDIR /app

# ── System dependencies ──────────────────────────────────────────────────────
# tesseract-ocr  → OCR engine
# poppler-utils  → PDF → image conversion (pdf2image)
# libpq-dev      → PostgreSQL C headers (needed to compile psycopg2)
# gcc            → C compiler for psycopg2 and other C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr \
      tesseract-ocr-eng \
      poppler-utils \
      libpq-dev \
      gcc \
      curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ──────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Copy project source ──────────────────────────────────────────────────────
COPY . .

# ── Collect static files ─────────────────────────────────────────────────────
# Uses a dummy SECRET_KEY — real key must be provided at runtime via .env
RUN SECRET_KEY=build-time-placeholder \
    DATABASE_URL=sqlite:////tmp/build.db \
    DEBUG=False \
    python manage.py collectstatic --noinput --settings=tally_pro.settings

# ── Non-root user for security ───────────────────────────────────────────────
RUN addgroup --system appgroup && adduser --system --group appuser
RUN chown -R appuser:appgroup /app
USER appuser

# ── Expose port 8000 ─────────────────────────────────────────────────────────
EXPOSE 8000

# ── Entrypoint ───────────────────────────────────────────────────────────────
COPY docker-entrypoint.sh /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["gunicorn", "tally_pro.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
