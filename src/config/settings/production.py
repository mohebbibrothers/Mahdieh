"""تنظیمات تولید — سخت‌گیرانه، و «امن به‌صورت پیش‌فرض».

هر مقدار امنیتی که در تولید لازم است، اینجا اجبار می‌شود؛ نه اینکه امیدوار
باشیم کسی یادش نرود. اگر متغیر الزامی نباشد، فرآیند بالا نمی‌آید (fail-fast).
"""

from __future__ import annotations

from .base import *
from .base import env

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

# ── بومی‌سازی رسانه در تولید ─────────────────────────────────────────────────
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"

# ── لاگ ساخت‌یافته ───────────────────────────────────────────────────────────
LOG_LEVEL = env.str("LOG_LEVEL", default="INFO")
LOG_FORMAT = "json"

# ── کارایی ───────────────────────────────────────────────────────────────────
CONN_MAX_AGE = env.int("DATABASE_CONN_MAX_AGE", default=600)

# ── پایش خطا ─────────────────────────────────────────────────────────────────
SENTRY_DSN = env.str("SENTRY_DSN", default="")
