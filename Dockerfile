 # ─────────────────────────────────────────────────────────────────────────────
  # Akshaya Vistara — Dockerfile
  # ─────────────────────────────────────────────────────────────────────────────
    FROM python:3.11-slim
 
  # Prevent .pyc files and enable unbuffered stdout/stderr
  ENV PYTHONDONTWRITEBYTECODE=1
  ENV PYTHONUNBUFFERED=1
  # Ensure Python can find the 'tally_pro' module in the current directory
  ENV PYTHONPATH=/app
 
  # Set working directory inside the container
  WORKDIR /app
 
  # ── System dependencies ──────────────────────────────────────────────────────
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
  # We explicitly set PYTHONPATH=. and specify the settings module
  RUN PYTHONPATH=. SECRET_KEY=build-time-placeholder \
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
  # Make sure docker-entrypoint.sh has Linux (LF) line endings!
  RUN chmod +x docker-entrypoint.sh
  ENTRYPOINT ["./docker-entrypoint.sh"]
 
  CMD ["gunicorn", "tally_pro.wsgi:application", \
       "--bind", "0.0.0.0:8000", \
       "--workers", "3", \
       "--timeout", "120", \
       "--access-logfile", "-", \
       "--error-logfile", "-"]
