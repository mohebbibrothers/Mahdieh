"""تنظیمات اجرای تست — قطعی، سریع‌تر، و بدون وابستگی به سرویس بیرونی.

قواعد مطلق:
    • SQLite ممنوع است. رفتار قیدهای بازه‌ای، قفل‌ها و همزمانی فقط روی
      PostgreSQL واقعی معنا دارد؛ تست روی موتور دیگر، «تست کردن چیزی که
      در تولید اجرا نمی‌شود» است.
    • هیچ تلاش شبکه‌ای انجام نمی‌شود؛ ارائه‌دهنده‌ها ماک هستند.
"""

from __future__ import annotations

from .base import *
from .base import env

DEBUG = False
SECRET_KEY = "test-only-secret-key-not-for-production-use"  # nosec B105 - مقدار تست، نه راز

# هش سریع: تست‌ها نباید روی PBKDF2/Argon2 وقت تلف کنند
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# ایمیل: در حافظه، بدون ارسال واقعی
MAILERS = {
    "default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
}

# پیامک: همیشه ماک در تست
SMS_ENABLED = False
SMS_PROVIDER = "mock"

# رمزنگاری: کلید قطعی تست (لیترال است و راز تولید نیست)
FIELD_ENCRYPTION_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
FIELD_HASH_KEY = "test-only-hmac-key-for-deterministic-lookup"

# محدودسازی نرخ: در تست خاموش تا تست‌ها به هم نخورند
REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = []
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {}

# کش: لوکال‌مموری در تست‌های واحد (تست‌های یکپارچگی صریحاً روی Valkey اجرا می‌شوند)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "mahdieh-test",
    }
}

# لاگ: خاموش تا خروجی تست خوانا بماند
LOG_LEVEL = env.str("LOG_LEVEL", default="CRITICAL")
LOG_FORMAT = "console"

# افزودنی‌های آزمون
TEST_RUNNER = "django.test.runner.DiscoverRunner"
