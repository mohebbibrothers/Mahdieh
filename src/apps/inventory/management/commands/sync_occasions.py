"""همگام‌سازی مناسبت‌ها و ایام پرتقاضا با تقویم رسمی کشور (idempotent؛ کار شبانهٔ cron و استقرار)."""

from __future__ import annotations

from argparse import ArgumentParser
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.jalali import format_jalali
from apps.core.text import to_persian_digits
from apps.inventory.services import SYNC_DAYS_AHEAD, default_sync_window, sync_official_occasions


class Command(BaseCommand):
    """تعطیلات رسمی و مناسبت‌های مذهبی بازهٔ پیش‌رو را با پایگاه‌داده همگام می‌کند."""

    help = (
        "تعطیلات رسمی و مناسبت‌های مذهبی (ولادت‌ها، شهادت‌ها، عرفه، قربان، غدیر، …) را از تقویم "
        "رسمی کشور در جدول مناسبت‌ها همگام می‌کند."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """گزینه‌ها: طول افق و حالت بی‌صدا."""
        parser.add_argument(
            "--days-ahead",
            type=int,
            default=SYNC_DAYS_AHEAD,
            help="چند روز آینده همگام شود (پیش‌فرض ۴۰۰)",
        )
        parser.add_argument("--quiet", action="store_true", help="فقط خطاها")

    def handle(self, *args: Any, **options: Any) -> None:
        """اجرای همگام‌سازی و گزارش فارسی."""
        del args
        start, _end = default_sync_window()
        end = timezone.localdate() + timedelta(days=max(1, options["days_ahead"]))
        report = sync_official_occasions(start=start, end=end)
        if options["quiet"]:
            return
        fa = to_persian_digits
        self.stdout.write(
            self.style.SUCCESS(
                f"✓ تقویم رسمی {format_jalali(report.start)} تا {format_jalali(report.end)}: "
                f"{fa(report.total)} مناسبت · {fa(report.created)} تازه · {fa(report.updated)} "
                f"به‌روز · {fa(report.removed)} حذف · {fa(report.provisional)} تقریبی"
            )
        )
