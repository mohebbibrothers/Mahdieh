"""دستور آزادسازی نگه‌داشت‌های منقضی — کار زمان‌بندی‌شدهٔ دقیقه‌ای.

کاربرد: در کرون/سلری هر دقیقه اجرا می‌شود. سانس بلافاصله پس از انقضا آزاد است
(قید دیتابیس تنها روی وضعیت‌های فعال اعمال می‌شود)؛ این دستور وضعیت را صریح
می‌کند تا گزارش‌ها و صف انتظار (فاز ۶) رخداد درست را ببینند.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.reservation.services import expire_holds


class Command(BaseCommand):
    """اجرای آزادسازی نگه‌داشت‌های منقضی‌شده."""

    help = "نگه‌داشت‌های موقت منقضی‌شده را آزاد می‌کند و رخداد Outbox می‌فرستد."

    def add_arguments(self, parser: CommandParser) -> None:
        """آرگومان‌های اختیاری برای اجرای دسته‌ای/تکرارشونده."""
        parser.add_argument(
            "--loop",
            action="store_true",
            help="اجرای پیوسته با فاصلهٔ زمانی (برای محیط توسعه و استقرار ساده)",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=60,
            help="فاصلهٔ اجرای حلقه به ثانیه (پیش‌فرض ۶۰)",
        )

    def handle(self, *args: Any, **options: Any) -> str:
        """اجرای یک‌بار یا حلقه‌ای."""
        del args
        if options.get("loop"):
            import time

            interval = max(5, int(options.get("interval") or 60))
            self.stdout.write(self.style.WARNING(f"حلقهٔ آزادسازی هر {interval} ثانیه آغاز شد."))
            try:
                while True:
                    released = expire_holds()
                    if released:
                        self.stdout.write(f"آزاد شد: {released}")
                    time.sleep(interval)
            except KeyboardInterrupt:  # pragma: no cover - توقف دستی
                self.stdout.write(self.style.WARNING("متوقف شد."))
                return "stopped"

        released = expire_holds()
        message = f"نگه‌داشت آزادشده: {released}"
        self.stdout.write(self.style.SUCCESS(message))
        return message
