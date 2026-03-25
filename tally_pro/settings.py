"""
tally_pro/settings.py
Production-ready Django settings for tally_pro.
"""

import environ
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Base directory
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["127.0.0.1", "localhost"]),
)

environ.Env.read_env(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Core settings
# ---------------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    # Django built-ins
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "crispy_forms",
    "crispy_bootstrap5",
    # Local apps
    "core",
    "accounts",
    "ledger",
    "vouchers",
    "ocr",
    "reports",
    "inventory",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Custom: injects current_company into request
    "core.middleware.CurrentCompanyMiddleware",
]

ROOT_URLCONF = "tally_pro.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Custom: makes current_company available in all templates
                "core.context_processors.current_company",
            ],
        },
    },
]

WSGI_APPLICATION = "tally_pro.wsgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db(default="sqlite:///db.sqlite3"),
}

# ---------------------------------------------------------------------------
# Custom user model
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static and media files
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---------------------------------------------------------------------------
# OCR / Tesseract / Poppler (Dynamic for Windows/Linux)
# ---------------------------------------------------------------------------
# Render (Linux) will find these in the PATH automatically.
# Windows uses the default paths provided below.
TESSERACT_PATH = env(
    "TESSERACT_PATH",
    default="tesseract" if os.name != "nt" else r"C:\Program Files\Tesseract-OCR\tesseract.exe",
)
POPPLER_PATH = env(
    "POPPLER_PATH",
    default=None if os.name != "nt" else r"C:\poppler\Library\bin",
)

# ---------------------------------------------------------------------------
# Default primary key
# ---------------------------------------------------------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Celery (async OCR and background tasks)
# ---------------------------------------------------------------------------
# Broker URL — override in .env for production Redis:
#   CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT     = ["json"]
CELERY_TASK_SERIALIZER    = "json"
CELERY_RESULT_SERIALIZER  = "json"
CELERY_TIMEZONE           = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT    = 300   # 5-minute hard limit per task

# ── Development / testing without Redis ──────────────────────────────────────
# Set CELERY_TASK_ALWAYS_EAGER=True in .env to run tasks synchronously
# (simulates async behaviour inline — no worker process needed).
CELERY_TASK_ALWAYS_EAGER          = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES      = True

# ---------------------------------------------------------------------------
# Crispy forms
# ---------------------------------------------------------------------------
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# ---------------------------------------------------------------------------
# Auth redirects
# ---------------------------------------------------------------------------
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/core/select-company/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
SESSION_COOKIE_AGE = 86400 * 7  # 7 days
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# ---------------------------------------------------------------------------
# Security (production overrides)
# ---------------------------------------------------------------------------
if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
