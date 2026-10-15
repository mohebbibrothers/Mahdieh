"""پنل مدیریت جنگو — نمایش و پایش Outbox، هم‌توانی و متریک‌ها."""

from __future__ import annotations

from typing import Any, ClassVar

from django.contrib import admin

from apps.core.idempotency import IdempotencyKey
from apps.core.outbox import OutboxEvent


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """پایش رخدادهای دامنه — عمق صف و رخدادهای راکد."""

    list_display = (
        "event_type",
        "aggregate_type",
        "status",
        "attempts",
        "occurred_at",
        "published_at",
    )
    list_filter = ("status", "aggregate_type", "event_type")
    search_fields = ("event_type", "aggregate_id")
    readonly_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "event_type",
        "aggregate_type",
        "aggregate_id",
        "payload",
        "headers",
        "occurred_at",
        "published_at",
        "attempts",
        "last_error",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "occurred_at"
    ordering = ("-occurred_at",)

    def has_add_permission(self, request: Any) -> bool:
        """رخداد دامنه فقط از کد ساخته می‌شود، نه دستی."""
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        """تغییر دستی رخداد ممنوع — فقط مشاهده برای عیب‌یابی."""
        return False


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """بازرسی کلیدهای هم‌توانی — برای پیگیری درخواست‌های تکراری."""

    list_display = ("key", "scope", "actor_ref", "response_status", "expires_at", "created_at")
    list_filter = ("scope",)
    search_fields = ("key", "actor_ref")
    readonly_fields = [field.name for field in IdempotencyKey._meta.fields]
    date_hierarchy = "created_at"

    def has_add_permission(self, request: Any) -> bool:
        """ساخت دستی کلید هم‌توانی بی‌معناست."""
        del request  # بخشی از امضای جنگو است
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        """پاک‌سازی فقط از طریق کار زمان‌بندی‌شده."""
        del request, obj
        return False
