"""زمینهٔ مشترک قالب‌ها — مقادیر سراسری و ایمن."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone

from apps.core.jalali import format_jalali, gregorian_to_jalali


def site(request: HttpRequest) -> dict[str, Any]:
    """افزودن فرادادهٔ سایت و امروزِ جلالی به همهٔ قالب‌ها."""
    today = timezone.localdate()
    jy, jm, _ = gregorian_to_jalali(today)
    return {
        "SITE_NAME": "زائرسرای مهدیه",
        "SITE_TAGLINE": "اقامت رایگان برای زائران امام رضا (ع)",
        "SITE_URL": settings.SITE_URL,
        "ENVIRONMENT": settings.ENVIRONMENT,
        "TODAY_JALALI": format_jalali(today, with_weekday=True),
        "TODAY_JALALI_SHORT": f"{jy:04d}/{jm:02d}",
        "request_id": getattr(request, "request_id", ""),
    }
