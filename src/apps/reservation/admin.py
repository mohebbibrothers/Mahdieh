"""پنل مدیریت رزرو — ابزار کارکنان با تاریخ‌نگاری کامل و بدون حذف سابقه."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Policy, PolicyRevision, Reservation, ReservationEvent


class ReservationEventInline(admin.TabularInline):  # type: ignore[type-arg]
    """رخدادهای رزرو — فقط خواندنی (سابقه هرگز ویرایش نمی‌شود)."""

    model = ReservationEvent
    extra = 0
    can_delete = False
    readonly_fields = (
        "created_at",
        "kind",
        "from_status",
        "to_status",
        "actor_label",
        "reason",
        "metadata",
    )

    def has_add_permission(self, request: Any, obj: Any = None) -> bool:
        """افزودن دستی رخداد ممنوع — رخداد فقط از سرویس‌ها می‌آید."""
        del request, obj
        return False


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """مدیریت رزروها با تمرکز روی وضعیت، سانس و مهمان."""

    list_display = (
        "code",
        "unit",
        "guest_display",
        "status_badge",
        "starts_at",
        "guests_count",
        "cells_count",
        "source",
        "created_at",
    )
    list_filter = ("status", "source", "unit__floor", "unit")
    search_fields = ("code", "guest__phone", "guest__first_name", "guest__last_name")
    date_hierarchy = "created_at"
    inlines = (ReservationEventInline,)
    readonly_fields = (
        "id",
        "code",
        "created_at",
        "updated_at",
        "version",
        "superseded_by",
        "cancelled_at",
        "checked_in_at",
        "checked_out_at",
        "no_show_at",
    )
    fieldsets = (
        (_("شناسه"), {"fields": ("id", "code", "status", "source", "created_by")}),
        (
            _("سانس"),
            {
                "fields": (
                    "unit",
                    ("starts_at", "ends_at"),
                    ("block_starts_at", "block_ends_at"),
                    "cells_count",
                    "guests_count",
                )
            },
        ),
        (_("مهمان"), {"fields": ("guest", "guest_note", "expected_arrival", "vehicle_plate")}),
        (_("کارکنان"), {"fields": ("staff_note", "version", "superseded_by")}),
        (
            _("لغو و پذیرش"),
            {
                "fields": (
                    "cancelled_at",
                    "cancelled_by",
                    "cancel_reason_kind",
                    "cancel_reason",
                    "is_late_cancel",
                    "checked_in_at",
                    "checked_out_at",
                    "no_show_at",
                )
            },
        ),
        (_("زمان‌ها"), {"fields": ("created_at", "updated_at")}),
    )

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        """حذف رزرو ممنوع است؛ لغو، عمل درست است (نگه‌داری سابقه)."""
        del request, obj
        return False

    @admin.display(description=_("مهمان"))
    def guest_display(self, obj: Reservation) -> str:
        """نام و شمارهٔ پوشاندهٔ مهمان."""
        return f"{obj.guest.display_name} · {obj.guest.masked_phone}"

    @admin.display(description=_("وضعیت"))
    def status_badge(self, obj: Reservation) -> str:
        """نمایش رنگی وضعیت برای اسکن سریع توسط پذیرش."""
        palette: dict[str, str] = {
            ReservationStatus.CONFIRMED: "#0a7d33",
            ReservationStatus.CHECKED_IN: "#0b5ed7",
            ReservationStatus.HOLD: "#8a6d00",
            ReservationStatus.COMPLETED: "#555",
            ReservationStatus.CANCELLED_BY_GUEST: "#a30000",
            ReservationStatus.CANCELLED_BY_STAFF: "#a30000",
            ReservationStatus.NO_SHOW: "#8a4b00",
            ReservationStatus.EXPIRED: "#666",
            ReservationStatus.SUPERSEDED: "#666",
        }
        color = palette.get(obj.status, "#444")
        return format_html('<b style="color:{}">{}</b>', color, obj.get_status_display())


@admin.register(Policy)
class PolicyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """سیاست‌های سیستم — ویرایش از پنل، ثبت نسخه از سرویس."""

    list_display = ("key", "value", "version", "effective_from", "changed_by")
    search_fields = ("key",)
    readonly_fields = ("id", "created_at", "updated_at", "version")
    ordering = ("key",)


@admin.register(PolicyRevision)
class PolicyRevisionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """تاریخ‌نگاری تغییر سیاست‌ها — فقط خواندنی."""

    list_display = ("policy", "version", "value", "changed_by", "created_at")
    list_filter = ("policy",)
    readonly_fields = (
        "id",
        "policy",
        "version",
        "value",
        "changed_by",
        "change_reason",
        "created_at",
    )

    def has_add_permission(self, request: Any) -> bool:
        """نسخهٔ سیاست فقط توسط سرویس ساخته می‌شود."""
        del request
        return False
