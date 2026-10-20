"""دستور ``production_check`` — دروازهٔ انتشار با «هشدارهای عمدی».

مسئله‌ای که این دستور حل می‌کند
    ``manage.py check --deploy --fail-level WARNING`` برای انتشار تولید درست است،
    ولی این پروژه چند هشدار **عمدی** دارد که *نباید* انتشار را ببندند:

      ۱. ``mahdieh.W001`` — نشانی/تلفن پذیرش هنوز پر نشده (شمارهٔ رسمی پس از
         بالا آمدن دامنه از کارفرما می‌رسد).
      ۲. ``mahdieh.W010/W011`` — پیامک روشن است ولی پنل هنوز کد الگو نداده
         (پنل، الگو را پیش از بالا آمدن دامنه تأیید نمی‌کند).

    اگر این‌ها بلوکه‌کننده باشند، سایت هرگز بالا نمی‌آید تا *خودش* را فعال کند —
    یعنی «مواظبت» به «قفل‌شدگی» تبدیل می‌شود. راه‌حل: یک دروازه با دو سبد:

      • **بلوکه**: هر خطای واقعی و هر هشداری که در فهرست عمدی‌ها نیست
        (شامل هشدارهای امنیتی خود جنگو مثل ``security.W018``).
      • **غیربلوکه ولی دیده‌شده**: هشدارهای عمدی بالا؛ چاپ می‌شوند، شمرده
        می‌شوند و در خروجی JSON هم می‌آیند — پنهان نمی‌شوند.

    ``--strict`` همهٔ آن‌ها را بلوکه‌کننده می‌کند؛ همان حالتی که برای **روز
    انتشار واقعی** و برای CI می‌خواهیم: «همه‌چیز سبز، بی‌استثنا».

نکتهٔ مهم: این دستور خودش بررسی‌ها را اجرا می‌کند، پس
``requires_system_checks = []`` دارد؛ وگرنه جنگو پیش از رسیدن به آن، پروژه را با
``SystemCheckError`` متوقف می‌کرد و در حالت «پیامک روشن ولی الگو نرسیده» هیچ
دستوری — حتی ``migrate`` — اجرا نمی‌شد.

خروجی: کد ۱ اگر سبد بلوکه خالی نباشد. ``--json`` برای پایش/CI.

    python manage.py production_check
    python manage.py production_check --strict --json
"""

from __future__ import annotations

import json
from typing import Any

from django.conf import settings
from django.core import checks
from django.core.checks import WARNING as WARNING_LEVEL, CheckMessage, Error
from django.core.management.base import BaseCommand

#: شناسهٔ هشدارهای عمدی — هر بار که یکی اضافه می‌شود، **همین‌جا و با دلیل** ثبت شود.
INTENTIONAL_WARNINGS: dict[str, str] = {
    "mahdieh.W001": "نشانی/تلفن پذیرش؛ شمارهٔ رسمی پس از بالا آمدن دامنه می‌رسد.",
    "mahdieh.W010": "پیامک روشن است ولی الگوی حیاتیِ پنل هنوز کد ندارد.",
    "mahdieh.W011": "پیامک روشن است ولی برخی الگوهای زائر کد ندارند.",
    # فرانت چرخهٔ استقرار جدا دارد (front_deploy.sh با بازگشت خودکار)؛ بستهٔ خراب فرانت نباید
    # استقرار بک‌اند را قفل کند. جنگو در این حالت صفحه‌های قدیمی را سرو می‌کند.
    "mahdieh.W020": "بستهٔ فعال فرانت نامعتبر است؛ جنگو صفحه‌های قدیمی را سرو می‌کند.",
}


class Command(BaseCommand):
    """دروازهٔ انتشار: خطاها بلوکه می‌کنند، هشدارهای عمدی فقط دیده می‌شوند."""

    help = "بررسی جنگو برای انتشار تولید، با تفکیک «خطای بلوکه» از «هشدارِ عمدی»."

    # خودمان بررسی‌ها را اجرا می‌کنیم؛ جنگو نباید پیش از این دستور پروژه را
    # با SystemCheckError ببندد (وگرنه در حالت پیامکِ بی‌الگو، migrate هم اجرا نمی‌شود).
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: Any) -> None:
        """گزینه‌های اجرا."""
        parser.add_argument("--json", action="store_true", help="خروجی ماشین‌خوان")
        parser.add_argument(
            "--strict",
            action="store_true",
            help="هشدارهای عمدی هم بلوکه‌کننده شمرده شوند (CI و روز انتشار)",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 - امضای جنگو
        """اجرای همهٔ بررسی‌ها (شامل بررسی‌های استقرار) و تفکیک دو سبد."""
        strict = bool(options.get("strict"))
        blocking: list[CheckMessage] = []
        mode = self._mode()
        if mode == "mismatch":
            blocking.append(self._mismatch_error())
        deployment = mode == "production"
        if mode != "local":
            messages = checks.run_checks(include_deployment_checks=deployment)
            blocking.extend(self._split(messages, strict=strict)[0])
            intentional = self._split(messages, strict=strict)[1]
        else:
            messages = checks.run_checks(include_deployment_checks=False)
            blocking = self._split(messages, strict=strict)[0]
            intentional = self._split(messages, strict=strict)[1]
        report = {
            "environment": str(getattr(settings, "ENVIRONMENT", "") or "—"),
            "settings_module": str(getattr(settings, "SETTINGS_MODULE", "") or "—"),
            "mode": mode,
            "strict": strict,
            "blocking": [self._describe(item) for item in blocking],
            "intentional": [self._describe(item) for item in intentional],
            "ok": not blocking,
        }
        if options.get("json"):
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            self._print_human(report)
        if blocking:
            raise SystemExit(1)

    @staticmethod
    def _mode() -> str:
        """حالت اجرا: ``production`` · ``local`` · ``mismatch``.

        گاردِ «نام محیط» از یک تجربهٔ تلخ واقعی می‌آید: در فایل محیط تولید، نام
        متغیر را ``ENVIRONMENT`` نوشته بودند ولی تنظیمات ``MAHDIEH_ENVIRONMENT``
        می‌خواند؛ سامانه فکر می‌کرد در «توسعه» است و هشدارهای مخصوص تولید خاموش
        ماندند. این گارد همان یک اشتباه را در لحظهٔ استقرار لو می‌دهد:

          • هم ماژول تنظیمات تولیدی است و هم محیط ``production`` ⇒ انتشار واقعی.
          • هیچ‌کدام تولیدی نیستند ⇒ اجرای محلی/آزمون: بررسی‌های استقرار کنار
            گذاشته می‌شوند (بی‌ربط‌اند، نه پنهان).
          • **فقط یکی** تولیدی است ⇒ ناسازگاری: بلوکه‌کننده.
        """
        module = str(getattr(settings, "SETTINGS_MODULE", "") or "")
        declared = str(getattr(settings, "ENVIRONMENT", "") or "")
        module_production = "production" in module
        env_production = declared == "production"
        if module_production != env_production:
            return "mismatch"
        return "production" if module_production else "local"

    @staticmethod
    def _mismatch_error() -> CheckMessage:
        """خطای «ناسازگاری نام محیط» با راهنمای رفع."""
        module = str(getattr(settings, "SETTINGS_MODULE", "") or "—")
        declared = str(getattr(settings, "ENVIRONMENT", "") or "—")
        return Error(
            f"ناسازگاری محیط: ماژول تنظیمات «{module}» ولی MAHDIEH_ENVIRONMENT=«{declared}».",
            hint=(
                "روی سرور تولید MAHDIEH_ENVIRONMENT=production را تنظیم کنید "
                "(نمونه: deploy/env/mahdieh.env.example)؛ در اجرای محلی، ماژول "
                "تنظیمات را به development برگردانید."
            ),
            id="mahdieh.E004",
        )

    @staticmethod
    def _split(
        messages: list[CheckMessage], *, strict: bool
    ) -> tuple[list[CheckMessage], list[CheckMessage]]:
        """تفکیک پیام‌ها: سبد بلوکه یا سبد «هشدار عمدی».

        در حالت پیش‌فرض، شناسه‌های فهرست عمدی حتی اگر سطحشان «خطا» باشد انتشار
        را نمی‌بندند (سایت باید بالا بیاید تا بتواند خودش را فعال کند). با
        ``--strict`` همان‌ها هم بلوکه‌کننده‌اند.
        """
        blocking: list[CheckMessage] = []
        intentional: list[CheckMessage] = []
        for message in messages:
            identifier = str(getattr(message, "id", "") or "")
            if identifier in INTENTIONAL_WARNINGS:
                (blocking if strict else intentional).append(message)
                continue
            if message.is_serious() or message.level >= WARNING_LEVEL:
                blocking.append(message)
        return blocking, intentional

    @staticmethod
    def _describe(message: CheckMessage) -> dict[str, str]:
        """نمایش ساخت‌یافتهٔ یک پیام بررسی."""
        return {
            "id": str(getattr(message, "id", "") or "—"),
            "level": "error" if message.is_serious() else "warning",
            "message": str(message.msg),
            "hint": str(getattr(message, "hint", "") or ""),
        }

    def _print_human(self, report: dict[str, Any]) -> None:
        """گزارش خوانا برای انسان (فارسی، با شمارش)."""
        style = self.style
        self.stdout.write(
            f"محیط: {report['environment']} · ماژول: {report['settings_module']}"
            f" · حالت: {report['mode']} · سخت‌گیرانه: {report['strict']}"
        )
        for item in report["blocking"]:
            self.stdout.write(style.ERROR(f"✗ [{item['id']}] {item['message']}"))
            if item["hint"]:
                self.stdout.write(f"   راهنمای رفع: {item['hint']}")
        if report["intentional"]:
            self.stdout.write(
                style.WARNING(f"هشدارهای عمدی و غیربلوکه: {len(report['intentional'])}")
            )
            for item in report["intentional"]:
                reason = INTENTIONAL_WARNINGS.get(item["id"], "")
                self.stdout.write(f"  ! [{item['id']}] {item['message']}")
                if reason:
                    self.stdout.write(f"     چرا بلوکه نمی‌کند: {reason}")
        if report["ok"]:
            self.stdout.write(style.SUCCESS("✅ دروازهٔ انتشار: بی‌ایراد بلوکه‌کننده"))
        else:
            self.stdout.write(
                style.ERROR(f"✗ دروازهٔ انتشار: {len(report['blocking'])} ایراد بلوکه‌کننده")
            )
