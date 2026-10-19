"""سریال‌ایزرهای رزرو — قرارداد ورودی/خروجی و سند OpenAPI.

قاعدهٔ حریم خصوصی: نمای «عمومی» رزرو هرگز نام یا شمارهٔ مهمان را نشان نمی‌دهد؛
نمای کارکنان (``StaffReservationSerializer``) جزئیات عملیاتی را اضافه می‌کند.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone
from rest_framework import serializers

from apps.core.serializers import PageMetaSerializer
from apps.reservation import standing
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Reservation, ReservationEvent

TRANSITION_ACTIONS: tuple[str, ...] = ("check-in", "check-out", "no-show", "note")


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


class TransitionRequestSerializer(serializers.Serializer[Any]):
    """بدنهٔ انتقال کارمندی — ``staff_note`` برای no-show و note اجباری است (سند)."""

    staff_note = serializers.CharField(
        max_length=300, required=False, help_text="برای no-show و note اجباری؛ برای بقیه نادیده"
    )


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
            "can_cancel",
            "free_cancel_until",
            "created_at",
        )

    start = serializers.SerializerMethodField()
    end = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    free_cancel_until = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()

    # همهٔ لحظه‌ها به وقت محلی سایت (+03:30): رزرو تازه‌ساخته و رزرو خوانده‌شده
    # از دیتابیس (که درایور آن را UTC برمی‌گرداند) دقیقاً یک رشته می‌دهند.
    def get_start(self, obj: Reservation) -> str:
        """آغاز اقامت در قالب ISO 8601 (وقت محلی سایت)."""
        return timezone.localtime(obj.starts_at).isoformat()

    def get_end(self, obj: Reservation) -> str:
        """پایان اقامت در قالب ISO 8601 (وقت محلی سایت)."""
        return timezone.localtime(obj.ends_at).isoformat()

    def get_created_at(self, obj: Reservation) -> str:
        """زمان ثبت رزرو (وقت محلی سایت)."""
        return timezone.localtime(obj.created_at).isoformat()

    def get_can_cancel(self, obj: Reservation) -> bool:
        """آیا دکمهٔ «لغو» باید نمایش داده شود؟"""
        return obj.status in ReservationStatus.cancellable_statuses()

    def get_free_cancel_until(self, obj: Reservation) -> str:
        """آخرین لحظهٔ لغو رایگان؛ پس از آن، لغو «دیرهنگام» با خنک‌شدن است."""
        return timezone.localtime(standing.free_cancel_until(obj)).isoformat()


class StaffReservationSerializer(ReservationSerializer):
    """نمای کارکنان — با جزئیات عملیاتی و هویت مهمان (پوشانده)."""

    guest_id = serializers.UUIDField(source="guest.id", read_only=True)
    guest_name = serializers.CharField(source="guest.display_name", read_only=True)
    guest_phone_masked = serializers.CharField(source="guest.masked_phone", read_only=True)
    guest_nationality = serializers.CharField(source="guest.nationality", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    superseded_by_code = serializers.CharField(
        source="superseded_by.code", read_only=True, default=None
    )

    class Meta(ReservationSerializer.Meta):
        fields: tuple[str, ...] = (  # type: ignore[assignment]
            *ReservationSerializer.Meta.fields,
            "guest_id",
            "guest_name",
            "guest_phone_masked",
            "guest_nationality",
            "staff_note",
            "expected_arrival",
            "vehicle_plate",
            "source",
            "source_label",
            "is_late_cancel",
            "version",
            "superseded_by_code",
            "cancelled_at",
            "cancel_reason_kind",
            "cancel_reason",
            "checked_in_at",
            "checked_out_at",
            "no_show_at",
        )


class ViolationSerializer(serializers.Serializer[Any]):
    """یک تخلف قاعده: کد ماشین‌خوان + پیام فارسی."""

    code = serializers.CharField()
    message = serializers.CharField()


class AlternativeSerializer(serializers.Serializer[Any]):
    """یک بازهٔ جایگزین پیشنهادی وقتی بازهٔ درخواستی اشغال است."""

    unit_code = serializers.CharField()
    unit_title = serializers.CharField()
    floor_number = serializers.IntegerField()
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    date_jalali = serializers.CharField()
    is_same_unit = serializers.BooleanField()


class QuoteUnitSerializer(serializers.Serializer[Any]):
    """واحدِ پیش‌بینی."""

    code = serializers.CharField()
    title = serializers.CharField()


class QuoteSerializer(serializers.Serializer[Any]):
    """پاسخ پیش‌بینی رزرو — سبد پیش از تأیید (فقط برای سند و کلاینت تایپ‌شده)."""

    allowed = serializers.BooleanField()
    is_available = serializers.BooleanField()
    violations = ViolationSerializer(many=True)
    alternatives = AlternativeSerializer(many=True)
    unit = QuoteUnitSerializer()
    start = serializers.DateTimeField(allow_null=True)
    end = serializers.DateTimeField(allow_null=True)
    cells = serializers.IntegerField()
    duration_label = serializers.CharField()
    guests_count = serializers.IntegerField()
    capacity_total = serializers.IntegerField()
    notes = serializers.ListField(child=serializers.CharField())


class QuotaSerializer(serializers.Serializer[Any]):
    """یک سهمیهٔ شمارشی."""

    used = serializers.IntegerField()
    limit = serializers.IntegerField()
    remaining = serializers.IntegerField()
    window_days = serializers.IntegerField(allow_null=True)


class StandingSerializer(serializers.Serializer[Any]):
    """وضعیت توان رزرو زائر (سند)."""

    can_book = serializers.BooleanField()
    profile_complete = serializers.BooleanField()
    active = QuotaSerializer()
    window = QuotaSerializer()
    year = QuotaSerializer()
    cooldown_until = serializers.DateTimeField(allow_null=True)
    suspended_until = serializers.DateTimeField(allow_null=True)
    next_eligible_at = serializers.DateTimeField(allow_null=True)
    blockers = ViolationSerializer(many=True)


class CancelPreviewSerializer(serializers.Serializer[Any]):
    """پیامد لغو در همین لحظه (سند)."""

    cancellable = serializers.BooleanField()
    late = serializers.BooleanField()
    free_until = serializers.DateTimeField()
    cooldown_hours = serializers.IntegerField()
    cooldown_until = serializers.DateTimeField(allow_null=True)
    message = serializers.CharField()


class ReservationEventSerializer(serializers.ModelSerializer[ReservationEvent]):
    """یک رخداد از تاریخچهٔ رزرو — نمای زائر (بدون نام کارمند و دلیل داخلی)."""

    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    at = serializers.SerializerMethodField()

    class Meta:
        model = ReservationEvent
        fields: tuple[str, ...] = ("id", "kind", "kind_label", "from_status", "to_status", "at")

    def get_at(self, obj: ReservationEvent) -> str:
        """زمان رخداد (وقت محلی سایت)."""
        return timezone.localtime(obj.created_at).isoformat()


class StaffReservationEventSerializer(ReservationEventSerializer):
    """رخداد برای کارکنان — با عامل، دلیل و فراداده."""

    class Meta(ReservationEventSerializer.Meta):
        fields: tuple[str, ...] = (  # type: ignore[assignment]
            *ReservationEventSerializer.Meta.fields,
            "actor_label",
            "reason",
            "metadata",
        )


class ReservationEventListSerializer(serializers.Serializer[Any]):
    """پوشش تاریخچهٔ رزرو."""

    results = ReservationEventSerializer(many=True)


class StaffReservationEventListSerializer(serializers.Serializer[Any]):
    """پوشش تاریخچهٔ رزرو برای کارکنان."""

    results = StaffReservationEventSerializer(many=True)


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
