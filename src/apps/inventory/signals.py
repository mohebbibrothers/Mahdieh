"""سیگنال‌های موجودی — اصلاح آغاز ماه قمری ⇒ همگام‌سازی بی‌درنگ مناسبت‌های تقویم."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.inventory.models import LunarMonthStart


@receiver(post_save, sender=LunarMonthStart, dispatch_uid="inventory.lunar_saved")
@receiver(post_delete, sender=LunarMonthStart, dispatch_uid="inventory.lunar_deleted")
def resync_occasions(sender: type[LunarMonthStart], **kwargs: Any) -> None:
    """پس از ثبت تراکنش، مناسبت‌های پنجرهٔ پیش‌فرض با جدول تازهٔ ماه‌ها همگام می‌شوند."""
    del sender
    if kwargs.get("raw"):
        return
    from apps.inventory.services import (
        default_sync_window,
        sync_official_occasions,
    )

    def run() -> None:
        """پس از ثبت تراکنش، مناسبت‌های رسمی بازهٔ پیش‌فرض را دوباره همگام می‌کند."""
        start, end = default_sync_window()
        sync_official_occasions(start=start, end=end)

    transaction.on_commit(run)
