"""پنل مدیریت حسابرسی — فقط مشاهده (تغییر ممنوع)."""

from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.audit.models import AuditAnchor, AuditRecord


@admin.register(AuditRecord)
class AuditRecordAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """سابقهٔ تغییرناپذیر عملیات — فقط خواندن."""

    list_display = ("sequence", "action", "outcome", "actor_label", "target_type", "created_at")
    list_filter = ("action", "outcome", "target_type")
    search_fields = ("actor_label", "target_id", "request_id")
    date_hierarchy = "created_at"
    ordering = ("-sequence",)
    readonly_fields = [field.name for field in AuditRecord._meta.fields]

    def has_add_permission(self, request: Any) -> bool:
        """حسابرسی فقط از طریق سرویس ثبت می‌شود."""
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        """تغییر سابقهٔ حسابرسی ممنوع است — حتی برای مدیر ارشد."""
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        """حذف سابقهٔ حسابرسی ممنوع است (سیاست عدم‌انکار)."""
        return False


@admin.register(AuditAnchor)
class AuditAnchorAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """لنگرهای زنجیره — اثبات بیرونی یکپارچگی."""

    list_display = ("last_sequence", "record_count", "anchored_at", "last_hash")
    ordering = ("-anchored_at",)
    readonly_fields = [field.name for field in AuditAnchor._meta.fields]

    def has_add_permission(self, request: Any) -> bool:
        """لنگر فقط با کار زمان‌بندی‌شده ساخته می‌شود."""
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        """لنگرها تغییرناپذیرند."""
        return False
