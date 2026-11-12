"""دستور بستن خودکار پرونده‌های اقامتِ تمام‌شده — کار زمان‌بندی‌شدهٔ ده‌دقیقه‌ای.

بی این زمان‌بند، رزروی که پیشخوان تخلیه یا عدم‌حضورش را ثبت نکند برای همیشه «فعال»
می‌ماند: زائر نمی‌تواند دل‌نوشته بنویسد و گزارش اشغال، اقامتی خیالی را می‌شمارد.

    manage.py settle_stays              # بستن پرونده‌های رسیده (پیش‌فرض ۲۰۰ در هر اجرا)
    manage.py settle_stays --dry-run    # فقط گزارش: چه چیزی با کدام حالت بسته خواهد شد
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.reservation.settlement import DEFAULT_LIMIT, settle_ended_stays


class Command(BaseCommand):
    """اجرای یک نوبت بستن خودکار."""

    help = "پرونده‌های اقامتِ تمام‌شده‌ای را که پیشخوان پس از مهلت سیاستی نبسته، خودکار می‌بندد."

    def add_arguments(self, parser: CommandParser) -> None:
        """سقف هر اجرا و حالت آزمایشی."""
        parser.add_argument(
            "--limit",
            type=int,
            default=DEFAULT_LIMIT,
            help=f"بیشینهٔ پرونده در هر اجرا (پیش‌فرض {DEFAULT_LIMIT})",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="هیچ چیز را تغییر نده؛ فقط بگو چه پرونده‌هایی بسته خواهند شد",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """یک نوبت بستن و چاپ کارنامه (یک خط؛ جنگو مقدار بازگشتی را دوباره چاپ می‌کند ⇒ چیزی برنمی‌گردانیم)."""
        del args
        dry_run = bool(options.get("dry_run"))
        report = settle_ended_stays(
            limit=int(options.get("limit") or DEFAULT_LIMIT), dry_run=dry_run
        )
        message = report.summary() + (" — آزمایشی؛ چیزی تغییر نکرد" if dry_run else "")
        self.stdout.write(self.style.SUCCESS(message))
