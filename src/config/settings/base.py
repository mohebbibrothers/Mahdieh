"""تنظیمات پایهٔ مهدیه — مشترک میان همهٔ محیط‌ها.

قواعد:
    • هیچ رازی در کد نیست؛ همه از محیط خوانده می‌شود.
    • هیچ مقدار جادویی در کد کسب‌وکار نیست؛ سیاست‌ها در جدول Policy هستند.
    • هر متغیر محیطی اینجا خوانده شود، در ``.env.example`` مستند است
      (تست ``test_env_example_parity`` این را اجرا می‌کند).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import environ

# ─── مسیرها ───────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
SRC_DIR = BASE_DIR / "src"

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# ─── عمومی ────────────────────────────────────────────────────────────────────
ENVIRONMENT = env.str("MAHDIEH_ENVIRONMENT", default="development")
DEBUG = env.bool("MAHDIEH_DEBUG", default=False)
SECRET_KEY = env.str("MAHDIEH_SECRET_KEY", default="insecure-development-key")
ALLOWED_HOSTS = env.list("MAHDIEH_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
SITE_URL = env.str("MAHDIEH_SITE_URL", default="http://127.0.0.1:8000")
ADMINS = [("مدیر مهدیه", "admin@example.com")]

# ─── بومی‌سازی ────────────────────────────────────────────────────────────────
LANGUAGE_CODE = env.str("MAHDIEH_LANGUAGE_CODE", default="fa")
TIME_ZONE = env.str("MAHDIEH_TIME_ZONE", default="Asia/Tehran")
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [SRC_DIR / "locale"]

# ─── اپلیکیشن‌ها ──────────────────────────────────────────────────────────────
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "drf_spectacular",
]

LOCAL_APPS = [
    "apps.core",
    "apps.web",
    "apps.identity",
    "apps.audit",
    "apps.notifications",
    "apps.reporting",
    "apps.inventory",
    "apps.reservation",
]

INSTALLED_APPS = [*DJANGO_APPS, *THIRD_PARTY_APPS, *LOCAL_APPS]

# ─── میان‌افزارها ─────────────────────────────────────────────────────────────
# ترتیب مهم است:
#   RequestContext ابتدا ⇒ همهٔ لایه‌ها شناسهٔ درخواست و ترِیس داشته باشند.
#   CSP پس از RequestContext ⇒ nonce تولید و در پاسخ تزریق شود.
MIDDLEWARE = [
    "apps.core.middleware.RequestContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.AccessLogMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES: list[dict[str, Any]] = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [SRC_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
            ],
            "builtins": ["django.templatetags.i18n"],
        },
    },
]

# ─── دیتابیس ──────────────────────────────────────────────────────────────────
DATABASES = {
    "default": {
        **env.db_url("DATABASE_URL", default="postgres://mahdieh:mahdieh@127.0.0.1:5432/mahdieh"),
        "CONN_MAX_AGE": env.int("DATABASE_CONN_MAX_AGE", default=60),
        "CONN_HEALTH_CHECKS": env.bool("DATABASE_CONN_HEALTH_CHECKS", default=True),
        "OPTIONS": {
            # محدودیت زمان اجرای کوئری: هیچ کوئری سرگردانی نباید کانکشن را اشغال کند
            "options": (
                f"-c statement_timeout={env.int('DATABASE_STATEMENT_TIMEOUT_MS', default=5000)}"
            ),
            "connect_timeout": 5,
        },
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─── کش و صف (Valkey — سازگار با پروتکل Redis) ────────────────────────────────
CACHE_KEY_PREFIX = env.str("CACHE_KEY_PREFIX", default="mahdieh")
CACHE_DEFAULT_TTL_SECONDS = env.int("CACHE_DEFAULT_TTL_SECONDS", default=300)
VALKEY_URL = env.str("VALKEY_URL", default="redis://127.0.0.1:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": VALKEY_URL,
        "KEY_PREFIX": CACHE_KEY_PREFIX,
        "TIMEOUT": CACHE_DEFAULT_TTL_SECONDS,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "IGNORE_EXCEPTIONS": True,  # قطعی کش نباید سایت را بخواباند
            "SOCKET_CONNECT_TIMEOUT": 2,
            "SOCKET_TIMEOUT": 2,
            "CONNECTION_POOL_KWARGS": {"max_connections": 64},
        },
    }
}
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

CELERY_BROKER_URL = env.str("CELERY_BROKER_URL", default="redis://127.0.0.1:6379/1")
CELERY_RESULT_BACKEND = env.str("CELERY_RESULT_BACKEND", default="redis://127.0.0.1:6379/2")

# ─── رمز عبور (سیاست سخت) ─────────────────────────────────────────────────────
AUTH_USER_MODEL = "identity.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# ─── API ──────────────────────────────────────────────────────────────────────
REST_FRAMEWORK: dict[str, Any] = {
    "EXCEPTION_HANDLER": "apps.core.problems.problem_details_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticatedOrReadOnly"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.CursorPageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "240/min",
        # سقف‌های دامنه‌ای (فاز ۱ و ۳): کد ورود، تأیید کد، عملیات حساس کارکنان
        "otp_request": "5/min",
        "otp_verify": "10/min",
        "sensitive_action": "60/min",
    },
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    # پنهان‌سازی اطلاعات: هیچ‌گاه جزئیات داخلی در پاسخ خطا لو نمی‌رود
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}

SPECTACULAR_SETTINGS: dict[str, Any] = {
    "TITLE": "Mahdieh Reservation Platform API",
    "DESCRIPTION": (
        "قرارداد رسمی API سامانهٔ مهدیه.\n\n"
        "• خطاها بر پایهٔ RFC 9457 (application/problem+json)\n"
        "• هم‌توانی نوشتن با هدر Idempotency-Key\n"
        "• نسخه‌بندی با Deprecation/Sunset (RFC 8594)\n"
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "ENUM_NAME_OVERRIDES": {},
    "POSTPROCESSING_HOOKS": [],
}

# ─── امنیت ────────────────────────────────────────────────────────────────────
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=False)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=False)
CSRF_COOKIE_HTTPONLY = False  # HTMX برای ارسال توکن به آن نیاز دارد
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
X_FRAME_OPTIONS = "DENY"

# سیاست امنیت محتوا — CSP بومی Django 6 با nonce (``LazyNonce``).
# نکتهٔ Django 6.1: ``SECURE_CSP`` سیاست «اعمال‌شده» است و
# ``SECURE_CSP_REPORT_ONLY`` سیاست «فقط-گزارشی». این دو جدا هستند و نباید
# هم‌زمان یک سیاست را دو بار تعریف کنند.
_CSP_POLICY: dict[str, list[str]] = {
    "default-src": ["'self'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
    "frame-ancestors": ["'none'"],
    "object-src": ["'none'"],
    # nonce جنگو به‌صورت خودکار به این‌ها افزوده می‌شود
    "script-src": ["'self'"],
    "style-src": ["'self'"],
    "img-src": ["'self'", "data:", "blob:"],
    "font-src": ["'self'", "data:"],
    "connect-src": ["'self'"],
    "worker-src": ["'self'"],
    "manifest-src": ["'self'"],
}
if env.str("CSP_REPORT_URI", default=""):
    _CSP_POLICY["report-uri"] = [env.str("CSP_REPORT_URI")]

CSP_REPORT_ONLY_MODE = env.bool("CSP_REPORT_ONLY", default=True)
CSP_POLICY_ENFORCED: dict[str, list[str]] = _CSP_POLICY  # سیاست نهایی، برای محیط‌ها
SECURE_CSP: dict[str, list[str]] = {} if CSP_REPORT_ONLY_MODE else _CSP_POLICY
SECURE_CSP_REPORT_ONLY: dict[str, list[str]] = _CSP_POLICY if CSP_REPORT_ONLY_MODE else {}

# ─── رمزنگاری دادهٔ حساس ──────────────────────────────────────────────────────
FIELD_ENCRYPTION_KEY = env.str("FIELD_ENCRYPTION_KEY", default="")
FIELD_HASH_KEY = env.str("FIELD_HASH_KEY", default="")

# ─── پیامک ────────────────────────────────────────────────────────────────────
# ─── کد یک‌بارمصرف (OTP) ──────────────────────────────────────────────────────
# این اعداد «سیاست امنیتی» هستند: کوتاه‌کردن TTL یا برداشتن فاصلهٔ ارسال،
# هزینهٔ پیامک و ریسک سوءاستفاده را بالا می‌برد. تغییرشان نیازمند دلیل است.
OTP_TTL_SECONDS = env.int("OTP_TTL_SECONDS", default=120)
OTP_RESEND_COOLDOWN_SECONDS = env.int("OTP_RESEND_COOLDOWN_SECONDS", default=60)
OTP_MAX_ATTEMPTS = env.int("OTP_MAX_ATTEMPTS", default=5)
OTP_WINDOW_SECONDS = env.int("OTP_WINDOW_SECONDS", default=600)
OTP_MAX_PER_WINDOW = env.int("OTP_MAX_PER_WINDOW", default=3)

SMS_ENABLED = env.bool("SMS_ENABLED", default=False)
SMS_PROVIDER = env.str("SMS_PROVIDER", default="mock")
SMS_API_KEY = env.str("SMS_API_KEY", default="")
SMS_SENDER_NUMBER = env.str("SMS_SENDER_NUMBER", default="")
SMS_FALLBACK_PROVIDER = env.str("SMS_FALLBACK_PROVIDER", default="")
SMS_DAILY_BUDGET = env.int("SMS_DAILY_BUDGET", default=1000)

# ─── رسانه ────────────────────────────────────────────────────────────────────
STORAGE_BACKEND = env.str("STORAGE_BACKEND", default="local")
S3_ENDPOINT_URL = env.str("S3_ENDPOINT_URL", default="")
S3_ACCESS_KEY = env.str("S3_ACCESS_KEY", default="")
S3_SECRET_KEY = env.str("S3_SECRET_KEY", default="")
S3_BUCKET = env.str("S3_BUCKET", default="")
S3_REGION = env.str("S3_REGION", default="us-east-1")
SIGNED_URL_TTL_SECONDS = env.int("SIGNED_URL_TTL_SECONDS", default=900)

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [SRC_DIR / "static"] if (SRC_DIR / "static").exists() else []
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ─── ایمیل (روش مدرن Django 6.1: دیکشنری MAILERS) ─────────────────────────────
MAILERS: dict[str, dict[str, Any]] = {
    "default": {
        "BACKEND": env.str(
            "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
        ),
        "OPTIONS": {
            "host": env.str("EMAIL_HOST", default="localhost"),
            "port": env.int("EMAIL_PORT", default=25),
            "username": env.str("EMAIL_HOST_USER", default=""),
            "password": env.str("EMAIL_HOST_PASSWORD", default=""),
            "use_tls": env.bool("EMAIL_USE_TLS", default=False),
        },
    }
}

# ─── مشاهده‌پذیری ─────────────────────────────────────────────────────────────
LOG_LEVEL = env.str("LOG_LEVEL", default="INFO")
LOG_FORMAT = env.str("LOG_FORMAT", default="console")
OTEL_ENABLED = env.bool("OTEL_ENABLED", default=False)
OTEL_EXPORTER_OTLP_ENDPOINT = env.str("OTEL_EXPORTER_OTLP_ENDPOINT", default="")
OTEL_SERVICE_NAME = env.str("OTEL_SERVICE_NAME", default="mahdieh-web")
SENTRY_DSN = env.str("SENTRY_DSN", default="")

# ─── خروجی‌ها ─────────────────────────────────────────────────────────────────
EXPORT_MAX_ROWS = env.int("EXPORT_MAX_ROWS", default=200_000)
EXPORT_LINK_TTL_SECONDS = env.int("EXPORT_LINK_TTL_SECONDS", default=86_400)

# ─── سلامت ────────────────────────────────────────────────────────────────────
HEALTH_DETAILED_PUBLIC = env.bool("HEALTH_DETAILED_PUBLIC", default=False)

# ─── ورود و خروج ──────────────────────────────────────────────────────────────
# قرارداد: صفحهٔ ورود سایت در اپ «web» است؛ نام مسیرها (نه URL سخت‌کدشده) تا
# هر جابه‌جایی در مسیرها یک‌جا اثر کند.
# حالت نمایش: باز کردن مسیر ورود نمایشی (فقط توسعه). در تولید همیشه خاموش است.
DEMO_MODE = env.bool("MAHDIEH_DEMO_MODE", default=False)

LOGIN_URL = "web:login"
LOGIN_REDIRECT_URL = "web:my_reservations"
LOGOUT_REDIRECT_URL = "web:home"
