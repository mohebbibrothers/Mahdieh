"""فرمان زمان‌بندی‌شده: یادآور پیامکی پیش از پذیرش.

چرا فرمان و نه کرون خارجی؟ چون سیاستش («چند ساعت قبل»، «ساعات سکوت») در
``PolicyRevision`` زندگی می‌کند و باید بتوان بدون تغییر استقرار عوضش کرد. پس
فرمان هر ساعت اجرا می‌شود، پنجرهٔ یادآوری را از سیاست زنده می‌خواند و تنها برای
اقامت‌های همان پنجره پیام می‌فرستد.

    manage.py send_booking_reminders            # اجرای واقعی
    manage.py send_booking_reminders --dry-run  # فهرست بدون ارسال
    manage.py send_booking_reminders --lead-hours 6  # پنجرهٔ موقت (آزمون میدانی)

هر فرمان هرگز استثنا پرت نمی‌کند: خروجی همیشه گزارش است و شکست یک رکورد بقیهٔ
فهرست را متوقف نمی‌کند.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from apps.core.jalali import clock_label, format_jalali
from apps.notifications import patterns
from apps.notifications.choices import SmsTemplateKey
from apps.reservation import reminders
from apps.reservation.policy import quiet_hours

QUIET_NOTE = "یادآور در ساعات سکوت فرستاده نمی‌شود (سیاست notification.quiet_hours)."


class Command(BaseCommand):
    """ارسال یادآور پیامکی برای اقامت‌هایی که نزدیک شروعشان است."""

    help = "برای رزروهای فعالی که اقامتشان در پنجرهٔ یادآوری آغاز می‌شود، پیامک یادآور می‌فرستد."

    def add_arguments(self, parser: CommandParser) -> None:
        """گزینه‌های اجرا: سقف، پنجرهٔ موقت، و حالت آزمایشی."""
        parser.add_argument("--limit", type=int, default=reminders.DEFAULT_LIMIT)
        parser.add_argument(
            "--lead-hours",
            type=int,
            default=None,
            help="پنجرهٔ یادآوری بر حسب ساعت (پیش‌فرض: از سیاست).",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *_args: Any, **options: Any) -> str:
        """اجرای چرخهٔ یادآوری و چاپ خلاصهٔ نتیجه."""
        limit = max(1, int(options["limit"]))
        lead = options["lead_hours"]
        lead = reminders.lead_hours() if lead is None else max(0, int(lead))

        if lead <= 0:
            self.stdout.write(
                self.style.WARNING("پنجرهٔ یادآوری صفر است؛ سیاست، یادآور را خاموش کرده.")
            )
            return ""
        if quiet_hours(timezone.now()):
            self.stdout.write(self.style.WARNING(QUIET_NOTE))
            return ""
        if options["dry_run"]:
            return self._dry_run(lead=lead, limit=limit)

        report = reminders.run(limit=limit)
        for problem in report.skipped:
            self.stderr.write(f"رد شد: {problem}")
        if report.blocked:
            self._warn_about_blocked(report.blocked)
        self.stdout.write(self.style.SUCCESS(report.summary))
        return report.summary

    def _dry_run(self, *, lead: int, limit: int) -> str:
        """نمایش فهرست واجدها بدون هیچ ارسالی (و بدون هیچ رکوردی در دفتر تحویل)."""
        pending, already = reminders.candidates(now=timezone.now(), lead=lead, limit=limit)
        for reservation in pending:
            start = timezone.localtime(reservation.starts_at)
            self.stdout.write(
                f"[آزمایشی] {reservation.code} — {reservation.unit.title}"
                f" — پذیرش {format_jalali(start)} ساعت {clock_label(start)}"
            )
        summary = f"یادآور (آزمایشی): {len(pending)} واجد، {already} پیش‌تر یادآوری‌شده"
        self.stdout.write(self.style.SUCCESS(summary))
        return summary

    def _warn_about_blocked(self, blocked: int) -> None:
        """توضیح مسدودی: اگر الگوی پنل کد ندارد، این طبیعی است، نه خطا.

        گزارش مسدودی باید **قابل اقدام** باشد: کاربر باید بداند چه چیزی را کجا
        بگذارد تا پیام فردا برود (کد الگو داده است، نه کد برنامه).
        """
        key = SmsTemplateKey.BOOKING_REMINDER.value
        if patterns.resolve_binding(key) is None:
            self.stdout.write(
                self.style.WARNING(
                    f"{blocked} پیام مسدود ثبت شد: کد الگوی «یادآور پیش از پذیرش» در "
                    "SMS_PATTERN_CODES نیست. کد که از پنل رسید، همان‌جا بگذارید؛ "
                    "فعال‌سازی این الگو کاری برنامه‌نویسی نیست، کاری داده‌ای است. "
                    "پیام‌های مسدودشدهٔ امروز بازپخش نمی‌شوند (زائر از «رزروهای من» "
                    "باخبر است)؛ هر رزروی که فردا واجد شود، یادآور می‌گیرد."
                )
            )
