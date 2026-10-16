"""فرمان عملیاتی: بررسی یکپارچگی زنجیرهٔ حسابرسی و لنگرها.

چرا فرمان و نه یک خط ``python -c`` در Makefile؟ چون این بررسی **شاهد قانونی**
سازمان است: نتیجه‌اش در گزارش حادثه و تمرین DR استناد می‌شود. یک فرمان رسمی
یعنی: کد تست‌پذیر، خروجی قابل‌ماشین‌خوان (``--json``)، کد خروج معنادار برای
Cron/Alerting، و امکان اجرا روی **هر** پایگاه‌داده‌ای (از جمله نسخهٔ
بازیابی‌شده در تمرین فاجعه).

خروجی‌ها:
    • شمار رکوردها و بازهٔ ترتیبی
    • بررسی پیوستگی و صحت هش هر رکورد
    • بررسی امضای HMAC همهٔ لنگرها (کشف دستکاری لنگر)
    • مقایسهٔ آخرین لنگر با وضعیت فعلی (آیا چیزی پس از آخرین لنگر اضافه شده؟)
"""

from __future__ import annotations

import json
from typing import Any, Final

from django.core.management.base import BaseCommand, CommandParser

from apps.audit.models import AuditAnchor, AuditRecord
from apps.audit.services import latest_sequence, verify_anchor, verify_chain
from apps.core.errors import DomainError

EXIT_OK: Final[int] = 0
EXIT_BROKEN: Final[int] = 1


class Command(BaseCommand):
    """بررسی سلامت زنجیرهٔ حسابرسی."""

    help = "پیوستگی و صحت زنجیرهٔ حسابرسی و امضای لنگرها را بررسی می‌کند."

    def add_arguments(self, parser: CommandParser) -> None:
        """گزینه‌ها: خروجی JSON، سقف بررسی، و سخت‌گیری روی نبود لنگر."""
        parser.add_argument("--json", action="store_true", help="خروجی ماشین‌خوان برای پایش.")
        parser.add_argument(
            "--limit-anchors", type=int, default=0, help="حداکثر لنگر بررسی‌شده (۰=همه)."
        )
        parser.add_argument(
            "--require-anchor",
            action="store_true",
            help="اگر هیچ لنگری ثبت نشده باشد، با خطا خارج شو.",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """اجرای بررسی و گزارش نتیجه."""
        as_json: bool = bool(options["json"])
        limit_anchors: int = int(options["limit_anchors"])
        require_anchor: bool = bool(options["require_anchor"])

        records = AuditRecord.all_objects.order_by("sequence")
        total = records.count()
        first_sequence = records.values_list("sequence", flat=True).first() or 0
        last_sequence = latest_sequence()

        chain_ok, broken_at = verify_chain()

        anchors = AuditAnchor.objects.order_by("-anchored_at")
        if limit_anchors > 0:
            anchors = anchors[:limit_anchors]
        anchor_results: list[dict[str, Any]] = [
            {
                "id": int(anchor.pk),
                "last_sequence": int(anchor.last_sequence),
                "record_count": int(anchor.record_count),
                "anchored_at": anchor.anchored_at.isoformat(),
                "valid": verify_anchor(anchor),
            }
            for anchor in anchors
        ]
        broken_anchors = [row["id"] for row in anchor_results if not row["valid"]]
        newest_anchor = anchor_results[0] if anchor_results else None

        unanchored = 0
        if newest_anchor is not None:
            unanchored = max(0, last_sequence - int(newest_anchor["last_sequence"]))

        healthy = chain_ok and not broken_anchors and (not require_anchor or anchor_results)

        payload = {
            "healthy": healthy,
            "records": total,
            "sequence_range": [first_sequence, last_sequence],
            "chain_valid": chain_ok,
            "broken_at_sequence": broken_at,
            "anchors_checked": len(anchor_results),
            "broken_anchors": broken_anchors,
            "latest_anchor_sequence": (
                int(newest_anchor["last_sequence"]) if newest_anchor is not None else None
            ),
            "records_since_latest_anchor": unanchored,
        }

        if as_json:
            self.stdout.write(json.dumps(payload, ensure_ascii=False))
        else:
            self._report(payload)

        if not healthy:
            raise DomainError("زنجیرهٔ حسابرسی سالم نیست.")

    def _report(self, payload: dict[str, Any]) -> None:
        """گزارش خوانا برای اپراتور."""
        mark = "✅" if payload["healthy"] else "❌"
        self.stdout.write(f"{mark} زنجیرهٔ حسابرسی")
        self.stdout.write(
            f"   رکوردها: {payload['records']} | بازهٔ ترتیبی: "
            f"{payload['sequence_range'][0]}…{payload['sequence_range'][1]}"
        )
        if payload["chain_valid"]:
            self.stdout.write("   پیوستگی و هش رکوردها: سالم")
        else:
            self.stdout.write(
                f"   پیوستگی و هش رکوردها: شکسته در شمارهٔ {payload['broken_at_sequence']}"
            )
        if payload["broken_anchors"]:
            self.stdout.write(f"   لنگرهای دست‌کاری‌شده: {payload['broken_anchors']}")
        elif payload["anchors_checked"]:
            self.stdout.write(f"   لنگرها: {payload['anchors_checked']} لنگر، همه معتبر")
        else:
            self.stdout.write("   لنگرها: هیچ لنگری ثبت نشده است")
        if payload["latest_anchor_sequence"]:
            self.stdout.write(
                f"   رکوردهای پس از آخرین لنگر: {payload['records_since_latest_anchor']}"
            )
