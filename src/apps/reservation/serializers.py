"""سریال‌ایزرهای رزرو — قرارداد ورودی/خروجی و سند OpenAPI.

قاعدهٔ حریم خصوصی: نمای «عمومی» رزرو هرگز نام یا شمارهٔ مهمان را نشان نمی‌دهد؛
نمای کارکنان (``StaffReservationSerializer``) جزئیات عملیاتی را اضافه می‌کند.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.core.serializers import PageMetaSerializer
from apps.reservation.models import Reservation


class QuoteRequestSerializer(serializers.Serializer[Any]):
    """ورودی پیش‌بینی رزرو (سبد رزرو)."""

    unit_code = serializers.CharField(max_length=8)
    start = serializers.DateTimeField(help_text="آغاز اقامت (ISO 8601)")
    end = serializers.DateTimeField(help_text="پایان اقامت (ISO 8601)")
    guests_count = serializers.IntegerField(min_value=1, max_value=60)


class ReservationCreateSerializer(QuoteRequestSerializer):
    """ورودی ثبت رزرو توسط زائر."""

    guest_note = serializers.CharField(max_length=300, required=False, allow_blank=True)
    expected_arrival = serializers.TimeField(required=False, allow_null=True)
    vehicle_plate = serializers.CharField(max_length=20, required=False, allow_blank=True)


class StaffReservationCreateSerializer(ReservationCreateSerializer):
    """ورودی ثبت رزرو تلفنی/حضوری توسط کارکنان."""

    phone = serializers.CharField(
        max_length=20, help_text="شمارهٔ تلفن مهمان؛ اگر حساب نداشته باشد ساخته می‌شود."
    )
    first_name = serializers.CharField(max_length=50, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=50, required=False, allow_blank=True)
    province = serializers.CharField(max_length=40, required=False, allow_blank=True)
    city = serializers.CharField(max_length=60, required=False, allow_blank=True)
    staff_note = serializers.CharField(max_length=300, required=False, allow_blank=True)


class CancelSerializer(serializers.Serializer[Any]):
    """ورودی لغو رزرو."""

    reason_kind = serializers.CharField(max_length=16, required=False, default="guest_request")
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True)


class MoveSerializer(serializers.Serializer[Any]):
    """ورودی جابه‌جایی رزرو توسط کارکنان."""

    unit_code = serializers.CharField(max_length=8)
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    reason = serializers.CharField(max_length=300)
    expected_version = serializers.IntegerField(required=False, allow_null=True)


class NoteSerializer(serializers.Serializer[Any]):
    """ورودی ثبت یادداشت داخلی."""

    staff_note = serializers.CharField(max_length=300)


class ReservationSerializer(serializers.ModelSerializer[Reservation]):
    """نمای عمومی رزرو — بدون هیچ دادهٔ هویتی مهمان."""

    unit_code = serializers.CharField(source="unit.code", read_only=True)
    unit_title = serializers.CharField(source="unit.title", read_only=True)
    floor_number = serializers.IntegerField(source="unit.floor.number", read_only=True)
    # ``source`` تکراری ممنوع است (DRF اَسِرت می‌کند)؛ متد مدل خودکار فراخوانی می‌شود.
    status_label = serializers.CharField(read_only=True)
    duration_label = serializers.CharField(read_only=True)
    date_jalali = serializers.CharField(source="stay_date_jalali", read_only=True)
    weekday = serializers.CharField(source="start_weekday", read_only=True)
    is_active = serializers.BooleanField(source="is_active_reservation", read_only=True)
    cells_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Reservation
        fields = (
            "id",
            "code",
            "unit_code",
            "unit_title",
            "floor_number",
            "status",
            "status_label",
            "is_active",
            "start",
            "end",
            "date_jalali",
            "weekday",
            "cells_count",
            "duration_label",
            "guests_count",
            "guest_note",
            "created_at",
        )

    start = serializers.SerializerMethodField()
    end = serializers.SerializerMethodField()

    def get_start(self, obj: Reservation) -> str:
        """آغاز اقامت در قالب ISO 8601."""
        return obj.starts_at.isoformat()

    def get_end(self, obj: Reservation) -> str:
        """پایان اقامت در قالب ISO 8601."""
        return obj.ends_at.isoformat()


class StaffReservationSerializer(ReservationSerializer):
    """نمای کارکنان — با جزئیات عملیاتی و هویت مهمان (پوشانده)."""

    guest_name = serializers.CharField(source="guest.display_name", read_only=True)
    guest_phone_masked = serializers.CharField(source="guest.masked_phone", read_only=True)
    superseded_by_code = serializers.CharField(
        source="superseded_by.code", read_only=True, default=None
    )

    class Meta(ReservationSerializer.Meta):
        fields: tuple[str, ...] = (  # type: ignore[assignment]
            *ReservationSerializer.Meta.fields,
            "guest_name",
            "guest_phone_masked",
            "staff_note",
            "expected_arrival",
            "vehicle_plate",
            "source",
            "version",
            "superseded_by_code",
            "cancelled_at",
            "cancel_reason_kind",
            "cancel_reason",
            "checked_in_at",
            "checked_out_at",
            "no_show_at",
        )


class ReservationListSerializer(serializers.Serializer[Any]):
    """پوشش فهرست رزروهای من: ``{"results": [...], "meta": {...}}``.

    ``meta.total`` برای فهرست‌های کاربرمحور فرستاده می‌شود تا فرانت بتواند
    «۳ رزرو فعال» را بدون صفحه‌گردی نشان دهد.
    """

    results = ReservationSerializer(many=True)
    meta = PageMetaSerializer()


class StaffReservationListSerializer(serializers.Serializer[Any]):
    """پوشش میز کارکنان — با صفحهٔ بزرگ‌تر و بدون شمارش کل (کوئری گران)."""

    results = StaffReservationSerializer(many=True)
    meta = PageMetaSerializer()


class CalendarCellSerializer(serializers.Serializer[Any]):
    """وضعیت یک نیم‌روز روی یک واحد."""

    index = serializers.IntegerField()
    kind = serializers.CharField()
    # نام «label» با ویژگی ``Field.label`` در استاب‌های DRF هم‌نام است؛ عمداً
    # حفظ شده تا قرارداد JSON تقویم تغییر نکند و فقط نوع صریح شده است.
    label = serializers.CharField()  # type: ignore[assignment]
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    status = serializers.CharField()
    detail = serializers.CharField(allow_blank=True)


class CalendarUnitSerializer(serializers.Serializer[Any]):
    """وضعیت یک واحد در یک روز."""

    code = serializers.CharField()
    title = serializers.CharField()
    floor_number = serializers.IntegerField()
    unit_type = serializers.CharField()
    capacity_total = serializers.IntegerField()
    cells = CalendarCellSerializer(many=True)


class CalendarDaySerializer(serializers.Serializer[Any]):
    """یک شبانه‌روز از تقویم موجودی."""

    day = serializers.DateField()
    date_jalali = serializers.CharField()
    weekday = serializers.CharField()
    is_weekend = serializers.BooleanField()
    demand_level = serializers.CharField()
    occupancy_rate = serializers.FloatField()
    free_cells = serializers.IntegerField()
    total_cells = serializers.IntegerField()
    units = CalendarUnitSerializer(many=True)


class CalendarSerializer(serializers.Serializer[Any]):
    """پاسخ تقویم — روزها + فرادادهٔ سیاست."""

    days = CalendarDaySerializer(many=True)
    max_cells = serializers.IntegerField()
    min_cells = serializers.IntegerField()
    horizon_days = serializers.IntegerField()
    lattice = serializers.DictField(child=serializers.IntegerField())


def calendar_payload(days: list[Any], *, policy_values: dict[str, int]) -> dict[str, Any]:
    """ساخت بدنهٔ تقویم از ماتریس موجودی (کمکی برای نما)."""
    return {
        "days": [
            {
                "day": day.day,
                "date_jalali": day.date_jalali,
                "weekday": day.weekday,
                "is_weekend": day.is_weekend,
                "demand_level": day.demand_level,
                "occupancy_rate": day.occupancy_rate,
                "free_cells": day.free_cells,
                "total_cells": day.total_cells,
                "units": [
                    {
                        "code": unit.code,
                        "title": unit.title,
                        "floor_number": unit.floor_number,
                        "unit_type": unit.unit_type,
                        "capacity_total": unit.capacity_total,
                        "cells": [
                            {
                                "index": cell.index,
                                "kind": cell.kind,
                                "label": cell.label,
                                "start": cell.start,
                                "end": cell.end,
                                "status": cell.status,
                                "detail": cell.detail,
                            }
                            for cell in unit.cells
                        ],
                    }
                    for unit in day.units
                ],
            }
            for day in days
        ],
        "max_cells": policy_values["max_cells"],
        "min_cells": policy_values["min_cells"],
        "horizon_days": policy_values["horizon_days"],
        "lattice": {
            "cell_hours": policy_values["cell_hours"],
            "grid_start_hour": policy_values["grid_start_hour"],
        },
    }
