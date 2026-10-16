"""فرمان زمان‌بندی‌شده: درخواست نظرسنجی برای اقامت‌های پایان‌یافته.

چرا فرمان و نه «ارسال در لحظهٔ تخلیه»؟ چون تجربهٔ زائر مهم است: پیامک نظرسنجی
باید چند ساعت **پس از** رسیدن به خانه برسد، نه وقتی بار و بچه در دست است. پس
اجرای دوره‌ای (هر ساعت یا هر شب، بسته به Cron/Beat) این کار را با یک قاعدهٔ
ساده انجام می‌دهد: هر اقامت تمام‌شده‌ای که دست‌کم ``--after-hours`` ساعت از
پایانش گذشته و تا کنون درخواستی نگرفته، یک پیامک می‌گیرد.

ایمنی‌ها:
    • ``--limit`` سقف هر اجرا را می‌بندد (سهمیهٔ روزانهٔ پیامک هم سد دوم است).
    • ``--dry-run`` فهرست را نشان می‌دهد و چیزی نمی‌فرستد.
    • درخواست‌های دوباره با ``requested_at`` جلوگیری می‌شوند و کلید
      ``dedup_key`` پیامک هم یک لایهٔ دیگر است.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Final

from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from apps.reservation import feedback as feedback_services
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Reservation

DEFAULT_LIMIT: Final[int] = 40
DEFAULT_AFTER_HOURS: Final[int] = 6


class Command(BaseCommand):
    """ارسال پیامک نظرسنجی به اقامت‌های تازه‌تمام‌شده."""

    help = "برای اقامت‌های پایان‌یافته که نظرسنجی نگرفته‌اند، پیامک نظرسنجی می‌فرستد."

    def add_arguments(self, parser: CommandParser) -> None:
        """گزینه‌های اجرا: سقف، فاصلهٔ زمانی از پایان، و حالت آزمایشی."""
        parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
        parser.add_argument(
            "--after-hours",
            type=int,
            default=DEFAULT_AFTER_HOURS,
            help="دست‌کم چند ساعت از پایان اقامت گذشته باشد.",
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--resend-after-days",
            type=int,
            default=0,
            help="اگر بزرگ‌تر از صفر باشد، برای درخواست‌های قدیمی‌تر از این تعداد روز یادآوری می‌فرستد.",
        )

    def handle(self, *_args: Any, **options: Any) -> str:
        """اجرای فرمان و گزارش خلاصهٔ نتیجه (متناسب با فرمان‌های خط‌فرمان دیگر)."""
        limit = max(1, int(options["limit"]))
        after_hours = max(0, int(options["after_hours"]))
        dry_run = bool(options["dry_run"])
        resend_days = max(0, int(options["resend_after_days"]))
        now = timezone.now()

        candidates = self._candidates(
            now=now, after_hours=after_hours, limit=limit, resend_days=resend_days
        )
        if not candidates:
            self.stdout.write(self.style.SUCCESS("اقامت واجد شرطی برای نظرسنجی پیدا نشد."))
            return ""

        sent = 0
        skipped = 0
        for reservation in candidates:
            label = f"{reservation.code} — {reservation.unit.code} ({reservation.ends_at:%Y-%m-%d})"
            if dry_run:
                self.stdout.write(f"[آزمایشی] {label}")
                continue
            try:
                feedback_services.request_feedback(
                    reservation=reservation, resend=bool(resend_days)
                )
            except Exception as error:  # فرمان هرگز نباید روی یک رکورد بد بایستد
                skipped += 1
                self.stderr.write(f"رد شد: {label} — {error}")
                continue
            sent += 1
            self.stdout.write(f"فرستاده شد: {label}")

        summary = f"نظرسنجی: {sent} ارسال، {skipped} رد، {len(candidates)} بررسی‌شده"
        self.stdout.write(self.style.SUCCESS(summary))
        return summary

    @staticmethod
    def _candidates(
        *, now: Any, after_hours: int, limit: int, resend_days: int
    ) -> list[Reservation]:
        """رزروهای پایان‌یافتهٔ واجد شرط، به‌ترتیب قدیمی‌ترین پایان.

        تنها یک پرسمان می‌زند: اقامت‌های تمام‌شده در پنجرهٔ مجاز که یا هرگز
        درخواست نگرفته‌اند، یا (در حالت یادآوری) درخواستشان کهنه شده است.
        """
        window_start = now - timedelta(days=feedback_services.WINDOW_DAYS)
        queryset = (
            Reservation.objects.filter(
                status=ReservationStatus.COMPLETED,
                # کران بالای بازهٔ اقامت، در قالب قیدهای خودِ RangeField
                stay_period__endswith__lte=now - timedelta(hours=after_hours),
                stay_period__endswith__gte=window_start,
            )
            .exclude(feedback__submitted_at__isnull=False)
            .select_related("unit", "guest")
            .order_by("stay_period")
        )
        if resend_days:
            stale_before = now - timedelta(days=resend_days)
            queryset = queryset.filter(feedback__requested_at__lte=stale_before)
        else:
            queryset = queryset.filter(feedback__requested_at__isnull=True)
        return list(queryset[:limit])
