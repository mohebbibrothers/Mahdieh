"""شمارشی‌های دامنهٔ اعلان."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class SmsStatus(models.TextChoices):
    """سرنوشت یک پیامک در دفتر تحویل."""

    QUEUED = "queued", _("در صف")
    SENT = "sent", _("ارسال‌شده")
    SUPPRESSED = "suppressed", _("ارسال‌نشده (محیط غیرتولیدی)")
    BLOCKED = "blocked", _("مسدود (سهمیه/سیاست)")
    FAILED = "failed", _("ناموفق")


class SmsTemplateKey(models.TextChoices):
    """کلید قالب‌های پیامک — هر کلید یک قالب نسخه‌دار و پیش‌نمایش‌پذیر.

    در فاز ۵ قالب‌ها به دیتابیس منتقل و قابل ویرایش توسط مدیر می‌شوند؛ کلیدها
    از همین حالا ثابت‌اند تا هیچ‌گاه پیام خام در کد پخش نشود.
    """

    OTP_LOGIN = "otp_login", _("کد ورود")
    WELCOME = "welcome", _("خوش‌آمد پس از تکمیل پروفایل")
    BOOKING_CONFIRMED = "booking_confirmed", _("تأیید رزرو")
    BOOKING_CANCELLED = "booking_cancelled", _("لغو رزرو")
    BOOKING_REMINDER = "booking_reminder", _("یادآور پیش از پذیرش")
    OFFER_CREATED = "offer_created", _("پیشنهاد سانس آزادشده")
    OFFER_ACCEPTED = "offer_accepted", _("تأیید پذیرش پیشنهاد")
    WAITLIST_JOINED = "waitlist_joined", _("ثبت درخواست انتظار")
    WAITLIST_REQUEUED = "waitlist_requeued", _("پایان فرصت پیشنهادها")
    STAFF_ALERT = "staff_alert", _("هشدار عملیاتی کارکنان")
    DRY_RUN = "dry_run", _("پیام آزمایشی")
