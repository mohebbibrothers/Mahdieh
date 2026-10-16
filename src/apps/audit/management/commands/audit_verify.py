"""دستور ``audit_verify`` — پاسخ به پرسش «آیا زنجیرهٔ حسابرسی دست‌کاری شده؟».

این دستور، ابزار روزانهٔ مدیر و شرط تیکِ «راستی‌آزمایی» در نقشهٔ راه است:
    • پیوستگی زنجیره (هر رکورد به رکورد پیشین قفل شده است)
    • صحت امضای همهٔ لنگرها و هم‌خوانی هر لنگر با رکورد هم‌شماره‌اش
    • شمار رکوردهای ثبت‌شده و آخرین لنگر

خروجی ``--json`` برای پایش ماشینی (systemd timer + هشدار) و خروجی متنی برای
انسان. کد خروج ۱ یعنی «زنجیره مشکوک است» — و همین کد، هشدار را فعال می‌کند.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand

from apps.audit import services

FAIL_EXIT_CODE = 1
OK_EXIT_CODE = 0


class Command(BaseCommand):
    """بررسی یکپارچگی زنجیرهٔ حسابرسی و لنگرها."""

    help = "بررسی پیوستگی زنجیرهٔ حسابرسی، امضای لنگرها و هم‌خوانی آن‌ها."

    def add_arguments(self, parser: Any) -> None:
        """افزودن گزینه‌های اجرا."""
        parser.add_argument("--json", action="store_true", help="خروجی ماشین‌خوان (JSON)")
        parser.add_argument(
            "--fail-on-unanchored",
            action="store_true",
            help="اگر پس از آخرین لنگر رکورد تازه‌ای ثبت شده باشد، خطا بده",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 - امضای جنگو
        """اجرای بررسی و چاپ گزارش."""
        chain_ok, broken_at = services.verify_chain()
        records = services.latest_sequence()
        report = services.verify_anchors()
        unanchored = (
            records - report.latest_anchor_sequence if report.latest_anchor_sequence else records
        )
        unanchored_ok = not (options["fail_on_unanchored"] and unanchored > 0)
        healthy = bool(chain_ok and report.valid and unanchored_ok)

        payload: dict[str, Any] = {
            "healthy": healthy,
            "records": records,
            "chain_valid": chain_ok,
            "broken_at_sequence": broken_at,
            "anchors_checked": report.checked,
            "anchors_invalid": list(report.invalid_sequences),
            "latest_anchor_sequence": report.latest_anchor_sequence,
            "records_since_latest_anchor": unanchored,
        }
        if options["json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False))
        else:
            self._write_human(payload)
        if not healthy:
            raise SystemExit(FAIL_EXIT_CODE)

    def _write_human(self, payload: dict[str, Any]) -> None:
        """گزارش خوانا برای انسان — پاراگراف‌های کوتاه، بدون جدول شلوغ."""
        style = self.style
        if payload["chain_valid"]:
            self.stdout.write(style.SUCCESS("✔ زنجیرهٔ حسابرسی پیوسته و دست‌نخورده است."))
        else:
            self.stdout.write(
                style.ERROR(f"✘ زنجیره در رکورد شمارهٔ {payload['broken_at_sequence']} گسسته است.")
            )
        self.stdout.write(f"• رکوردهای ثبت‌شده: {payload['records']}")
        self.stdout.write(
            f"• لنگرها: {payload['anchors_checked']} بررسی‌شده، "
            f"{len(payload['anchors_invalid'])} نامعتبر"
        )
        self.stdout.write(f"• آخرین لنگر روی رکورد: {payload['latest_anchor_sequence'] or '—'}")
        self.stdout.write(f"• رکوردهای پس از آخرین لنگر: {payload['records_since_latest_anchor']}")
        if payload["healthy"]:
            self.stdout.write(style.SUCCESS("نتیجه: سالم ✅"))
        else:
            self.stdout.write(style.ERROR("نتیجه: نیازمند بررسی انسانی ❌"))
