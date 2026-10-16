"""سرویس‌های کمکی لایهٔ نمایش — آماده‌سازی دادهٔ نمایشی و ابزارهای صفحه.

چرا اینجا و نه در ``views``؟ قاعدهٔ لایه‌بندی: نما حق ندارد روی مدل بنویسد.
هر نوشتن — حتی «ساخت کاربر نمایشی» — از سرویس می‌گذرد تا قاعده یک‌دست بماند.
"""

from __future__ import annotations

from typing import Final

from django.conf import settings

from apps.identity.choices import Province, UserRole
from apps.identity.models import User
from apps.identity.validators import normalize_phone

DEMO_PHONE_GUEST: Final[str] = "09120000000"
DEMO_PHONE_STAFF: Final[str] = "09120000009"
DEMO_ROLES: Final[dict[str, tuple[str, str, str]]] = {
    "guest": (DEMO_PHONE_GUEST, UserRole.GUEST, "زائر نمایشی"),
    "staff": (DEMO_PHONE_STAFF, UserRole.OPERATOR, "کارمند نمایشی"),
}

# کدهای ملی معتبر (رقم کنترلی محاسبه‌شده) برای حساب‌های نمایشی — ثابت و شفاف.
DEMO_NATIONAL_IDS: Final[dict[str, str]] = {
    "guest": "0012345679",
    "staff": "0012345687",
}


def demo_mode_enabled() -> bool:
    """آیا حالت نمایش روشن است؟ (توسعه + پرچم صریح محیط)."""
    return bool(getattr(settings, "DEBUG", False) and getattr(settings, "DEMO_MODE", False))


def ensure_demo_user(role: str) -> User:
    """ساخت (یا بازیابی) کاربر نمایشی با پروفایل کامل.

    فقط در حالت نمایش فراخوانی می‌شود؛ در تولید هیچ مسیری به این تابع نمی‌رسد.
    """
    phone, user_role, display = DEMO_ROLES[role]
    normalized = normalize_phone(phone)
    user = User.objects.by_phone(normalized)
    if user is not None:
        return user
    user = User.objects.create_user(phone=normalized, role=user_role)
    first_name, _, last_name = display.partition(" ")
    user.complete_profile(
        first_name=first_name or "کاربر",
        last_name=last_name or "نمایشی",
        province=Province.KHORASAN_RAZAVI,
        city="مشهد",
        national_id=_demo_national_id(role),
    )
    if user_role != UserRole.GUEST:
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    return user


def _demo_national_id(role: str) -> str:
    """کد ملی معتبر و ثابت حساب نمایشی (رقم کنترلی محاسبه‌شده و آزموده‌شده)."""
    return DEMO_NATIONAL_IDS[role]
