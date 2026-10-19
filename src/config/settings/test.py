"""تنظیمات اجرای تست — قطعی، سریع‌تر، و بدون وابستگی به سرویس بیرونی.

قواعد مطلق:
    • SQLite ممنوع است. تریگرهای عدم‌هم‌پوشانی، قفل‌ها، collation و همزمانی
      فقط روی MariaDB واقعی (همان نسخهٔ میزبان، 11.4) معنا دارد؛ تست روی موتور
      دیگر، «تست کردن چیزی که در تولید اجرا نمی‌شود» است.
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

# عامل دوم کارکنان: در آزمون‌های عمومی خاموش (بیشترشان با force_authenticate اند)؛
# آزمون‌های اختصاصی MFA آن را با ``settings`` روشن می‌کنند.
STAFF_MFA_REQUIRED = False

# محدودسازی نرخ: در تست خاموش تا تست‌ها به هم نخورند
REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = []
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {}

# پایگاه‌داده: اتصال پایدار در آزمون بی‌فایده است و نخ‌های آزمون همزمانی را
# به اتصال‌های نیمه‌باز وابسته می‌کند؛ هر آزمون اتصال تازه می‌گیرد.
for _alias in DATABASES.values():
    _alias["CONN_MAX_AGE"] = 0

# کش: لوکال‌مموری در آزمون‌های عمومی (جدا از پایگاه‌داده تا ``cache.clear()``
# خودکار در conftest به دسترسی پایگاه‌داده نیاز نداشته باشد). کش جدولی تولید
# (``AtomicDatabaseCache``) در ``tests/test_db_cache.py`` جداگانه و روی MariaDB
# واقعی آزموده می‌شود.
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
