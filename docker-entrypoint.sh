#!/bin/sh
# docker-entrypoint.sh
# Runs before the main CMD (Gunicorn).
# Waits for PostgreSQL to be ready, then runs migrations.

set -e

echo "──────────────────────────────────────────────"
echo "  TallyPro — Container Starting"
echo "──────────────────────────────────────────────"

# ── Wait for PostgreSQL ───────────────────────────────────────────────────────
echo "Waiting for PostgreSQL to be ready..."
MAX_TRIES=30
count=0
until python -c "
import os, psycopg2, sys
try:
    conn = psycopg2.connect(os.environ.get('DATABASE_URL', ''))
    conn.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; do
    count=$((count + 1))
    if [ $count -ge $MAX_TRIES ]; then
        echo "ERROR: PostgreSQL not ready after $MAX_TRIES attempts. Aborting."
        exit 1
    fi
    echo "  PostgreSQL not ready yet... ($count/$MAX_TRIES)"
    sleep 2
done

echo "PostgreSQL is ready."

# ── Run migrations ────────────────────────────────────────────────────────────
echo "Running database migrations..."
python manage.py migrate --noinput

# ── Generate PWA icons (first run only) ──────────────────────────────────────
if [ ! -f "static/icons/icon-192.png" ]; then
    echo "Generating PWA icons..."
    python manage.py generate_pwa_icons || echo "Icon generation skipped (non-critical)."
fi

echo "Startup complete. Starting Gunicorn..."
echo "──────────────────────────────────────────────"

# Execute the CMD (gunicorn)
exec "$@"
