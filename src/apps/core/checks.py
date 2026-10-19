"""سیستم‌چک‌های پیکربندی — «اشتباه تنظیمات، پیش از استقرار قرمز شود».

چرا این ماژول وجود دارد؟ بخشی از تنظیمات، خطاهایی می‌سازند که **در محیط توسعه
دیده نمی‌شوند** و فقط در مرورگر کاربر ظاهر می‌شوند: کوکی بی‌دامنه‌ای که مرورگر
بی‌صدا دور می‌ریزد («ورود کار نمی‌کند!»)، مبدأ نادرست CORS («خطای شبکه»)، یا
``*`` همراه با کوکی که مرورگر رد می‌کند. این‌ها را در ``manage.py check`` و CI
گرفتن، تفاوت تیم حرفه‌ای است با تیمی که در روز تحویل دنبال باگ می‌گردد.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.checks import Error, Warning, register
from django.utils import timezone

from apps.core.xorigin import is_valid_origin

CORS_ERROR = "mahdieh.E001"
CORS_WARNING = "mahdieh.E002"
COOKIE_ERROR = "mahdieh.E003"
CONTACT_WARNING = "mahdieh.W001"
TIMEZONE_ERROR = "mahdieh.E020"
HASHER_LIBRARY_ERROR = "mahdieh.E021"

#: افق بررسی ثابت‌بودن اختلاف ساعت؛ یک سال و اندکی، تا هر تغییر فصلی دیده شود.
TIMEZONE_HORIZON_DAYS = 400

#: برچسب چک‌های «هشدارِ عمدی». این‌ها در انتشار تولید نباید **بلوکه** کنند
#: (سایت باید بالا بیاید تا شمارهٔ پذیرش و کدهای الگو برسند)، ولی باید دیده شوند.
#: نقطهٔ ورود کانتینر، این برچسب را از گذرِ سخت‌گیرانه کنار می‌گذارد و در گذر
#: دوم چاپشان می‌کند: «غیربلوکه، ولی نه پنهان».
SOFT_WARNING_TAG = "mahdieh_soft"


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


@register(SOFT_WARNING_TAG)
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


def utc_offset_transitions(zone_name: str, *, days: int = TIMEZONE_HORIZON_DAYS) -> list[datetime]:
    """لحظه‌هایی (با دقت روز) که اختلاف ساعت منطقه در افق پیش‌رو عوض می‌شود."""
    zone = ZoneInfo(zone_name)
    moment = timezone.now()
    previous = moment.astimezone(zone).utcoffset()
    transitions: list[datetime] = []
    for _ in range(days):
        moment += timedelta(days=1)
        current = moment.astimezone(zone).utcoffset()
        if current != previous:
            transitions.append(moment)
            previous = current
    return transitions


@register()
def fixed_offset_timezone_check(*_args: Any, **_kwargs: Any) -> list[Any]:
    """``TIME_ZONE`` باید اختلاف ثابت داشته باشد (پیش‌نیاز درایور MariaDB).

    میزبان جدول‌های منطقهٔ زمانی MariaDB را ندارد؛ درایور ما (``apps.core.db.
    backends.mariadb``) نام منطقه را به اختلاف عددیِ «اکنون» تبدیل می‌کند. این برای
    ایران (بی‌ساعت تابستانی از ۱۴۰۱) دقیق است، اما اگر روزی ``TIME_ZONE`` به
    منطقه‌ای با جابه‌جایی ساعت تغییر کند، فیلترهای ``__date``/گزارش‌ها نیمی از
    سال یک ساعت جابه‌جا می‌شدند — بی‌هیچ خطایی. این چک همان خطای خاموش را بلند
    می‌کند.
    """
    zone_name = str(getattr(settings, "TIME_ZONE", "UTC") or "UTC")
    try:
        transitions = utc_offset_transitions(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return [Error(f"منطقهٔ زمانی ناشناخته: {zone_name!r}", id=TIMEZONE_ERROR)]
    if not transitions:
        return []
    first = transitions[0].date().isoformat()
    return [
        Error(
            f"منطقهٔ زمانی {zone_name} در افق یک‌ساله جابه‌جایی ساعت دارد (نخستین: {first}).",
            hint=(
                "درایور MariaDB اختلاف ثابت فرض می‌کند (ADR-0020). یا منطقهٔ بی‌جابه‌جایی "
                "انتخاب کنید، یا جدول‌های tz را روی سرور بارگذاری و درایور را بازبینی کنید."
            ),
            id=TIMEZONE_ERROR,
        )
    ]


@register()
def password_hasher_library_check(*_args: Any, **_kwargs: Any) -> list[Any]:
    """هر الگوریتم ``PASSWORD_HASHERS`` باید کتابخانه‌اش را واقعاً بارگذاری کند.

    درس واقعی: Argon2 نخستین الگوریتم بود ولی ``argon2-cffi`` در وابستگی‌ها نبود.
    تنظیمات آزمون MD5 است، پس ۷۶۴ آزمون سبز ماند و فقط روی میزبان، هنگام ساخت مدیر،
    ``ValueError`` بیرون زد. بارگذاری کتابخانه ارزان است (بی‌هش‌کردن)، پس در هر
    ``check`` و در دروازهٔ انتشار (مرحلهٔ ۳ استقرار) اجرا می‌شود.
    """
    from django.contrib.auth.hashers import get_hashers

    issues: list[Any] = []
    for hasher in get_hashers():
        if not getattr(hasher, "library", None):
            continue
        try:
            # همان مسیری که هش‌کردن طی می‌کند؛ در stubها نیست ولی در جنگو ۶.۱ پایدار است.
            hasher._load_library()  # type: ignore[attr-defined]
        except ValueError as exc:
            issues.append(
                Error(
                    f"الگوریتم رمز {hasher.algorithm} بارگذاری نمی‌شود: {exc}",
                    hint="بستهٔ لازم (مثلاً argon2-cffi) را به وابستگی‌های pyproject بیفزایید.",
                    id=HASHER_LIBRARY_ERROR,
                )
            )
    return issues
