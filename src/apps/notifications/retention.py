"""نگهداری دفتر پیامک — پاک‌سازی ردیف‌های کهنهٔ ارسال.

دفتر ``SmsDelivery`` برای پیگیری و جلوگیری از ارسال تکراری است؛ بعد از چند ماه
دیگر ارزش عملیاتی ندارد و نگه‌داشتنش فقط ریسک است (شمارهٔ پوشانده، متن فشردهٔ
پیام). پنجره از سیاست ``data.sms_ledger_retention_days`` می‌آید.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from apps.notifications.models import SmsDelivery
from apps.reservation.policy import policy_int

RETENTION_POLICY_KEY = "data.sms_ledger_retention_days"
DEFAULT_RETENTION_DAYS = 180
MIN_RETENTION_DAYS = 7


def sms_ledger_window_days() -> int:
    """پنجرهٔ نگهداری دفتر پیامک (روز) — با کف ۷ روز (پنجرهٔ لازم برای پیگیری)."""
    return max(MIN_RETENTION_DAYS, policy_int(RETENTION_POLICY_KEY))


def purge_sms_ledger(
    *, now: datetime | None = None, window_days: int | None = None, dry_run: bool = False
) -> int:
    """شمردن/پاک‌کردن ردیف‌های کهنهٔ دفتر پیامک.

    ``dry_run`` فقط می‌شمارد — همان چیزی که برای تخمین پیش از حذف لازم است.
    """
    window = window_days if window_days is not None else sms_ledger_window_days()
    cutoff = (now or timezone.now()) - timedelta(days=window)
    queryset = SmsDelivery.objects.filter(created_at__lt=cutoff)
    count = queryset.count()
    if not dry_run and count:
        queryset.delete()
    return count
