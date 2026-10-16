"""دستور ``export_report`` — ساخت گزارش اکسل از خط فرمان (برای پشتیبان و آرشیو).

نمونه:
    uv run python src/manage.py export_report --from 2026-10-01 --to 2026-10-31 --out /tmp/r.xlsx
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from apps.reporting.services import build_report


class Command(BaseCommand):
    """ساخت فایل اکسل گزارش و چاپ اثر انگشت آن."""

    help = "ساخت خروجی اکسل گزارش رزروها برای یک بازهٔ تاریخ"

    def add_arguments(self, parser: CommandParser) -> None:
        """افزودن آرگومان‌های بازه و مقصد."""
        parser.add_argument("--from", dest="from_raw", default=None, help="تاریخ آغاز (YYYY-MM-DD)")
        parser.add_argument("--to", dest="to_raw", default=None, help="تاریخ پایان (YYYY-MM-DD)")
        parser.add_argument("--units", dest="units", default="", help="کد واحدها با کاما")
        parser.add_argument("--out", dest="out", default="", help="مسیر فایل خروجی")

    def handle(self, *_args: object, **options: object) -> None:
        """ساخت گزارش و نوشتن آن روی دیسک."""
        units = tuple(
            item.strip() for item in str(options.get("units") or "").split(",") if item.strip()
        )
        report = build_report(
            from_raw=str(options.get("from_raw") or "") or None,
            to_raw=str(options.get("to_raw") or "") or None,
            unit_codes=units,
        )
        stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
        destination = Path(str(options.get("out") or f"mahdieh-report-{stamp}.xlsx"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(report.content)
        self.stdout.write(
            self.style.SUCCESS(
                f"گزارش ساخته شد: {destination} · {len(report.content)} بایت · "
                f"sha256={report.sha256} · ردیف‌ها={report.rows}"
            )
        )
