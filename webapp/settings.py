"""Django settings for the user platform (webapp/). Everything that differs per machine comes from the environment
(or .local/database.env, read by project_paths), never from this file:

    CRAWL4AI_APP_URL        the web app's database login (default postgresql://crawl4ai_app@localhost:5432/crawl4ai)
    WEBAPP_SECRET_KEY       Django's secret key; if unset, one is made once and kept in .local/webapp_secret_key
    WEBAPP_DEBUG            1 shows error details (default 1 on this Mac; 0 on the server)
    WEBAPP_ALLOWED_HOSTS    comma-separated host names (default localhost,127.0.0.1)
    WEBAPP_HTTPS            1 when served over HTTPS: secure cookies, HSTS (the server, phase 8)
"""

import os
import secrets
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

import project_paths
from warehouse import postgres

project_paths.apply_local_env()

BASE_DIR = Path(__file__).resolve().parent


def _secret_key() -> str:
    """WEBAPP_SECRET_KEY, or a random key made once and kept in .local/ (git-ignored), so logins survive restarts."""
    if os.environ.get("WEBAPP_SECRET_KEY"):
        return os.environ["WEBAPP_SECRET_KEY"]
    path = project_paths.LOCAL_DIR / "webapp_secret_key"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        key = secrets.token_urlsafe(50)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key, encoding="utf-8")
        path.chmod(0o600)
        return key


SECRET_KEY = _secret_key()
DEBUG = os.environ.get("WEBAPP_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("WEBAPP_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
HTTPS = os.environ.get("WEBAPP_HTTPS") == "1"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_htmx",
    "axes",
    "webapp.core",
    "webapp.accounts",
    "webapp.audit",
    "webapp.gridtest",
    "webapp.catalog",
    "webapp.explore",
    "webapp.insights",
    "webapp.foresight",
    "webapp.articles",
    "webapp.userdata",
    "webapp.charts",
    "webapp.reports",
    "webapp.assistant",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "axes.middleware.AxesMiddleware",           # last: turns a locked-out login into the "locked" page
]

ROOT_URLCONF = "webapp.urls"
WSGI_APPLICATION = "webapp.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "webapp.core.context.menu",
    ]},
}]


def _database() -> dict:
    """The web app's PostgreSQL login. Its own tables go in schema `app` (search_path); the collected data is read
    with schema-qualified names (companies.companies, news.news_items, ...)."""
    info = conninfo_to_dict(postgres.url("app"))
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": info.get("dbname") or "crawl4ai",
        "USER": info.get("user") or "crawl4ai_app",
        "PASSWORD": info.get("password") or "",
        "HOST": info.get("host") or "localhost",
        "PORT": info.get("port") or "5432",
        "OPTIONS": {"options": f"-c search_path={postgres.APP_SCHEMA},public"},   # public: extensions (pg_trgm)
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }


DATABASES = {"default": _database()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Passwords: Argon2 first (the recommended hasher); the others only read older hashes
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",     # first: refuses logins while an account is locked
    "django.contrib.auth.backends.ModelBackend",
]

# django-axes: 5 failed logins for a user name from one address lock it for 15 minutes
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 0.25                       # hours
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_LOCKOUT_TEMPLATE = "registration/locked.html"
AXES_RESET_ON_SUCCESS = True

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_AGE = 12 * 3600                 # a working day
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False                   # HTMX and the grid read it to send edits
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = HTTPS
if HTTPS:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Asia/Colombo"
USE_I18N = False
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = project_paths.REPO_ROOT / "output" / "webapp-static"   # collectstatic, for the server (phase 8)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"axes": {"level": "WARNING"}, "weasyprint": {"level": "ERROR"}, "fontTools": {"level": "ERROR"}},
}
