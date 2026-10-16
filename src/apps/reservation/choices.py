"""شمارشی‌های دامنهٔ رزرو — ماشین حالت و دلایل."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class ReservationStatus(models.TextChoices):
    """وضعیت رزرو — دقیقاً همان ماشین حالت بخش ۵ سند دامنه."""

    HOLD = "hold", _("نگه‌داشت موقت")
    CONFIRMED = "confirmed", _("تأییدشده")
    CHECKED_IN = "checked_in", _("پذیرش‌شده")
    COMPLETED = "completed", _("پایان‌یافته")
    CANCELLED_BY_GUEST = "cancelled_by_guest", _("لغو توسط زائر")
    CANCELLED_BY_STAFF = "cancelled_by_staff", _("لغو توسط کارکنان")
    NO_SHOW = "no_show", _("عدم‌حضور")
    EXPIRED = "expired", _("منقضی‌شده")
    SUPERSEDED = "superseded", _("جانشین‌شده")

    @classmethod
    def active_statuses(cls) -> tuple[ReservationStatus, ...]:
        """وضعیت‌هایی که سانس را **اشغال** می‌کنند (مبنای قید EXCLUDE)."""
        return (cls.HOLD, cls.CONFIRMED, cls.CHECKED_IN)

    @classmethod
    def terminal_statuses(cls) -> tuple[ReservationStatus, ...]:
        """وضعیت‌های پایانی — هیچ انتقالی از آن‌ها مجاز نیست."""
        return (
            cls.COMPLETED,
            cls.CANCELLED_BY_GUEST,
            cls.CANCELLED_BY_STAFF,
            cls.NO_SHOW,
            cls.EXPIRED,
            cls.SUPERSEDED,
        )

    @classmethod
    def cancellable_statuses(cls) -> tuple[ReservationStatus, ...]:
        """وضعیت‌هایی که لغو در آن‌ها معنا دارد."""
        return (cls.HOLD, cls.CONFIRMED)

    @classmethod
    def held_statuses(cls) -> tuple[ReservationStatus, ...]:
        """وضعیت‌هایی که زائر «واقعاً سانس را گرفته است» — مبنای قاعدهٔ فاصله.

        لغو‌شده‌ها این‌جا نیستند: زائری که لغو کرده، اقامتی نداشته است. سخت‌گیری
        برای لغو دیرهنگام، مسیر جداگانهٔ خودش را دارد (``late_cancel_cooldown``)،
        پس جریمهٔ دوباره معنا ندارد. «عدم‌حضور» اما می‌ماند، چون سانس را اشغال
        کرده و بستن حساب برای آن منصفانه است.
        """
        return (cls.HOLD, cls.CONFIRMED, cls.CHECKED_IN, cls.COMPLETED, cls.NO_SHOW)


class ReservationSource(models.TextChoices):
    """منبع ایجاد رزرو — برای تحلیل و تفکیک مسئولیت."""

    WEB = "web", _("وب (خود زائر)")
    PHONE = "phone", _("تلفنی")
    DESK = "desk", _("پذیرش حضوری")
    ADMIN = "admin", _("پنل مدیریت")


class CancelReasonKind(models.TextChoices):
    """دستهٔ دلیل لغو — برای تحلیل و سیاست جریمه."""

    GUEST_REQUEST = "guest_request", _("درخواست زائر")
    MAINTENANCE = "maintenance", _("تعمیر و نگهداری")
    OVERBOOKING = "overbooking", _("اصلاح رزرو")
    DUPLICATE = "duplicate", _("رزرو تکراری")
    NO_SHOW = "no_show", _("عدم‌حضور")
    OTHER = "other", _("سایر")


class ReservationEventKind(models.TextChoices):
    """نوع رخداد ثبت‌شده روی یک رزرو (تاریخ کامل زندگی رزرو)."""

    CREATED = "created", _("ایجاد")
    CONFIRMED = "confirmed", _("تأیید")
    CANCELLED = "cancelled", _("لغو")
    MOVED = "moved", _("جابه‌جایی")
    CHECKED_IN = "checked_in", _("پذیرش")
    CHECKED_OUT = "checked_out", _("تخلیه")
    NO_SHOW = "no_show", _("عدم‌حضور")
    EXPIRED = "expired", _("انقضای نگه‌داشت")
    NOTE = "note", _("یادداشت")
