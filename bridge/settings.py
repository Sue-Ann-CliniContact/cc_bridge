"""Django settings for the CliniContact Bridge project."""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-dev-only-replace-me')

DEBUG = os.getenv('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = [h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'constance',
    'constance.backends.database',
    'accounts',
    'core',
    'ai_manager',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

CONSTANCE_BACKEND = 'constance.backends.database.DatabaseBackend'
CONSTANCE_CONFIG = {
    'ACTIVE_AI_PROVIDER': ('', 'Name of the active AIProvider row (blank = use default Anthropic)', str),
}

ROOT_URLCONF = 'bridge.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'bridge.wsgi.application'
ASGI_APPLICATION = 'bridge.asgi.application'


DATABASES = {
    'default': dj_database_url.config(
        default=os.getenv('DB_INTERNAL', f'sqlite:///{BASE_DIR / "db.sqlite3"}'),
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
if not DEBUG:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'home'
LOGIN_REDIRECT_URL = 'dashboard'


# ─── Integration credentials ───────────────────────────
MONDAY_CLIENT_ID = os.getenv('MONDAY_CLIENT_ID', '')
MONDAY_CLIENT_SECRET = os.getenv('MONDAY_CLIENT_SECRET', '')
MONDAY_REDIRECT_URI = os.getenv('MONDAY_REDIRECT_URI', 'http://localhost:8000/oauth/callback/')
MONDAY_WORKSPACE_ID = os.getenv('MONDAY_WORKSPACE_ID', '')

INSTANTLY_API_KEY = os.getenv('INSTANTLY_API_KEY', '')

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
ANTHROPIC_DEFAULT_MODEL = os.getenv('ANTHROPIC_DEFAULT_MODEL', 'claude-sonnet-4-6')

APOLLO_API_KEY = os.getenv('APOLLO_API_KEY', '')
APOLLO_MONTHLY_BUDGET_CREDITS = int(os.getenv('APOLLO_MONTHLY_BUDGET_CREDITS', '1000'))

APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://localhost:8000')


# ─── HTTPS / CSRF (Render production) ──────────────────
# Django 4+ requires CSRF_TRUSTED_ORIGINS for POSTs over HTTPS. Default to
# APP_BASE_URL so the Render domain Just Works; allow an env override for
# multi-host setups (comma-separated).
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv('CSRF_TRUSTED_ORIGINS', APP_BASE_URL).split(',')
    if origin.strip() and origin.strip().startswith(('http://', 'https://'))
]

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
