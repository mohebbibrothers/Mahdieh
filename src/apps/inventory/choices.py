"""شمارشی‌های دامنهٔ موجودی."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class UnitType(models.TextChoices):
    """نوع واحد اقامتی — دقیقاً دو نوع واقعی زائرسرا."""

    SUITE = "suite", _("سوئیت")
    LARGE_ROOM = "large_room", _("اتاق بزرگ")

    @property
    def default_capacity(self) -> int:
        """ظرفیت پیش‌فرض هر نوع (قابل بازنویسی روی هر واحد)."""
        return 6 if self is UnitType.SUITE else 8


class OccasionKind(models.TextChoices):
    """نوع مناسبت — تعیین‌کنندهٔ لحن نمایش در تقویم."""

    HOLIDAY = "holiday", _("تعطیل رسمی")
    RELIGIOUS = "religious", _("مناسبت مذهبی")
    SEASONAL = "seasonal", _("اوج فصلی")
    CUSTOM = "custom", _("سفارشی")


class DemandLevel(models.TextChoices):
    """سطح تقاضا — مبنای قواعد تخصیص ایام پیک (فاز ۶)."""

    NORMAL = "normal", _("عادی")
    HIGH = "high", _("پرتقاضا")
    PEAK = "peak", _("اوج")


class BlockKind(models.TextChoices):
    """دلیل بلوک نگهداری — هر بلوک، پاسخ به «چرا این سانس بسته است؟»."""

    MAINTENANCE = "maintenance", _("تعمیر و نگهداری")
    CLEANING = "cleaning", _("نظافت اساسی")
    EVENT = "event", _("رویداد / مأموریت")
    ADMIN = "admin", _("تصمیم مدیریتی")
