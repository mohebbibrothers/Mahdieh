"""دستور رلهٔ Outbox — برداشت رخدادها و انتشار آن‌ها.

دو حالت کاربرد:
    • ``--once``   : یک چرخه (برای زمان‌بند/کرون یا عیب‌یابی)
    • ``--loop``   : حلقهٔ پیوسته (برای کارگر مستقل در استقرارهای سبک)
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.choices import OutboxStatus
from apps.core.metrics import OUTBOX_DEAD, OUTBOX_LAG
from apps.core.outbox import OutboxEvent, fetch_pending_batch, mark_failed, mark_published


class Command(BaseCommand):
    """اجرای رلهٔ Outbox."""

    help = "برداشت رخدادهای دامنه از Outbox و انتشار آن‌ها به مصرف‌کننده‌ها."

    def add_arguments(self, parser: Any) -> None:
        """افزودن گزینه‌های اجرا."""
        parser.add_argument("--limit", type=int, default=100, help="حداکثر رخداد در هر چرخه")
        parser.add_argument("--once", action="store_true", help="یک چرخه و خروج")
        parser.add_argument("--loop", action="store_true", help="اجرای پیوسته")
        parser.add_argument("--interval", type=float, default=2.0, help="فاصلهٔ چرخه‌ها (ثانیه)")

    def handle(self, *args: Any, **options: Any) -> None:
        """چرخهٔ اصلی.

        ``*args`` بخشی از امضای دستورهای مدیریتی جنگو است.
        """
        del args
        limit: int = options["limit"]
        interval: float = options["interval"]

        while True:
            self._beat()
            processed = self._run_cycle(limit)
            if not options["loop"]:
                self.stdout.write(self.style.SUCCESS(f"✅ {processed} رخداد پردازش شد."))
                return
            if processed == 0:
                time.sleep(interval)

    @staticmethod
    def _beat() -> None:
        """ثبت ضربان رله (برای پایش «رله زنده است؟»).

        پایش فقط «چند رخداد در صف است» را می‌بیند؛ صفِ خالیِ همیشه‌خالی با رلهٔ
        مرده از صفِ خالیِ سالم قابل‌تفکیک نیست. این ضربان همان تفکیک است.
        """
        from django.core.cache import cache
        from django.utils import timezone

        from apps.core.health import OUTBOX_HEARTBEAT_KEY

        with contextlib.suppress(Exception):
            # قطعیِ کش نباید رله را متوقف کند؛ ضربان «خوب است اگر بشود».
            cache.set(OUTBOX_HEARTBEAT_KEY, timezone.now(), timeout=3600)

    def _run_cycle(self, limit: int) -> int:
        """یک چرخهٔ کامل: برداشت، انتشار، علامت‌گذاری — در یک تراکنش."""
        processed = 0
        with transaction.atomic():
            batch = fetch_pending_batch(limit=limit)
            for event in batch:
                try:
                    # انتشار واقعی در فاز ۵ به Valkey Streams وصل می‌شود؛
                    # در فاز ۰ فقط چرخهٔ عمر و متریک‌ها آزموده می‌شوند.
                    self._dispatch(event)
                    mark_published(event)
                    processed += 1
                except Exception as exc:
                    mark_failed(event, str(exc), backoff_seconds=min(300, 2**event.attempts))
        self._refresh_gauges()
        return processed

    def _dispatch(self, event: Any) -> None:
        """نقطهٔ اتصال مصرف‌کننده‌ها (در فازهای بعدی تکمیل می‌شود)."""
        self.stdout.write(f"   ↳ {event.event_type} · {event.aggregate_id}")

    def _refresh_gauges(self) -> None:
        """به‌روزرسانی متریک‌های عمق صف."""
        OUTBOX_LAG.set(OutboxEvent.all_objects.filter(status=OutboxStatus.PENDING).count())
        OUTBOX_DEAD.set(OutboxEvent.all_objects.filter(status=OutboxStatus.DEAD).count())
