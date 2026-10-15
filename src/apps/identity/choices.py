"""شمارشی‌های دامنهٔ هویت."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class UserRole(models.TextChoices):
    """نقش کاربر — تعیین‌کنندهٔ مرز اختیارات.

    مرزها در سند معماری بخش ۷.۴ تعریف شده است:
        • GUEST: رزرو و لغو رزرو خود.
        • OPERATOR: پذیرش، تخلیه، عدم‌حضور، رزرو تلفنی.
        • MANAGER: سیاست‌ها، جابه‌جایی آزاد، خروجی اکسل.
        • ADMIN: مدیریت کاربران و دسترسی‌ها، حسابرسی.
    """

    GUEST = "guest", _("زائر")
    OPERATOR = "operator", _("پذیرش")
    MANAGER = "manager", _("مدیر")
    ADMIN = "admin", _("مدیر ارشد")

    @classmethod
    def staff_roles(cls) -> tuple[UserRole, ...]:
        """نقش‌هایی که به پنل کارکنان دسترسی دارند."""
        return (cls.OPERATOR, cls.MANAGER, cls.ADMIN)
