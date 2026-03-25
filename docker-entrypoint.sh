#!/bin/sh
# docker-entrypoint.sh
set -e

echo "──────────────────────────────────────────────"
echo "  Akshaya Vistara — Container Starting"
echo "──────────────────────────────────────────────"

# ── Debug: Check if DATABASE_URL exists ──────────────────────────────────────
if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL is not set! Check your Render Environment variables."
    # We don't exit here so we can see the app crash with a Django error instead
else
    echo "DATABASE_URL is set (hiding details for security)."
fi

# ── Wait for PostgreSQL ───────────────────────────────────────────────────────
echo "Waiting for PostgreSQL to be ready..."
MAX_TRIES=15
count=0
until python -c "
import os, psycopg2, sys
try:
    url = os.environ.get('DATABASE_URL', '')
    if not url: sys.exit(1)
    conn = psycopg2.connect(url, connect_timeout=3)
    conn.close()
    sys.exit(0)
except Exception as e:
    # Print the error so we can debug it in Render logs
    print(f'  Detail: {e}')
    sys.exit(1)
"; do
    count=$((count + 1))
    if [ $count -ge $MAX_TRIES ]; then
        echo "WARNING: PostgreSQL check timed out. Attempting to run migrations anyway..."
        break
    fi
    echo "  PostgreSQL not ready yet... ($count/$MAX_TRIES)"
    sleep 2
done

# ── Run migrations ────────────────────────────────────────────────────────────
echo "Running database migrations..."
python manage.py migrate --noinput || echo "Migration failed! Check DB connection."

echo "Startup complete. Starting Gunicorn..."
echo "──────────────────────────────────────────────"

exec "$@"
