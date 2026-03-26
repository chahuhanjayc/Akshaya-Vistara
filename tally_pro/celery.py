"""
tally_pro/celery.py

Celery application instance for Akshaya Vistara.

Start a worker (requires Redis to be running):
    celery -A tally_pro worker -l info

To run beat (for periodic tasks — not used yet):
    celery -A tally_pro beat -l info

Development without Redis (eager / synchronous mode):
    Set CELERY_TASK_ALWAYS_EAGER=True in your .env to execute tasks
    inline in the request/response cycle. Useful for local dev without Redis.
"""

import os
from celery import Celery

# Tell Celery which Django settings module to use
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tally_pro.settings")

app = Celery("tally_pro")

# Pull Celery configuration from Django settings keys that start with CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py files inside each INSTALLED_APP
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Utility task — prints the request object for debugging."""
    print(f"Request: {self.request!r}")
