"""جاروی نگهداری داده — یک فرمان شبانه برای همهٔ پنجره‌های عمر داده.

چرا یک فرمان؟ چون «نگهداری» یک تصمیم حاکمیتی است، نه سه کار پراکنده. این‌جا
هر چیزی که عمر دارد با هم و با گزارش شفاف پاک/کمینه می‌شود:

    • هویت زائران بی‌فعالیت ⇒ کمینه‌سازی (نام/کد ملی پاک، تلفن می‌ماند).
    • دفتر پیامک کهنه ⇒ حذف (پس از پنجرهٔ پیگیری).
    • کلیدهای هم‌توانی منقضی ⇒ حذف.

``--dry-run`` هیچ نوشتنی نمی‌کند و فقط می‌شمارد؛ پیش از نخستین اجرای واقعی روی
دادهی تولیدی، همین را اجرا کنید. پنجره‌ها سیاست‌اند (پنل مدیر) نه ثابت‌های کد.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.core.idempotency import purge_expired
from apps.identity.retention import retention_stats, run_guest_retention
from apps.notifications.retention import purge_sms_ledger, sms_ledger_window_days


class Command(BaseCommand):
    """اجرای جاروی نگهداری: هویت زائر، دفتر پیامک، کلیدهای هم‌توانی."""

    help = "پنجره‌های نگهداری داده را اعمال می‌کند (کمینه‌سازی هویت + پاک‌سازی دفترها)."

    def add_arguments(self, parser: CommandParser) -> None:
        """گزینه‌های اجرا."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="فقط بشمار و گزارش بده؛ هیچ تغییری در داده نده",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="سقف تعداد زائران کمینه‌شده در هر اجرا (پیش‌فرض ۲۰۰)",
        )
        parser.add_argument(
            "--stats",
            action="store_true",
            help="فقط آمار وضعیت نگهداری را نشان بده (بدون هیچ اقدامی)",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 - امضای جنگو
        """اجرای جارو و چاپ گزارش فارسی."""
        dry_run = bool(options["dry_run"])

        if options["stats"]:
            stats = retention_stats()
            self.stdout.write(
                f"پنجرهٔ نگهداری هویت: {stats['window_days']} روز (مرز: {stats['cutoff']})"
            )
            self.stdout.write(
                f"واجد شرط کمینه‌سازی: {stats['eligible']} · "
                f"پیش‌تر کمینه‌شده: {stats['already_redacted']} · "
                f"در پنجرهٔ نگهداری: {stats['inside_window']}"
            )
            return

        guests = run_guest_retention(dry_run=dry_run, limit=int(options["limit"]))
        sms_rows = purge_sms_ledger(dry_run=dry_run)
        idem_rows = 0 if dry_run else purge_expired()
        expired_keys = _count_expired_idempotency() if dry_run else idem_rows

        self.stdout.write(self.style.HTTP_INFO("── نگهداری دادهٔ زائر ──"))
        self.stdout.write(guests.summary)
        self.stdout.write(self.style.HTTP_INFO("── دفترهای عملیاتی ──"))
        self.stdout.write(
            f"دفتر پیامک (پنجرهٔ {sms_ledger_window_days()} روز): "
            f"{'حذف‌شدنی' if dry_run else 'حذف‌شده'} {sms_rows}"
        )
        self.stdout.write(
            f"کلیدهای هم‌توانی منقضی: {'حذف‌شدنی' if dry_run else 'حذف‌شده'} {expired_keys}"
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("حالت آزمایشی بود؛ هیچ داده‌ای تغییر نکرد."))
        self.stdout.write(self.style.SUCCESS(guests.summary))


def _count_expired_idempotency() -> int:
    """شمارش کلیدهای منقضی بدون حذف — برای گزارش حالت آزمایشی."""
    from django.utils import timezone

    from apps.core.idempotency import IdempotencyKey

    return int(IdempotencyKey.all_objects.filter(expires_at__lt=timezone.now()).count())
