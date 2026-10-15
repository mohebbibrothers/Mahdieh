"""تنظیمات محیط توسعه — راحت برای کار روزمره، امن برای ماشین محلی."""

from __future__ import annotations

from .base import *
from .base import env

DEBUG = True
# پذیرش میزبان دلخواه فقط در محیط توسعه مجاز است؛ در تولید، ALLOWED_HOSTS
# از محیط و اجباری خوانده می‌شود (settings/production.py).
_DEV_HOSTS = ("0.0.0.0", "127.0.0.1", "localhost", "*")  # noqa: S104  # nosec B104
ALLOWED_HOSTS = [*ALLOWED_HOSTS, *_DEV_HOSTS]
SECRET_KEY = env.str("MAHDIEH_SECRET_KEY", default="dev-only-insecure-key-change-me")

# احراز هویت API در توسعه: مرورگر پذیرفته می‌شود تا کاوش API راحت باشد
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]

# ایمیل در توسعه: خروجی روی کنسول (Mailpit در Docker به‌صورت اختیاری).
# نکتهٔ Django 6.1: هرگز EMAIL_* قدیمی را همراه MAILERS تعریف نکنید
# (ImproperlyConfigured). قالب مدرن، دیکشنری MAILERS است.
MAILERS = {
    "default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"},
}

# لاگ پرسروصدا برای عیب‌یابی
LOG_LEVEL = env.str("LOG_LEVEL", default="DEBUG")

INTERNAL_IPS = ["127.0.0.1", "localhost"]
