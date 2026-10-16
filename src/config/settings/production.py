"""تنظیمات تولید — سخت‌گیرانه، و «امن به‌صورت پیش‌فرض».

هر مقدار امنیتی که در تولید لازم است، اینجا اجبار می‌شود؛ نه اینکه امیدوار
باشیم کسی یادش نرود. اگر متغیر الزامی نباشد، فرآیند بالا نمی‌آید (fail-fast).
"""

from __future__ import annotations

from .base import *
from .base import MIDDLEWARE as _BASE_MIDDLEWARE, env

DEBUG = False

# ── مقادیر الزامی: نبودشان یعنی کانفیگ ناقص، نه «حدس بزن» ───────────────────
SECRET_KEY = env.str("MAHDIEH_SECRET_KEY")  # الزامی
ALLOWED_HOSTS = env.list("MAHDIEH_ALLOWED_HOSTS")  # الزامی
DATABASES["default"]["PASSWORD"] = env.str("DATABASE_PASSWORD")

if len(SECRET_KEY) < 50:  # noqa: PLR2004
    msg = "MAHDIEH_SECRET_KEY در تولید باید دست‌کم ۵۰ نویسه باشد."
    raise RuntimeError(msg)

# ── امنیت اجباری ─────────────────────────────────────────────────────────────
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31_536_000  # یک سال
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# CSP در تولید «اعمال‌شده» است: سیاستِ فقط-گزارشی توسعه را خاموش و
# سیاست اعمالی را روشن می‌کنیم (هر دو در base.py ساخته شده‌اند).
SECURE_CSP = CSP_POLICY_ENFORCED
SECURE_CSP_REPORT_ONLY = {}
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ── دارایی‌های ایستا در تولید ────────────────────────────────────────────────
# درس مهم (۶۰۲): در Django 6.x تنظیم ``STATICFILES_STORAGE`` **حذف شده** است و
# تنها راه تعیین انبارِ دارایی ایستا، کلید ``staticfiles`` در ``STORAGES`` است.
# نام قدیمی هیچ خطایی نمی‌دهد و بی‌صدا بی‌اثر می‌ماند ⇒ نسخه‌بندی دارایی‌ها
# (cache-busting) در تولید از کار می‌افتاد. اکنون صریح، فشرده و آزمون‌شده:
STORAGES["staticfiles"] = {
    "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
}

# ── وایت‌نویز: سد دومِ سرو دارایی ایستا ───────────────────────────────────────
# اگر Nginx جلوی سامانه نباشد یا مسیر /static/ را نگیرد، خودِ اپ دارایی فشردهٔ
# نسخه‌دار را با هدرهای درست سرو می‌کند. Nginx سرِ جایش می‌ماند (سریع‌تر)، اما
# استقرار «به آن وابسته» نمی‌شود؛ یک نقطهٔ شکست کمتر.
_security_index = _BASE_MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1
MIDDLEWARE = [
    *_BASE_MIDDLEWARE[:_security_index],  # RequestContext ⇒ DatabaseResilience ⇒ Security
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *_BASE_MIDDLEWARE[_security_index:],
]
WHITENOISE_MAX_AGE = 31_536_000  # دارایی‌ها نسخه‌دارند ⇒ کش یک‌ساله بی‌خطر است
WHITENOISE_KEEP_ONLY_HASHED_FILES = True

# ── لاگ ساخت‌یافته ───────────────────────────────────────────────────────────
LOG_LEVEL = env.str("LOG_LEVEL", default="INFO")
LOG_FORMAT = "json"

# ── کارایی پایگاه‌داده ───────────────────────────────────────────────────────
# در تولید **استخر اتصال** فعال است (پایه)، پس عمر اتصالِ پایدار را صفر می‌کنیم؛
# برای خوشهٔ بدون استخر می‌توان با DATABASE_POOL_ENABLED=false به رفتار قدیمی
# برگشت. فرمول ظرفیت و تنظیم max_connections سرور در docs/runbooks/deploy.md.
DATABASE_POOL_ENABLED = env.bool("DATABASE_POOL_ENABLED", default=True)
CONN_MAX_AGE = 0 if DATABASE_POOL_ENABLED else env.int("DATABASE_CONN_MAX_AGE", default=600)

# ── ایمیل عملیاتی ───────────────────────────────────────────────────────────
# کانال اصلی اطلاع‌رسانی به زائر **پیامک** است؛ ایمیل تنها برای هشدار فنی
# (mail_admins روی خطای ۵۰۰) و مکاتبات اداری استفاده می‌شود. با این حال، در
# تولید نباید کنسولِ بی‌مصرف باشد: ``manage.py check --deploy`` این را خطا
# می‌شمارد و به‌درستی هم می‌شمارد — ایمیلِ کنسولی یعنی هشدار گم‌شده.
MAILERS["default"] = {  # type: ignore[name-defined]
    "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "OPTIONS": {
        "HOST": env.str("EMAIL_HOST", default="localhost"),
        "PORT": env.int("EMAIL_PORT", default=25),
        "USER": env.str("EMAIL_HOST_USER", default=""),
        "PASSWORD": env.str("EMAIL_HOST_PASSWORD", default=""),
        "USE_TLS": env.bool("EMAIL_USE_TLS", default=False),
        "TIMEOUT": env.int("EMAIL_TIMEOUT", default=10),
    },
}
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="no-reply@mahdieh.ir")
SERVER_EMAIL = env.str("SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)

# ── پایش خطا ─────────────────────────────────────────────────────────────────
SENTRY_DSN = env.str("SENTRY_DSN", default="")
