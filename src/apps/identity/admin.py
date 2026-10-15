"""پنل مدیریت کاربران — با پوشاندن دادهٔ حساس."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from apps.identity.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):  # type: ignore[type-arg]
    """مدیریت کاربران با نمایش تلفن پوشانده و بدون فیلد ایمیل اجباری."""

    ordering = ("-date_joined",)
    list_display = (
        "masked_phone_display",
        "full_name",
        "role",
        "is_phone_verified",
        "is_active",
        "date_joined",
    )
    list_filter = ("role", "is_active", "is_phone_verified", "is_staff")
    search_fields = ("phone", "first_name", "last_name", "city")
    readonly_fields = ("id", "date_joined", "last_login", "last_login_ip", "masked_phone_display")

    fieldsets = (
        (_("هویت"), {"fields": ("id", "phone", "masked_phone_display", "email")}),
        (_("پروفایل"), {"fields": ("first_name", "last_name", "province", "city")}),
        (
            _("نقش و دسترسی"),
            {"fields": ("role", "is_active", "is_staff", "is_superuser", "is_phone_verified")},
        ),
        (
            _("وضعیت"),
            {"fields": ("profile_completed_at", "last_login", "last_login_ip", "date_joined")},
        ),
    )
    add_fieldsets = (
        (
            _("ساخت کاربر"),
            {"classes": ("wide",), "fields": ("phone", "role", "password1", "password2")},
        ),
    )

    @admin.display(description=_("تلفن"), ordering="phone")
    def masked_phone_display(self, obj: User) -> str:
        """نمایش پوشاندهٔ تلفن — هرگز کامل نشان داده نمی‌شود."""
        return obj.masked_phone

    @admin.display(description=_("نام"), ordering="last_name")
    def full_name(self, obj: User) -> str:
        """نام نمایشی کاربر."""
        return obj.display_name

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        """حذف فیزیکی کاربر ممنوع — حذف نرم با حفظ سابقهٔ رزروها."""
        return False
