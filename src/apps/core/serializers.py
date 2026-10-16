"""سریالایزرها و فیلدهای **مشترک** هستهٔ API.

چرا این ماژول وجود دارد؟ مفهوم «بازهٔ زمانی» عرضی است: هر جا بازه‌ای به API
می‌رسد باید یک شکل و یک نام داشته باشد. پیش از این، ``Block.period`` با
``ModelField`` عمومی سریال می‌شد و خروجی‌اش **رشتهٔ JSON فرارکرده** بود
(``'{"bounds": "[)", "lower": "2026-11-01T08:00:00+00:00", …}'``) — یعنی فرانت
باید دو بار ``JSON.parse`` می‌کرد و تایپ‌های تولیدشدهٔ کلاینت بی‌فایده بودند.
اکنون قرارداد یکی است: ``{"start": …, "end": …}`` با تاریخ‌های ISO 8601 (UTC).
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

PERIOD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "start": {"type": "string", "format": "date-time", "nullable": True},
        "end": {"type": "string", "format": "date-time", "nullable": True},
    },
    "required": ["start", "end"],
}


@extend_schema_field(PERIOD_SCHEMA)
# در نسخهٔ جاری DRF، ``Field`` چهار پارامتر نوع دارد (نمایش/ورودی/خروجی/…)؛
# ما همه را ``Any`` می‌گیریم چون این فیلد جز یک شیء ISO چیزی نمی‌فرستد و نمی‌گیرد.
class PeriodRangeField(serializers.Field[Any, Any, Any, Any]):
    """بازهٔ زمانی دیتابیس (``DateTimeRangeField``) به‌شکل شیء صریح.

    مرزهای بازه — ``lower`` و ``upper`` — به ISO 8601 تبدیل می‌شوند. بازهٔ خالی
    (``empty``) با ``null`` نمایش داده می‌شود تا معنایش «هیچ‌وقت» باشد، نه «۰».
    """

    def to_representation(self, value: Any) -> dict[str, str | None]:
        """نمایش بازه به‌صورت ``{"start", "end"}``."""
        lower = getattr(value, "lower", None)
        upper = getattr(value, "upper", None)
        return {
            "start": lower.isoformat() if lower else None,
            "end": upper.isoformat() if upper else None,
        }

    def to_internal_value(self, data: Any) -> Any:  # noqa: ARG002 - قرارداد DRF
        """این فیلد فقط خواندنی است؛ ورودی نمی‌پذیرد."""
        raise serializers.ValidationError("این فیلد فقط خواندنی است.")


class PageMetaSerializer(serializers.Serializer[Any]):
    """فرادادهٔ صفحه‌بندی مکان‌نما — بخش ``meta`` هر پاسخ فهرست.

    ``total`` فقط در فهرست‌های کاربرمحور می‌آید؛ نبودنش یعنی «شمارش کل را
    نخواستیم چون گران است»، نه «صفر».
    """

    next = serializers.CharField(allow_null=True, help_text="نشانی صفحهٔ بعد (یا null)")
    previous = serializers.CharField(allow_null=True, help_text="نشانی صفحهٔ قبل (یا null)")
    page_size = serializers.IntegerField(help_text="اندازهٔ صفحهٔ همین پاسخ")
    total = serializers.IntegerField(required=False, help_text="شمارش کل (فقط فهرست‌های کاربرمحور)")


class MetaSerializer(serializers.Serializer[Any]):
    """فرادادهٔ سرویس — برای پایش و بررسی نسخه توسط تیم پشتیبانی و فرانت."""

    service = serializers.CharField(help_text="نام سرویس (برای هم‌خوانی با ردیابی)")
    environment = serializers.CharField(help_text="محیط اجرا: development/staging/production")
    schema_version = serializers.CharField(help_text="نسخهٔ قرارداد API (نسخه‌بندی مسیر)")
    today_jalali = serializers.CharField(help_text="امروزِ تقویم جلالی — همیشه با ارقام لاتین")
    timezone = serializers.CharField(help_text="منطقهٔ زمانی سرویس (ذخیرهٔ UTC، نمایش محلی)")
