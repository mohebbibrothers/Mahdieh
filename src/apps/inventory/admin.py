"""پنل مدیریت موجودی — طبقات، واحدها، امکانات، مناسبت‌ها و بلوک‌ها."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.inventory.models import (
    Block,
    Floor,
    Occasion,
    Unit,
    UnitFeature,
    UnitFeatureLink,
    UnitPhoto,
)


class UnitFeatureLinkInline(admin.TabularInline):  # type: ignore[type-arg]
    """امکانات واحد — ویرایش درون‌خطی برای سرعت کارکنان."""

    model = UnitFeatureLink
    extra = 1
    autocomplete_fields = ("feature",)


class UnitPhotoInline(admin.TabularInline):  # type: ignore[type-arg]
    """تصاویر واحد."""

    model = UnitPhoto
    extra = 1


@admin.register(Floor)
class FloorAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """مدیریت طبقات."""

    list_display = ("number", "title", "sort_order", "is_active")
    ordering = ("sort_order", "number")


@admin.register(UnitFeature)
class UnitFeatureAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """مدیریت امکانات."""

    list_display = ("title", "code", "icon", "is_highlight", "is_active")
    search_fields = ("title", "code")


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """مدیریت واحدها — با نمایش زندهٔ ظرفیت و بافر نظافت."""

    list_display = (
        "code",
        "title",
        "floor",
        "unit_type",
        "capacity_adults",
        "capacity_children",
        "cleaning_buffer_minutes",
        "is_active",
    )
    list_filter = ("unit_type", "is_active", "floor")
    search_fields = ("code", "title")
    inlines = (UnitFeatureLinkInline, UnitPhotoInline)
    fieldsets = (
        (_("شناسه"), {"fields": ("id", "code", "title", "floor", "unit_type", "sort_order")}),
        (
            _("ظرفیت"),
            {"fields": ("capacity_adults", "capacity_children", "cleaning_buffer_minutes")},
        ),
        (_("وضعیت"), {"fields": ("is_active", "deactivated_reason", "deactivated_at", "notes")}),
    )
    readonly_fields = ("id",)

    def get_readonly_fields(self, request: Any, obj: Any = None) -> tuple[str, ...]:
        """کلید اصلی همیشه فقط‌خواندنی است."""
        del request, obj
        return ("id",)


@admin.register(Occasion)
class OccasionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """مدیریت مناسبت‌ها و ایام پیک."""

    list_display = ("title", "date_jalali", "date_gregorian", "kind", "demand_level")
    list_filter = ("kind", "demand_level", "is_fixed_every_year")
    search_fields = ("title", "date_jalali")
    date_hierarchy = "date_gregorian"


@admin.register(Block)
class BlockAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """مدیریت بلوک‌های نگهداری — دلیل همیشه نمایش داده می‌شود."""

    list_display = ("unit", "kind", "starts_at", "ends_at", "reason", "created_by", "created_at")
    list_filter = ("kind", "is_active")
    search_fields = ("reason", "unit__code")
    readonly_fields = ("id", "created_at", "updated_at")
