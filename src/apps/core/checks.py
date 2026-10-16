"""سیستم‌چک‌های پیکربندی — «اشتباه تنظیمات، پیش از استقرار قرمز شود».

چرا این ماژول وجود دارد؟ بخشی از تنظیمات، خطاهایی می‌سازند که **در محیط توسعه
دیده نمی‌شوند** و فقط در مرورگر کاربر ظاهر می‌شوند: کوکی بی‌دامنه‌ای که مرورگر
بی‌صدا دور می‌ریزد («ورود کار نمی‌کند!»)، مبدأ نادرست CORS («خطای شبکه»)، یا
``*`` همراه با کوکی که مرورگر رد می‌کند. این‌ها را در ``manage.py check`` و CI
گرفتن، تفاوت تیم حرفه‌ای است با تیمی که در روز تحویل دنبال باگ می‌گردد.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import Error, Warning, register

from apps.core.xorigin import is_valid_origin

CORS_ERROR = "mahdieh.E001"
CORS_WARNING = "mahdieh.E002"
COOKIE_ERROR = "mahdieh.E003"
CONTACT_WARNING = "mahdieh.W001"


@register()
def cors_configuration_check(*_args: Any, **_kwargs: Any) -> list[Any]:
    """بررسی سلامت پیکربندی فرانت جدا (CORS + کوکی + CSRF)."""
    issues: list[Any] = []
    origins: list[str] = list(getattr(settings, "CORS_ALLOWED_ORIGINS", []) or [])
    credentials: bool = bool(getattr(settings, "CORS_ALLOW_CREDENTIALS", False))
    trusted: list[str] = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or [])

    if "*" in origins and credentials:
        issues.append(
            Error(
                "مبدأ «*» همراه با ارسال کوکی (CORS_ALLOW_CREDENTIALS) مجاز نیست؛ "
                "مرورگر چنین پاسخی را رد می‌کند.",
                hint="مبدأهای دقیق فرانت را در MAHDIEH_CORS_ORIGINS بنویسید.",
                id=CORS_ERROR,
            )
        )

    malformed = [origin for origin in origins if not is_valid_origin(origin)]
    if malformed:
        issues.append(
            Error(
                f"قالب مبدأ نادرست: {malformed}",
                hint="قالب درست: https://app.example.com — بدون اسلش پایانی و بدون مسیر.",
                id=CORS_ERROR,
            )
        )

    missing_trust = [origin for origin in origins if origin not in trusted]
    if missing_trust:
        issues.append(
            Error(
                f"مبدأ فرانت در CSRF_TRUSTED_ORIGINS نیست: {missing_trust}",
                hint=(
                    "این مقدار را دستی ست نکنید؛ MAHDIEH_CORS_ORIGINS هر دو را تغذیه می‌کند "
                    "(یک منبع حقیقت)."
                ),
                id=CORS_ERROR,
            )
        )

    if getattr(settings, "CROSS_SITE_COOKIES", False):
        secure = bool(
            getattr(settings, "SESSION_COOKIE_SECURE", False)
            and getattr(settings, "CSRF_COOKIE_SECURE", False)
        )
        if not secure:
            issues.append(
                Error(
                    "کوکی بی‌دامنه‌ای (MAHDIEH_CROSS_SITE_COOKIES=true) بدون Secure روشن "
                    "شده است؛ مرورگر کوکی SameSite=None را بدون Secure دور می‌ریزد و "
                    "ورود بی‌صدا شکست می‌خورد.",
                    hint="در محیط واقعی SESSION_COOKIE_SECURE=true و CSRF_COOKIE_SECURE=true دهند.",
                    id=COOKIE_ERROR,
                )
            )
        if getattr(settings, "SESSION_COOKIE_SAMESITE", "") != "None":
            issues.append(
                Error(
                    "پرچم کوکی بی‌دامنه‌ای روشن است ولی SESSION_COOKIE_SAMESITE روی None نیست.",
                    id=COOKIE_ERROR,
                )
            )

    if origins and not credentials:
        issues.append(
            Warning(
                "CORS باز است ولی ارسال کوکی خاموش؛ فرانت بی‌نشست کار می‌کند و زائر "
                "نمی‌تواند رزرو کند.",
                id=CORS_WARNING,
            )
        )
    return issues


@register()
def support_contact_check(*_args: Any, **_kwargs: Any) -> list[Any]:
    """هشدار دربارهٔ نشانی/تلفن پذیرش در محیط واقعی.

    چرا این چک وجود دارد؟ این دو مقدار **دادهٔ کسب‌وکار نیستند** (در پایگاه‌داده
    نمی‌نشینند و فرانت می‌تواند نسخهٔ خودش را داشته باشد)، ولی دو مصرف واقعی
    دارند: صفحهٔ راهنما/تماس در همین مخزن، و ``GET /api/v1/meta`` که آن‌ها را به
    فرانت می‌دهد. اگر در محیط واقعی خالی باشند، **هیچ شمارهٔ ساختگی چاپ نمی‌شود**
    (رفتار عمدی)، اما کارفرما باید بداند که بخش تماس سامانه خاموش است — وگرنه
    زائر «شمارهٔ پذیرش» را جایی نمی‌یابد و تلفن کارکنان اشغال می‌شود.
    """
    if getattr(settings, "ENVIRONMENT", "") != "production":
        return []
    phone = str(getattr(settings, "SUPPORT_PHONE", "") or "").strip()
    address = str(getattr(settings, "SUPPORT_ADDRESS", "") or "").strip()
    missing = [
        name
        for name, value in (("MAHDIEH_SUPPORT_PHONE", phone), ("MAHDIEH_SUPPORT_ADDRESS", address))
        if not value
    ]
    if not missing:
        return []
    return [
        Warning(
            "نشانی/تلفن پذیرش در تولید تنظیم نشده: " + "، ".join(missing),
            hint=(
                "این مقدارها دادهٔ حرفه‌ای اختیاری‌اند (فرانت می‌تواند نسخهٔ خودش را "
                "داشته باشد)؛ اگر می‌خواهید سامانه هم نشانشان دهد، در فایل محیط تولید پر کنید."
            ),
            id=CONTACT_WARNING,
        )
    ]
