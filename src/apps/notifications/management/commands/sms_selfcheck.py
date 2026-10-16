"""دستور ``sms_selfcheck`` — «آیا پیامک واقعاً کار می‌کند؟» با یک پیام واقعی.

چرا این دستور لازم است؟
    پنل‌های ایرانی، الگوها را تنها پس از بالا آمدن دامنه تأیید می‌کنند و کدها
    یک‌جا می‌رسند. آن روز، پرسش واقعی مدیر این است: «خب، حالا کار می‌کند یا نه؟»
    پاسخ با «کدها را گذاشتم» داده نمی‌شود؛ با **یک پیام واقعی که به موبایل
    می‌رسد** داده می‌شود. این دستور همان آزمون را در سه گام انجام می‌دهد:

    ۱. **بازرسی پیکربندی** (بی‌شبکه): چه الگویی کد دارد، چه الگویی ندارد.
    ۲. **ارسال واقعی** از الگوی آزمایشی (`dry_run`) — پس هیچ کد یک‌بارمصرف
       واقعی سوزانده نمی‌شود و هیچ زائری پیام بی‌ربط نمی‌گیرد.
    ۳. **گزارش و حسابرسی**: پاسخ پنل، شناسهٔ ارسال و وضعیت ثبت‌شده.

قواعد ایمنی:
    • بدون ``--to`` هیچ پیامی فرستاده نمی‌شود؛ فقط بازرسی پیکربندی.
    • اگر الگوی آزمایشی کد نداشته باشد، دستور **هیچ پیامی نمی‌فرستد** و با کد
      خروج ۱ تمام می‌شود؛ به‌جای آن، دقیقاً می‌گوید چه چیزی کم است و چگونه
      می‌شود با یک الگوی دیگر آزمود (``--template``).
    • نتیجه در ``SmsDelivery`` و زنجیرهٔ حسابرسی ثبت می‌شود تا «آزمون شده» یک
      ادعا نباشد، یک رکورد باشد.

کاربردها:
    python manage.py sms_selfcheck                      # فقط بازرسی پیکربندی
    python manage.py sms_selfcheck --to 0912… --confirm  # ارسال реального آزمون
    python manage.py sms_selfcheck --to 0912… --template otp_login --confirm
    python manage.py sms_selfcheck --json               # خروجی ماشین‌خوان برای پایش
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.text import to_persian_digits
from apps.notifications import patterns
from apps.notifications.choices import SmsStatus, SmsTemplateKey
from apps.notifications.services import send_sms

#: الگوی پیش‌فرضِ آزمون: بی‌خطر برای زائر، گویا برای مدیر.
DEFAULT_TEST_TEMPLATE = SmsTemplateKey.DRY_RUN
OK_EXIT_CODE = 0
PROBLEM_EXIT_CODE = 1
TEST_MESSAGE = "آزمون روز راه‌اندازی — اگر این پیام را دیدید، پنل سالم است."


class Command(BaseCommand):
    """بازرسی آمادگی پیامک و انجام یک ارسال واقعی آزمون."""

    help = "وضعیت الگوهای پیامک را می‌سنجد و (با --to) یک پیام واقعی آزمایشی می‌فرستد."

    def add_arguments(self, parser: Any) -> None:
        """افزودن گزینه‌های اجرا."""
        parser.add_argument("--to", default="", help="شمارهٔ موبایل دریافت‌کنندهٔ آزمون")
        parser.add_argument(
            "--template",
            default=DEFAULT_TEST_TEMPLATE,
            help=f"قالب الگوی آزمون (پیش‌فرض: {DEFAULT_TEST_TEMPLATE})",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="تأیید صریح ارسال واقعی (بدون آن، فقط بازرسی انجام می‌شود)",
        )
        parser.add_argument("--json", action="store_true", help="خروجی ماشین‌خوان")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 - امضای جنگو
        """اجرای بازرسی و — در صورت درخواست — آزمون واقعی."""
        readiness = patterns.pattern_readiness()
        report: dict[str, Any] = {"readiness": readiness, "test": None}
        target = str(options["to"] or "").strip()
        template = str(options["template"] or DEFAULT_TEST_TEMPLATE)
        confirmed = bool(options["confirm"])

        if not target or not confirmed:
            report["test"] = self._skipped(target, confirmed)
            self._emit(report, options, readiness)
            return

        draft = patterns.all_bindings().get(template)
        if draft is None:
            raise CommandError(
                f"قالب «{template}» شناخته‌شده نیست. قالب‌های مجاز: "
                + "، ".join(sorted(patterns.all_bindings()))
            )
        # رشتهٔ خام هرگز به لایهٔ ارسال نمی‌رود: همان‌جا به کلید نوع‌دار تبدیل
        # می‌شود تا غلط‌تایپی، پیام اشتباه به موبایل زائر نبرد.
        try:
            SmsTemplateKey(template)
        except ValueError as error:
            raise CommandError(f"قالب «{template}» کلید پیامک شناخته‌شده نیست.") from error
        if not patterns.is_bound(draft):
            report["test"] = {
                "outcome": "blocked",
                "template": template,
                "reason": (
                    "این قالب کد الگو ندارد؛ پیامی فرستاده نشد. کد واقعی را در "
                    "SMS_PATTERN_CODES بگذارید (یا با --template قالب دیگری آزمون کنید)."
                ),
            }
            self._emit(report, options, readiness)
            self._fail(report)
            return

        context = self._context_for(template)
        delivery = send_sms(
            phone=target,
            template_key=SmsTemplateKey(template),
            context=context,
            dedup_key=f"selfcheck:{template}:{target}",
        )
        delivered = delivery.status in {
            SmsStatus.SENT,
            SmsStatus.SUPPRESSED,
            SmsStatus.QUEUED,
        }
        report["test"] = {
            "outcome": "sent" if delivered else "failed",
            "template": template,
            "status": str(delivery.status),
            "provider": delivery.provider,
            "provider_ref": delivery.provider_ref,
            "error": delivery.error,
            "delivery_id": str(delivery.pk),
        }
        self._emit(report, options, readiness)
        if not delivered:
            self._fail(report)

    def _context_for(self, template: str) -> dict[str, str]:  # noqa: ARG002 - امضای یکنواخت
        """زمینهٔ متغیرهای الگوی آزمون.

        یک زمینهٔ کاملِ نمونه می‌دهیم (به‌جای «فقط متغیرهای همین قالب») با یک
        دلیل: نگاشت هر الگو فقط متغیرهای خودش را برمی‌دارد، و اگر روزی قالبی
        متغیر تازه‌ای لازم داشته باشد، آزمون **مسدود می‌شود** و همین‌جا لو
        می‌رود — نه در روزی که زائر منتظر پیام است.
        """
        return self._sample_context()

    @staticmethod
    def _sample_context() -> dict[str, str]:
        """زمینهٔ کاملِ نمونه برای هر قالب — یک‌جا و بدون تکرار."""
        return {
            "code": "۱۲۳۴۵۶",
            "full_name": "آزمون سامانه",
            "unit": "واحد آزمون",
            "window": "بازهٔ آزمون",
            "date": "امروز",
            "rooz": "۱",
            "offers": "۱",
            "position": "۱",
            "url": "mahdie-shohada.ir",
            "link": "mahdie-shohada.ir",
            "message": TEST_MESSAGE,
            "ttl_minutes": "۵",
            "guests": "۱",
            "check_in": "امروز",
            "check_out": "فردا",
        }

    @staticmethod
    def _skipped(target: str, confirmed: bool) -> dict[str, str]:
        """توضیح شفاف «چرا آزمون انجام نشد»."""
        if not target:
            return {"outcome": "skipped", "reason": "شماره‌ای با --to داده نشد."}
        if not confirmed:
            return {
                "outcome": "skipped",
                "reason": "ارسال واقعی به تأیید صریح نیاز دارد (--confirm).",
            }
        return {"outcome": "skipped", "reason": "دلیل نامشخص."}

    def _emit(
        self, report: dict[str, Any], options: dict[str, Any], readiness: dict[str, Any]
    ) -> None:
        """چاپ گزارش (ماشین‌خوان یا انسانی)."""
        if options.get("json"):
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return
        style = self.style
        bound = readiness["bound"]
        unbound = readiness["unbound"]
        self.stdout.write(
            f"الگوهای آماده: {to_persian_digits(len(bound))} · "
            f"بدون کد: {to_persian_digits(len(unbound))}"
        )
        if readiness["critical_unbound"]:
            self.stdout.write(
                style.ERROR("قالب‌های حیاتی بدون کد: " + "، ".join(readiness["critical_unbound"]))
            )
        if unbound:
            self.stdout.write("بدون کد: " + "، ".join(unbound))
        test = report.get("test") or {}
        outcome = str(test.get("outcome", ""))
        if outcome == "sent":
            self.stdout.write(style.SUCCESS(f"پیام آزمون ارسال شد ({test['template']})."))
            self.stdout.write(f"وضعیت: {test['status']} · ارائه‌دهنده: {test['provider']}")
            if test.get("provider_ref"):
                self.stdout.write(f"شناسهٔ پنل: {test['provider_ref']}")
        elif outcome == "failed":
            self.stdout.write(style.ERROR(f"ارسال ناموفق: {test.get('error') or 'نامشخص'}"))
        else:
            self.stdout.write(f"آزمون ارسال نشد — {test.get('reason', '')}")

    def _fail(self, report: dict[str, Any]) -> None:
        """پایان با کد خروج خطا — «سبزِ نمایشی» ممنوع."""
        message = str((report.get("test") or {}).get("reason") or "ارسال آزمون ناموفق بود.")
        self.stderr.write(self.style.ERROR(message))
        raise SystemExit(PROBLEM_EXIT_CODE)
