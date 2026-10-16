"""شمارشی‌های دامنهٔ صف انتظار."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class WaitlistStatus(models.TextChoices):
    """چرخهٔ عمر یک درخواست در صف انتظار.

    مسیر خوش‌بینانه: ``WAITING`` → ``OFFERED`` → ``CONVERTED``.
    مسیرهای خروج: ``EXPIRED`` (پنجرهٔ زمانی گذشت)، ``WITHDRAWN`` (خود زائر انصراف
    داد)، ``DECLINED`` (پیشنهاد را نپذیرفت)، ``UNFULFILLED`` (به سقف پیشنهاد رسید).
    """

    WAITING = "waiting", _("در انتظار")
    OFFERED = "offered", _("پیشنهاد ارسال‌شده")
    CONVERTED = "converted", _("تبدیل به رزرو")
    DECLINED = "declined", _("پیشنهاد پذیرفته‌نشده")
    EXPIRED = "expired", _("پنجرهٔ زمانی گذشت")
    WITHDRAWN = "withdrawn", _("انصراف زائر")
    UNFULFILLED = "unfulfilled", _("پس از سقف پیشنهادها")

    @classmethod
    def active_statuses(cls) -> tuple[str, ...]:
        """وضعیت‌هایی که «در جریان» شمرده می‌شوند (سهمیه و یکتایی)."""
        return (cls.WAITING, cls.OFFERED)

    @classmethod
    def terminal_statuses(cls) -> tuple[str, ...]:
        """وضعیت‌های پایانی — دیگر در صف شمرده نمی‌شوند."""
        return (
            cls.CONVERTED,
            cls.DECLINED,
            cls.EXPIRED,
            cls.WITHDRAWN,
            cls.UNFULFILLED,
        )


class OfferStatus(models.TextChoices):
    """سرنوشت یک پیشنهاد مشخص برای یک سانس آزادشده."""

    OPEN = "open", _("باز (در انتظار پاسخ)")
    ACCEPTED = "accepted", _("پذیرفته‌شده")
    DECLINED = "declined", _("ردشده")
    EXPIRED = "expired", _("منقضی")
    SUPERSEDED = "superseded", _("جانشین‌شده")

    @classmethod
    def live_statuses(cls) -> tuple[str, ...]:
        """وضعیت‌هایی که یک پیشنهاد را «زنده» می‌کنند."""
        return (cls.OPEN,)


class DeclineReason(models.TextChoices):
    """دلیل رد پیشنهاد — برای تحلیل کیفیت پیشنهادها."""

    TIME_NOT_SUITABLE = "time_not_suitable", _("زمان مناسب نیست")
    PLAN_CHANGED = "plan_changed", _("برنامه تغییر کرد")
    FOUND_ELSEWHERE = "found_elsewhere", _("جای دیگری پیدا شد")
    BY_MISTAKE = "by_mistake", _("اشتباه ثبت شد")
    OTHER = "other", _("سایر")
