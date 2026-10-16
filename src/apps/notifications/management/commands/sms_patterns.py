"""دستور ``sms_patterns`` — «کدام پیام‌ها امروز واقعاً به زائر می‌رسد؟»

پنل‌های ایرانی «ارسال الگومحور» را اجبار می‌کنند: بدون الگوی تأییدشده، پیام
نمی‌رود. این دستور، عکس وضعیت را نشان می‌دهد و — مهم‌تر — متن آمادهٔ درخواست
الگوهای باقی‌مانده را می‌سازد تا مدیر همان را برای پشتیبانی پنل بفرستد.

کاربردها:
    python manage.py sms_patterns              # جدول وضعیت الگوها
    python manage.py sms_patterns --json       # برای پایش ماشینی
    python manage.py sms_patterns --request    # متن آمادهٔ ارسال به پنل
    python manage.py sms_patterns --check      # کد خروج ۱ اگر قالب حیاتی بی‌الگو باشد
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import logging
from typing import Any

from django.core.management.base import BaseCommand

from apps.notifications import patterns

CHECK_EXIT_CODE = 1
OK_EXIT_CODE = 0

# ``--request`` خروجی‌ای است که کاربر **کپی و برای پشتیبانی پنل می‌فرستد**؛ پس
# باید تمیز و پیپ‌پذیر باشد. لاگ تهی‌شدن نگاشت به قالب جانشین، دانستنی تولیدی
# است ولی در متن نامه جایی ندارد — برای همین در همین حالت موقتاً خاموش می‌شود.
DOC_LOGGER = "apps.notifications.patterns"


class Command(BaseCommand):
    """بازرسی آمادگی الگوهای پیامک و ساخت متن درخواست کد الگو."""

    help = "وضعیت الگوهای پنل پیامک و متن درخواست الگوهای باقی‌مانده."

    def add_arguments(self, parser: Any) -> None:
        """افزودن گزینه‌های اجرا."""
        parser.add_argument("--json", action="store_true", help="خروجی ماشین‌خوان (JSON)")
        parser.add_argument("--request", action="store_true", help="چاپ متن درخواست برای پنل")
        parser.add_argument(
            "--check",
            action="store_true",
            help="کد خروج ۱ اگر قالب حیاتی بدون الگو باشد (مناسب CI و پایش)",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 - امضای جنگو
        """چاپ گزارش آمادگی الگوها."""
        with _quiet_document_logs(bool(options["request"])):
            readiness = patterns.pattern_readiness()
            self._emit(readiness, options)
        if options["check"] and readiness["critical_unbound"]:
            self.stderr.write(
                self.style.ERROR(
                    "قالب‌های حیاتی بدون الگو: " + "، ".join(readiness["critical_unbound"])
                )
            )
            raise SystemExit(CHECK_EXIT_CODE)

    def _emit(self, readiness: dict[str, Any], options: dict[str, Any]) -> None:
        """چاپ خروجی بر پایهٔ گزینه‌ها: JSON، متن درخواست، یا جدول انسانی."""
        if options["json"]:
            self.stdout.write(json.dumps(readiness, ensure_ascii=False, indent=2))
        elif options["request"]:
            self.stdout.write(patterns.request_text())
        else:
            self._write_human(readiness)

    def _write_human(self, readiness: dict[str, Any]) -> None:
        """جدول خوانا برای انسان: هر قالب، کد الگو و وضعیت آن."""
        style = self.style
        self.stdout.write(f"ارقام متغیرها: {readiness['digit_mode']}")
        self.stdout.write(f"کدهای فعال الگو: {len([c for c in readiness['codes'].values() if c])}")
        self.stdout.write("")
        self.stdout.write(style.HTTP_INFO("── الگوهای آماده ──"))
        for key in readiness["bound"]:
            resolved = patterns.resolve_binding(key)
            if resolved is None:  # pragma: no cover - ناسازگاری منطقی
                continue
            sent_key, binding = resolved
            marker = "" if sent_key == key else f"  (با قالب جانشین: {sent_key})"
            self.stdout.write(style.SUCCESS(f"  ✓ {key:<22} ⇒ {binding.code}{marker}"))
        if readiness["guest_unbound"]:
            self.stdout.write("")
            self.stdout.write(style.WARNING("── پیام زائر، بدون الگو (ارسال مسدود می‌شود) ──"))
            for key in readiness["guest_unbound"]:
                severity = style.ERROR if key in readiness["critical_unbound"] else style.WARNING
                self.stdout.write(severity(f"  ✗ {key}"))
        if readiness["internal_unbound"]:
            self.stdout.write("")
            self.stdout.write("── پیام‌های درونی (کارکنان/آزمون) بدون الگو ──")
            for key in readiness["internal_unbound"]:
                self.stdout.write(f"  • {key}")
        if readiness["critical_unbound"]:
            self.stdout.write("")
            self.stdout.write(
                style.ERROR(
                    "قالب‌های حیاتی بی‌الگو: "
                    + "، ".join(readiness["critical_unbound"])
                    + " — با SMS_ENABLED=true این‌ها خطای راه‌اندازی می‌دهند."
                )
            )
        if readiness["missing_catalog"]:
            self.stdout.write("")
            self.stdout.write(f"برای دریافت کد {len(readiness['missing_catalog'])} الگو: ")
            self.stdout.write("    python manage.py sms_patterns --request")


@contextmanager
def _quiet_document_logs(enabled: bool) -> Iterator[None]:
    """خاموش‌کردن لاگ تشخیصی وقتی خروجی، **سند** است و نه گزارش عملیاتی.

    ``--request`` متنی است که مدیر کپی می‌کند و برای پشتیبانی پنل می‌فرستد؛ یک
    خط لاگ «نگاشت تهی شد» وسط نامه، بی‌جا است. (این خط در اجرای عادی، دانستنی و
    مفید است، پس فقط در حالت سند خاموش می‌شود.)
    """
    logger = logging.getLogger(DOC_LOGGER)
    previous = logger.level
    if enabled:
        logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        logger.setLevel(previous)
