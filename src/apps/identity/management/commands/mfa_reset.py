"""بازنشانی تأیید دومرحله‌ای یک کارمند (گوشی گم‌شده) — فقط از خط فرمان سرور.

چرا فرمان و نه دکمه؟ بازنشانی عامل دوم، خودِ سد امنیتی را برمی‌دارد. اگر از
رابط وب ممکن بود، یک نشست ربوده‌شدهٔ مدیر می‌توانست عامل دوم دیگران را بردارد.
دسترسی SSH به سرور، خودش لایهٔ جداگانه‌ای از اعتماد است.

    python manage.py mfa_reset --phone 09120000004 --reason "گوشی کارمند به سرقت رفت"
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.identity import mfa
from apps.identity.models import User

REASON_MIN_LENGTH = 5


class Command(BaseCommand):
    """حذف دستگاه TOTP و کدهای بازیابی کارمند + ثبت در زنجیرهٔ حسابرسی."""

    help = "بازنشانی تأیید دومرحله‌ای یک کارمند (با دلیل اجباری و حسابرسی)."

    def add_arguments(self, parser: CommandParser) -> None:
        """گزینه‌های فرمان."""
        parser.add_argument("--phone", required=True, help="شمارهٔ تلفن کارمند")
        parser.add_argument("--reason", required=True, help="دلیل بازنشانی (ثبت در حسابرسی)")

    def handle(self, *_args: Any, **options: Any) -> None:
        """اجرای بازنشانی."""
        user = User.objects.by_phone(str(options["phone"]))
        if user is None:
            raise CommandError("کاربری با این شماره پیدا نشد.")
        if not user.is_staff_member:
            raise CommandError("این حساب کارمند نیست؛ تأیید دومرحله‌ای ندارد.")
        reason = str(options["reason"]).strip()
        if len(reason) < REASON_MIN_LENGTH:
            raise CommandError("دلیل بازنشانی باید دست‌کم ۵ نویسه باشد.")
        removed = mfa.reset_device(user=user, actor_label="خط فرمان سرور", reason=reason)
        state = "removed" if removed else "nothing-to-remove"
        self.stdout.write(f"mfa_reset {user.masked_phone}: {state}")
