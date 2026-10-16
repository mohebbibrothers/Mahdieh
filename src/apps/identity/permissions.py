"""مجوزهای دامنه‌ای — «چه کسی چه کاری می‌تواند» (سند معماری بخش ۷.۴).

این مجوزها در لایهٔ HTTP اجرا می‌شوند، ولی منبع حقیقت مرزها همان لایهٔ دامنه
است: هر سرویس نیز پیش از عمل، همین شرایط را دوباره بررسی می‌کند (دفاع عمقی).
"""

from __future__ import annotations

from typing import Any, cast

from rest_framework.permissions import BasePermission

from apps.core.errors import AuthenticationRequiredError
from apps.identity.choices import UserRole
from apps.identity.models import User


class IsProfileComplete(BasePermission):
    """کاربر باید پروفایل کامل داشته باشد تا رزرو برایش باز شود (ثابت I5)."""

    message = "برای رزرو، ابتدا پروفایل خود را کامل کنید (نام، نام خانوادگی، کد ملی، شهر)."

    def has_permission(self, request: Any, view: Any) -> bool:
        """بررسی ورود + تکمیل پروفایل."""
        del view
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and getattr(user, "is_profile_complete", False))


class IsStaffRole(BasePermission):
    """دسترسی کارکنان پذیرش/مدیریت."""

    message = "این عملیات فقط برای کارکنان زائرسرا مجاز است."

    def has_permission(self, request: Any, view: Any) -> bool:
        """بررسی نقش کارکنان."""
        del view
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and getattr(user, "is_staff_member", False))


class IsManagerRole(BasePermission):
    """دسترسی مدیر به سیاست‌ها، خروجی‌ها و عملیات سنگین."""

    message = "این عملیات فقط برای مدیر مجاز است."

    _ALLOWED = (UserRole.MANAGER, UserRole.ADMIN)

    def has_permission(self, request: Any, view: Any) -> bool:
        """بررسی نقش مدیر."""
        del view
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        return bool(getattr(user, "is_superuser", False) or user.role in self._ALLOWED)


def authenticated_user(request: Any) -> User:
    """کاربر احرازشدهٔ درخواست — با تضمین نوع برای تایپ‌چک سخت‌گیر.

    مجوزهای سطح نما (``IsAuthenticated`` و هم‌خانواده) پیش‌تر دسترسی را کنترل
    کرده‌اند؛ این تابع همان تضمین را برای تحلیل ایستای تایپ‌ها صریح می‌کند تا
    می‌توانستیم بدون ``cast`` پراکنده در همهٔ نماها کار کنیم.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        raise AuthenticationRequiredError()
    return cast(User, user)
