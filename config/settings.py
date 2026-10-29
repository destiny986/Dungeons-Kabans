"""Environment-driven configuration; persistent SQLite storage."""
import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
ENVIRONMENT = os.environ.get("DJANGO_ENV", "local")
IS_LOCAL = ENVIRONMENT == "local"
DEBUG = IS_LOCAL and os.environ.get("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes"}
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-development-only-change-before-deploy")
ALLOWED_HOSTS = [host.strip() for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]" if IS_LOCAL else "").split(",") if host.strip()]
if not IS_LOCAL and (not os.environ.get("DJANGO_SECRET_KEY") or SECRET_KEY == "local-development-only-change-before-deploy" or not ALLOWED_HOSTS):
    raise ImproperlyConfigured("Production requires DJANGO_SECRET_KEY and DJANGO_ALLOWED_HOSTS.")

DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data")).resolve()
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", DATA_DIR / "media")).resolve()
IMPORT_ROOT = Path(os.environ.get("IMPORT_ROOT", DATA_DIR / "imports")).resolve()
for directory in (DATA_DIR, MEDIA_ROOT, IMPORT_ROOT):
    directory.mkdir(parents=True, exist_ok=True)

INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "game.apps.GameConfig"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware", "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "db.sqlite3", "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"}, "ATOMIC_REQUESTS": False}}
AUTH_PASSWORD_VALIDATORS = [{"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"}, {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"}, {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"}, {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"}]
LANGUAGE_CODE = "ru"
TIME_ZONE = os.environ.get("TIME_ZONE", "Europe/Moscow")
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", BASE_DIR / "staticfiles")).resolve()
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
MEDIA_URL = "/media/"
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "dk_sessionid"
CSRF_COOKIE_NAME = "dk_csrftoken"
SESSION_COOKIE_AGE = 30 * 24 * 60 * 60
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not IS_LOCAL
CSRF_COOKIE_SECURE = not IS_LOCAL
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if origin.strip()]
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000" if IS_LOCAL else "").rstrip("/")
MASTER_PASSWORD = os.environ.get("MASTER_PASSWORD", "")

# Gunicorn is reachable only through the trusted loopback reverse proxy.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
