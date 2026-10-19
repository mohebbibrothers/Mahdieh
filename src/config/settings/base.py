"""تنظیمات پایهٔ مهدیه — مشترک میان همهٔ محیط‌ها.

قواعد:
    • هیچ رازی در کد نیست؛ همه از محیط خوانده می‌شود.
    • هیچ مقدار جادویی در کد کسب‌وکار نیست؛ سیاست‌ها در جدول Policy هستند.
    • هر متغیر محیطی اینجا خوانده شود، در ``.env.example`` مستند است
      (تست ``test_env_example_parity`` این را اجرا می‌کند).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import environ

from apps.core.xorigin import parse_origins

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
# نشانی عمومی سایت — مبنای لینک‌های چاپی (QR گواهی) و لینک‌های پیامکی.
# در نماهای وب، نشانی همان درخواست اولویت دارد؛ این مقدار برای جاهایی است که
# درخواستی در دست نیست (گزارش‌ها، کارهای پس‌زمینه).
SITE_URL = env.str("MAHDIEH_SITE_URL", default="http://127.0.0.1:8000")
# راه تماس پذیرش — در میز راهنما، برگ چاپی و پاورقی نشان داده می‌شود. تهی
# بمانَد، هیچ شمارهٔ ساختگی چاپ نمی‌شود (قالب، گزینهٔ جانشین نشان می‌دهد).
SUPPORT_PHONE = env.str("MAHDIEH_SUPPORT_PHONE", default="")
SUPPORT_ADDRESS = env.str("MAHDIEH_SUPPORT_ADDRESS", default="")
# هشدار رسمی Django 6.1: زوج‌های (نام، نشانی) در ADMINS از ۲۰۲۸ حذف می‌شوند.
# قالب امروز «فهرست نشانی‌ها» است؛ زودتر مهاجرت می‌کنیم تا هشدار انباشته نشود.
ADMINS = env.list("MAHDIEH_ADMINS", default=["admin@example.com"])

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
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "corsheaders",
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
    "apps.waitlist",
    "apps.analytics",
]

INSTALLED_APPS = [*DJANGO_APPS, *THIRD_PARTY_APPS, *LOCAL_APPS]

# ─── میان‌افزارها ─────────────────────────────────────────────────────────────
# ترتیب مهم است:
#   RequestContext ابتدا ⇒ همهٔ لایه‌ها شناسهٔ درخواست و ترِیس داشته باشند.
#   CSP پس از RequestContext ⇒ nonce تولید و در پاسخ تزریق شود.
MIDDLEWARE = [
    "apps.core.middleware.RequestContextMiddleware",
    # نخستین لایهٔ کاربردی: قطعیِ گذرای زیرساخت ⇒ ۵۰۳ محترمانه، نه ۵۰۰ خام.
    "apps.core.middleware.DatabaseResilienceMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    # CORS باید **پیش از** CommonMiddleware باشد تا پاسخ‌های خطای Common هم
    # هدرهای CORS بگیرند (نبود این ترتیب ⇒ فرانت به‌جای پیام خطا، خطای شبکه می‌بیند).
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # سد عامل دوم کارکنان (ADR-0018): پس از احراز هویت و پیام‌ها، پیش از هر نما.
    "apps.identity.middleware.StaffSecurityMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # آخرین لایه (نزدیک‌ترین به نما): جلوگیری از ماندن دادهٔ شخصی در کش‌ها.
    "apps.core.middleware.PersonalDataCacheMiddleware",
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
                "apps.web.context_processors.policy",
            ],
            "builtins": ["django.templatetags.i18n"],
        },
    },
]

# ─── سدِ ظرفیت (Load shedding) ────────────────────────────────────────────────
LOAD_SHEDDING_ENABLED = env.bool("LOAD_SHEDDING_ENABLED", default=True)
LOAD_SHEDDING_MAX_CONCURRENCY = env.int("LOAD_SHEDDING_MAX_CONCURRENCY", default=240)
#: سدِ WSGI پیش‌فرض **خاموش** است: با کارگر ``sync`` هر فرآیند یک درخواست را
#: هم‌زمان اجرا می‌کند، پس سقفِ مؤثرِ شمارنده = شمار کارگرها و روشن‌کردنش
#: ظرفیتِ سالم را هم دور می‌ریزد. سدِ این مدل Nginx است (ADR-0016). اگر به
#: کارگرِ رشته‌ای کوچیدید، همین را true کنید.
LOAD_SHEDDING_WSGI_ENABLED = env.bool("LOAD_SHEDDING_WSGI_ENABLED", default=False)

# ─── پایگاه‌داده: MariaDB 11.4 LTS (ADR-0020) ─────────────────────────────────
# چرا MariaDB؟ میزبان cPanel کارفرما PostgreSQL 13 بدون contrib دارد (جنگو ۶٫۱
# دست‌کم ۱۵ می‌خواهد) و MariaDB 11.4 بومی، پشتیبان‌گیری‌شده و آزموده است.
# همهٔ قابلیت‌های حیاتی پیش از این تصمیم روی **خودِ میزبان** سنجیده شد: تریگر
# ضدهم‌پوشانی، قید CHECK، یکتایی شرطی با ستون مجازی، SKIP LOCKED و هم‌زمانی.
#
# • ``isolation_level = read committed``: هر دستور، تازه‌ترین دادهٔ ثبت‌شده را
#   می‌بیند؛ تریگر ضدهم‌پوشانی پس از گرفتن قفل ردیف واحد، رزرو هم‌زمانِ تازه
#   ثبت‌شده را حتماً می‌بیند (در REPEATABLE READ عکس فوری کهنه می‌ماند).
# • ``sql_mode`` سخت‌گیر: دادهٔ نامعتبر خطا می‌دهد، نه «اصلاح بی‌صدا».
# • ``time_zone = +00:00``: زمان‌ها همیشه UTC ذخیره و مقایسه می‌شوند.
# • ``max_statement_time``: هیچ کوئری سرگردانی اتصال را اشغال نمی‌کند (ثانیه).
DATABASE_STATEMENT_TIMEOUT_SECONDS = env.float("DATABASE_STATEMENT_TIMEOUT_SECONDS", default=5.0)
_SQL_MODE = (
    "STRICT_ALL_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ZERO_DATE,NO_ZERO_IN_DATE,"
    "NO_ENGINE_SUBSTITUTION,NO_AUTO_VALUE_ON_ZERO"
)
_DATABASE_OPTIONS: dict[str, Any] = {
    "charset": "utf8mb4",
    "isolation_level": "read committed",
    "connect_timeout": 5,
    "init_command": (
        f"SET SESSION sql_mode='{_SQL_MODE}', "
        "SESSION time_zone='+00:00', "
        "SESSION collation_connection='utf8mb4_uca1400_as_cs', "
        f"SESSION max_statement_time={DATABASE_STATEMENT_TIMEOUT_SECONDS:g}"
    ),
}
_DATABASE = env.db_url("DATABASE_URL", default="mysql://mahdieh:mahdieh@127.0.0.1:3306/mahdieh")
# درایور اختصاصی: همان درایور رسمی + collation حساس + منطقهٔ زمانی بدون جدول tz.
_DATABASE["ENGINE"] = "apps.core.db.backends.mariadb"
_DATABASE.update(
    {
        # اتصال پایدار: فرآیندهای Passenger/gunicorn عمر طولانی دارند؛ بازسازی
        # اتصال در هر درخواست هزینهٔ بی‌دلیل است. بررسی سلامت، اتصالِ مرده را
        # پیش از استفاده کنار می‌گذارد (wait_timeout سرور).
        "CONN_MAX_AGE": env.int("DATABASE_CONN_MAX_AGE", default=60),
        "CONN_HEALTH_CHECKS": env.bool("DATABASE_CONN_HEALTH_CHECKS", default=True),
        "OPTIONS": _DATABASE_OPTIONS,
        "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_uca1400_as_cs"},
    }
)
DATABASES = {
    "default": _DATABASE,
    # اتصال جداگانهٔ کش به **همان** پایگاه‌داده (ر.ک. apps.core.db.routers):
    # نوشتن در کش در تراکنش کسب‌وکار گیر نمی‌کند و با برگشت آن برنمی‌گردد.
    "cache": {**_DATABASE, "TEST": {"MIRROR": "default"}},
}
DATABASE_ROUTERS = ["apps.core.db.routers.CacheRouter"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─── کش (جدول پایگاه‌داده با شمارندهٔ اتمیک — ADR-0021) ─────────────────────
# میزبان Redis ندارد و برای بار این سامانه (۵ واحد) لازم هم نیست: کش جدولی با
# ``incr`` اتمیک همهٔ نیازها را پوشش می‌دهد و یک سرویس متحرک کمتر یعنی یک نقطهٔ
# شکست کمتر.
CACHE_KEY_PREFIX = env.str("CACHE_KEY_PREFIX", default="mahdieh")
CACHE_DEFAULT_TTL_SECONDS = env.int("CACHE_DEFAULT_TTL_SECONDS", default=300)
CACHE_TABLE = "django_cache"
CACHES = {
    "default": {
        "BACKEND": "apps.core.db.cache.AtomicDatabaseCache",
        "LOCATION": CACHE_TABLE,
        "KEY_PREFIX": CACHE_KEY_PREFIX,
        "TIMEOUT": CACHE_DEFAULT_TTL_SECONDS,
        "OPTIONS": {"MAX_ENTRIES": 20_000, "CULL_FREQUENCY": 4},
    }
}
# نشست مستقیم روی پایگاه‌داده: کش هم جدولی است، پس cached_db دو بار می‌نوشت.
SESSION_ENGINE = "django.contrib.sessions.backends.db"

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

# ─── آی‌پی واقعی کاربر: تعداد پروکسی‌های مورد اعتماد ─────────────────────────
# ``X-Forwarded-For`` را **کاربر** می‌تواند بفرستد. اگر بی‌شرط به آن اعتماد شود،
# سقف نرخ کد ورود (otp_request/otp_verify) با عوض‌کردن یک هدر دور زده می‌شود.
# پس پیش‌فرض صفر است (فقط REMOTE_ADDR؛ مثل Apache/Passenger روی cPanel) و پشت
# Nginx ما مقدار ۱ می‌گیرد. همین عدد به DRF هم داده می‌شود تا شناسهٔ سقف نرخ و
# آی‌پیِ حسابرسی از یک منبع بیایند.
TRUSTED_PROXY_COUNT = env.int("MAHDIEH_TRUSTED_PROXY_COUNT", default=0)

# ─── API ──────────────────────────────────────────────────────────────────────
REST_FRAMEWORK: dict[str, Any] = {
    "NUM_PROXIES": TRUSTED_PROXY_COUNT,
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

# ─── فرانت جدا (تیم فرانت شرکت) — دسترسی بی‌دامنه‌ای ───────────────────────────
# قاعدهٔ امنیتی: پیش‌فرض **بسته** است. تا مبدأ فرانت در محیط نوشته نشود، هیچ
# دامنهٔ دیگری به API دسترسی ندارد. ``MAHDIEH_CORS_ORIGINS`` تنها منبع حقیقت است و
# هم CORS را (خواندن پاسخ) و هم CSRF trusted origins را (پذیرش نوشتن) تغذیه می‌کند —
# دو متغیر جدا یعنی یک روز یکی از قلم می‌افتد و «دکمه‌ای که کار نمی‌کند» می‌سازیم.
FRONTEND_ORIGINS = parse_origins(env.list("MAHDIEH_CORS_ORIGINS", default=[]))
CORS_ALLOWED_ORIGINS = FRONTEND_ORIGINS
# کوکی نشست را با درخواست بی‌دامنه‌ای می‌فرستیم (زائر وارد می‌شود، بعد رزرو می‌کند).
CORS_ALLOW_CREDENTIALS = bool(FRONTEND_ORIGINS)
CORS_ALLOW_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "idempotency-key",
    "x-csrftoken",
    "x-requested-with",
]
CORS_EXPOSE_HEADERS = ["Content-Language", "X-Request-Id"]
CORS_PREFLIGHT_MAX_AGE = 600
CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, *FRONTEND_ORIGINS]

# کوکی بی‌دامنه‌ای: مرورگرها برای درخواست بی‌دامنه‌ای، ``SameSite=None; Secure``
# می‌خواهند. این پرچم فقط در محیط واقعی (HTTPS) روشن می‌شود؛ در غیر این صورت
# کوکی‌ها بی‌صدا دور ریخته می‌شوند و «ورود کار نمی‌کند» — یک باگ دیرپیدا.
CROSS_SITE_COOKIES = env.bool("MAHDIEH_CROSS_SITE_COOKIES", default=False)
if CROSS_SITE_COOKIES:
    SESSION_COOKIE_SAMESITE = "None"
    CSRF_COOKIE_SAMESITE = "None"
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
X_FRAME_OPTIONS = "DENY"
# اعتبارسنجی تنظیمات بی‌دامنه‌ای در ``apps/core/checks.py`` انجام می‌شود تا
# اشتباه پیکربندی، در ``manage.py check`` و CI قرمز شود، نه در دست فرانت.

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
# گزارش نقض CSP به مسیر خودمان می‌رود (``/csp-report/``). چرا پیش‌فرض روشن است؟
# چون سیاستی که گزارش نمی‌دهد، در عمل یا خاموش می‌شود یا بی‌دلیل تنگ می‌ماند.
#
# نکتهٔ آموخته: خطِ ``CSP_REPORT_URI=`` (تهی) در فایل محیط، «خاموش» شمرده
# نمی‌شود؛ چون فایل‌های محیطِ کهنه با خط‌های تهی زیادند و بی‌صدا گزارش‌گیری را
# می‌کilled‌اند. برای خاموش‌کردن، صریح بنویسید ``CSP_REPORT_URI=none``.
_CSP_REPORT_URI = env.str("CSP_REPORT_URI", default="").strip().lower()
if _CSP_REPORT_URI != "none":
    _CSP_POLICY["report-uri"] = [_CSP_REPORT_URI or "/csp-report/"]

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
# ─── تأیید دومرحله‌ای کارکنان (ADR-0018) ─────────────────────────────────────
# پیش‌فرض «روشن» است و در production.py قفل می‌شود؛ فقط محیط توسعه/آزمون
# می‌تواند آن را برای سهولت کار خاموش کند.
STAFF_MFA_REQUIRED = env.bool("MAHDIEH_STAFF_MFA_REQUIRED", default=True)
STAFF_MFA_STEP_UP_SECONDS = env.int("MAHDIEH_STAFF_MFA_STEP_UP_SECONDS", default=600)

# ─── زائران خارجی (ADR-0019) ─────────────────────────────────────────────────
# پذیرش شمارهٔ همراه غیرایرانی برای ورود. پیش‌فرض خاموش: پنل پیامک داخلی معمولاً
# به شمارهٔ خارجی تحویل نمی‌دهد؛ تنها پس از تأیید پشتیبانی پنل روشن شود.
INTERNATIONAL_PHONES_ENABLED = env.bool("MAHDIEH_INTERNATIONAL_PHONES", default=False)

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
# کد الگوهای «ارسال الگومحور» (ایران‌پیامک) — JSON و اختیاری؛ پیش‌فرض‌های
# سامانه در ``apps/notifications/patterns.py`` است و این‌جا بازنویسی می‌شود.
SMS_PATTERN_CODES = env.str("SMS_PATTERN_CODES", default="")
# ارقام داخل متغیرهای الگو: "persian" (پیش‌فرض) یا "latin".
SMS_ATTRIBUTE_DIGITS = env.str("SMS_ATTRIBUTE_DIGITS", default="persian")

# ─── رسانه ────────────────────────────────────────────────────────────────────
STORAGE_BACKEND = env.str("STORAGE_BACKEND", default="local")
S3_ENDPOINT_URL = env.str("S3_ENDPOINT_URL", default="")
S3_ACCESS_KEY = env.str("S3_ACCESS_KEY", default="")
S3_SECRET_KEY = env.str("S3_SECRET_KEY", default="")
S3_BUCKET = env.str("S3_BUCKET", default="")
S3_REGION = env.str("S3_REGION", default="us-east-1")
SIGNED_URL_TTL_SECONDS = env.int("SIGNED_URL_TTL_SECONDS", default=900)

STATIC_URL = "/static/"
# انبارهای فایل — تعریف صریح ``STORAGES`` در پایه، تا محیط‌های دیگر بتوانند
# یک کلید را جایگزین کنند. درس ۶۰۲: از Django 4.2 به بعد کلید ``staticfiles``
# تنها راه تعیین انبار دارایی ایستاست و تنظیم قدیمی ``STATICFILES_STORAGE``
# بی‌صدا بی‌اثر است (و در Django 6 حذف شده).
STORAGES: dict[str, dict[str, Any]] = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
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


def _running_release() -> str:
    """شناسهٔ نسخهٔ در حال اجرا: متغیر محیطی ⇐ BUILD_INFO.json بستهٔ انتشار ⇐ «dev».

    استقرار اتمی باید ثابت کند که فرآیندِ پاسخ‌دهنده **همان** نسخهٔ تازه است، نه
    فرآیندِ قدیمی‌ای که هنوز بازنشانی نشده؛ پس این شناسه در پاسخ زنده‌بودن می‌آید.
    """
    explicit = str(env.str("MAHDIEH_RELEASE", default="")).strip()
    if explicit:
        return explicit
    try:
        info = json.loads((BASE_DIR.parent / "BUILD_INFO.json").read_text(encoding="utf-8"))
        return str(info["release"])
    except (OSError, ValueError, KeyError, TypeError):
        return "dev"


RELEASE = _running_release()
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
