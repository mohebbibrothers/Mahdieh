"""سیستم‌چک‌های دامنهٔ اعلان — پیکربندی غلط باید پیش از انتشار دیده شود.

دو خطای واقعی که این چک‌ها می‌گیرند:
    ۱. پیامک روشن است، ارائه‌دهنده «الگومحور» است، ولی الگوی قالب‌های حیاتی
       (کد ورود، تأیید رزرو) در پنل ثبت نشده ⇒ سایت در عمل بی‌پیامک است و
       هیچ خطایی هم دیده نمی‌شود.
    ۲. پیامک روشن است ولی کلید API یا شمارهٔ خط تنظیم نشده ⇒ هر ارسال «ناموفق»
       ثبت می‌شود و تا شکایت زائر کسی متوجه نمی‌شود.

هر دو، «شکست خاموش»‌اند؛ و شکست خاموش، بدترین نوع شکست است.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning, register

from apps.notifications import patterns
from apps.notifications.providers import LOCAL_PROVIDERS

PROVIDER_MOCK: str = "mock"
PATTERN_CRITICAL_ID: str = "mahdieh.W010"
PATTERN_GUEST_ID: str = "mahdieh.W011"
KEY_MISSING_ID: str = "mahdieh.E012"
SENDER_MISSING_ID: str = "mahdieh.E013"
HINT_PATTERNS: str = (
    "python manage.py sms_patterns --request را اجرا کنید، کد الگوها را از پنل "
    "بگیرید و در SMS_PATTERN_CODES بگذارید (docs/runbooks/sms-provider.md)."
)


def _sms_enabled() -> bool:
    """آیا ارسال واقعی پیامک در این محیط روشن است؟"""
    return bool(getattr(settings, "SMS_ENABLED", False))


def _provider_name() -> str:
    """نام آداپتور پیامک فعال."""
    return str(getattr(settings, "SMS_PROVIDER", PROVIDER_MOCK) or PROVIDER_MOCK)


def _is_network_provider() -> bool:
    """آیا آداپتور فعال، تماس شبکه‌ای دارد (یعنی اعتبارنامه لازم دارد)؟"""
    return _provider_name() not in LOCAL_PROVIDERS


def _pattern_checks() -> list[CheckMessage]:
    """آمادگی الگوهای پیامکی برای قالب‌های زائر."""
    readiness = patterns.pattern_readiness()
    messages: list[CheckMessage] = []
    critical = list(readiness["critical_unbound"])
    if critical:
        messages.append(
            Error(
                "با SMS_ENABLED=True این قالب‌های حیاتی الگوی پیامکی ندارند: " + "، ".join(critical),
                hint=HINT_PATTERNS,
                id=PATTERN_CRITICAL_ID,
            )
        )
    remaining = [key for key in readiness["guest_unbound"] if key not in critical]
    if remaining:
        messages.append(
            Warning(
                "این قالب‌های زائر بدون الگوی پیامکی‌اند و ارسال‌شان مسدود می‌شود: "
                + "، ".join(remaining),
                hint=HINT_PATTERNS,
                id=PATTERN_GUEST_ID,
            )
        )
    return messages


def _credential_checks() -> list[CheckMessage]:
    """اعتبارنامه‌های پنل پیامک — نبودشان در تولید، خطاست نه هشدار."""
    messages: list[CheckMessage] = []
    key_present = bool(str(getattr(settings, "SMS_API_KEY", "") or "").strip())
    sender_present = bool(str(getattr(settings, "SMS_SENDER_NUMBER", "") or "").strip())
    # آداپتورهای محلی (mock/console) هیچ اعتبارنامه‌ای نمی‌خواهند.
    severity = Error if _is_network_provider() else Warning
    if not key_present:
        messages.append(
            severity(
                "پیامک روشن است ولی کلید API ارائه‌دهنده تنظیم نشده است.",
                hint="SMS_API_KEY را در محیط تولید پر کنید (docs/runbooks/secret-rotation.md).",
                id=KEY_MISSING_ID,
            )
        )
    if not sender_present:
        messages.append(
            severity(
                "پیامک روشن است ولی شمارهٔ خط فرستنده تنظیم نشده است.",
                hint="SMS_SENDER_NUMBER را پر کنید (برای ایران‌پیامک: خط اختصاصی پنل).",
                id=SENDER_MISSING_ID,
            )
        )
    return messages


@register("mahdieh_soft")
def sms_readiness(app_configs: Any = None, **kwargs: Any) -> list[CheckMessage]:
    """چک یکپارچهٔ آمادگی پیامک — فقط وقتی ارسال واقعی روشن است."""
    del app_configs, kwargs  # امضای ثابت سیستم‌چک‌های جنگو
    if not _sms_enabled():
        return []
    return [*_pattern_checks(), *_credential_checks()]
