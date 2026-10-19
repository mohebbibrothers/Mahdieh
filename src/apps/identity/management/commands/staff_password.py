"""تعیین گذرواژهٔ کارمند از خط فرمان سرور — جایگزین امن ``changepassword`` جنگو.

چرا فرمان جدا؟ ``changepassword`` جنگو کاربر را با متن خام جست‌وجو می‌کند
(``09…`` پیدا نمی‌شود، چون تلفن به‌صورت ``+98…`` ذخیره است) و در نصب ما
``django.contrib.auth`` پیش از ``apps.identity`` است، پس هم‌نام‌کردن آن را
جایگزین نمی‌کند. این فرمان:

* تلفن را در هر قالبی می‌پذیرد (``09…``، ``+98…``، ارقام فارسی، فاصله)؛
* فقط برای حساب کارمند/مدیر است (زائر گذرواژه ندارد؛ ورودش با کد پیامکی است)؛
* قوانین ``AUTH_PASSWORD_VALIDATORS`` را **بی امکان دور زدن** اعمال می‌کند؛
* با تغییر هش گذرواژه، همهٔ نشست‌های باز آن حساب خودبه‌خود باطل می‌شوند
  (``SessionAuthenticationMiddleware`` جنگو هش را در نشست می‌سنجد)؛
* رویداد را در زنجیرهٔ حسابرسی ثبت می‌کند (بی ذخیرهٔ خود گذرواژه).

    python manage.py staff_password 09121234567
"""

from __future__ import annotations

import getpass
from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.identity.models import User

MAX_TRIES = 3


class Command(BaseCommand):
    """تعیین گذرواژهٔ تازه برای کارمند، با اعتبارسنجی اجباری و حسابرسی."""

    help = "تعیین گذرواژهٔ کارمند/مدیر با شمارهٔ تلفن (هر قالب) — بی امکان دور زدن قوانین."

    def add_arguments(self, parser: CommandParser) -> None:
        """گزینه‌های فرمان."""
        parser.add_argument("phone", help="شمارهٔ تلفن کارمند، مثلاً 09121234567")

    def handle(self, *_args: Any, **options: Any) -> None:
        """پرسیدن گذرواژه (تا سه بار) و ذخیرهٔ آن."""
        user = User.objects.by_phone(str(options["phone"]))
        if user is None:
            raise CommandError("کاربری با این شماره پیدا نشد.")
        if not user.is_staff_member:
            raise CommandError("این حساب کارمند نیست؛ ورود زائر با کد پیامکی است، نه گذرواژه.")
        self.stdout.write(f"تعیین گذرواژهٔ تازه برای {user.masked_phone}")
        for _attempt in range(MAX_TRIES):
            password = getpass.getpass("گذرواژهٔ تازه: ")
            again = getpass.getpass("تکرار گذرواژه: ")
            if password != again:
                self.stderr.write("دو گذرواژه یکسان نیستند.")
                continue
            try:
                validate_password(password, user)
            except ValidationError as exc:
                self.stderr.write("\n".join(exc.messages))
                continue
            self._save(user, password)
            self.stdout.write(
                self.style.SUCCESS("گذرواژه تغییر کرد؛ همهٔ نشست‌های باز این حساب بسته شدند.")
            )
            return
        msg = f"پس از {MAX_TRIES} تلاش، گذرواژه تغییر نکرد."
        raise CommandError(msg)

    @staticmethod
    def _save(user: User, password: str) -> None:
        with transaction.atomic():
            user.set_password(password)
            user.save(update_fields=["password"])
            audit_record(
                AuditEntry(
                    action=AuditAction.PASSWORD_SET,
                    actor_label="خط فرمان سرور",
                    target_type="user",
                    target_id=str(user.pk),
                )
            )
