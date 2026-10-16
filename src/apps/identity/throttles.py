"""محدودسازی نرخ اختصاصی دامنهٔ هویت.

طراحی: کلاس پایه طوری رفتار می‌کند که اگر دامنهٔ نرخ در تنظیمات تعریف نشده
باشد، محدودسازی **خاموش** (نه خطا) شود. مزیت: محیط تست بدون دستکاری view‌ها
قطعی می‌ماند و در تولید کافی است نرخ در تنظیمات بیاید.
"""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle


class ScopedThrottle(SimpleRateThrottle):
    """محدودکنندهٔ نرخ مبتنی بر دامنه، با رفتار «نبود نرخ ⇒ خاموش»."""

    scope = ""

    def get_rate(self) -> str | None:
        """نرخ دامنه؛ در نبود تعریف، ``None`` یعنی محدودسازی غیرفعال."""
        return self.THROTTLE_RATES.get(self.scope)  # type: ignore[return-value]

    def get_cache_key(self, request: Request, view: Any) -> str:  # type: ignore[override]
        """کلید محدودسازی — بر پایهٔ آی‌پی برای مهمان و کاربر برای واردشده."""
        del view
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            ident = str(user.pk)
        else:
            ident = self.get_ident(request)  # type: ignore[attr-defined]
        return self.cache_format % {"scope": self.scope, "ident": ident}


class OtpRequestThrottle(ScopedThrottle):
    """سقف درخواست کد ورود به‌ازای هر آی‌پی/کاربر."""

    scope = "otp_request"


class OtpVerifyThrottle(ScopedThrottle):
    """سقف تلاش تأیید کد — لایهٔ دوم دفاعی پس از سقف منطق دامنه."""

    scope = "otp_verify"


class SensitiveActionThrottle(ScopedThrottle):
    """عملیات حساس کارکنان (جابه‌جایی، لغو اجباری، خروجی) — ضد سوءاستفادهٔ داخلی."""

    scope = "sensitive_action"
