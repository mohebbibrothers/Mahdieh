"""دادهٔ گزارش — تبدیل دامنه به جدول‌های آمادهٔ چاپ.

هر تابع این ماژول یک «برگه» را می‌سازد و همه از همان منابع دامنه می‌خوانند:
تقویم سانس‌ها از ``reservation.availability``، سیاست‌ها از ``reservation.policy``،
کنش‌ها از ``ReservationEvent`` و پیام‌ها از ``SmsDelivery``. هیچ عددی دوباره
محاسبه نمی‌شود و هیچ منطقی این‌جا تکرار نمی‌گردد.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from typing import Any

from django.utils import timezone

from apps.core.crypto import blind_index
from apps.identity.choices import Province
from apps.identity.countries import country_title
from apps.notifications.models import SmsDelivery
from apps.reporting.ranges import ReportRange
from apps.reservation import availability
from apps.reservation.choices import (
    ReservationSource,
    ReservationStatus,
)
from apps.reservation.models import Reservation, ReservationEvent

SMS_NAMESPACE = "report:phone"

CELL_STATUS_LABELS = {
    "free": "آزاد",
    "occupied": "اشغال",
    "buffer": "بافر نظافت",
    "blocked": "غیرقابل‌رزرو",
    "past": "گذشته",
}
SOURCE_LABELS = dict(ReservationSource.choices)
EVENT_LABELS = {
    "created": "ایجاد",
    "confirmed": "تأیید",
    "cancelled": "لغو",
    "moved": "جابه‌جایی",
    "superseded": "جانشین‌شده",
    "checked_in": "پذیرش",
    "checked_out": "تخلیه",
    "no_show": "عدم‌حضور",
    "expired": "انقضای نگه‌داشت",
    "note": "یادداشت",
}
STAFF_EVENT_KINDS = ("checked_in", "checked_out", "no_show", "moved", "cancelled", "confirmed")


@dataclass(frozen=True, slots=True)
class ReportData:
    """کل دادهٔ یک گزارش — هفت برگه، یک شیء."""

    report_range: ReportRange
    generated_at: datetime
    reservations: list[dict[str, Any]] = field(default_factory=list)
    slots: list[dict[str, Any]] = field(default_factory=list)
    units: list[dict[str, Any]] = field(default_factory=list)
    daily: list[dict[str, Any]] = field(default_factory=list)
    staff_actions: list[dict[str, Any]] = field(default_factory=list)
    sms: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, Any] = field(default_factory=dict)
    phones_revealed: bool = False

    @property
    def row_counts(self) -> dict[str, int]:
        """شمار ردیف‌های هر برگه — برای مهر و موم سند."""
        return {
            "reservations": len(self.reservations),
            "slots": len(self.slots),
            "units": len(self.units),
            "daily": len(self.daily),
            "staff_actions": len(self.staff_actions),
            "sms": len(self.sms),
        }

    def payload_digest(self) -> str:
        """هش SHA-256 دادهٔ خام — اثر انگشت محتوا (مستقل از ظاهر فایل)."""
        canonical = json.dumps(
            {
                "range": [
                    self.report_range.start_day.isoformat(),
                    self.report_range.end_day.isoformat(),
                ],
                "reservations": self.reservations,
                "units": self.units,
                "daily": self.daily,
                "staff_actions": self.staff_actions,
                "sms": self.sms,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_dataset(
    *,
    report_range: ReportRange,
    generated_at: datetime | None = None,
    unit_codes: tuple[str, ...] = (),
    statuses: tuple[str, ...] = (),
    reveal_phones: bool = False,
) -> ReportData:
    """ساخت کامل دادهٔ گزارش برای بازه و پالایه‌های درخواستی.

    ``reveal_phones`` فقط برای مدیرِ دارای تأیید تازهٔ عامل دوم روشن می‌شود
    (کنترل در لایهٔ HTTP)؛ پیش‌فرض همیشه شمارهٔ پوشانده است.
    """
    moment = generated_at or timezone.now()
    reservations = _reservations(report_range, unit_codes=unit_codes, statuses=statuses)
    matrix = availability.availability_matrix(
        start_day=report_range.start_day, days=report_range.days, staff=True
    )
    if unit_codes:
        matrix = _filter_matrix(matrix, unit_codes)

    return ReportData(
        report_range=report_range,
        generated_at=moment,
        reservations=_reservation_rows(reservations, reveal_phones=reveal_phones),
        phones_revealed=reveal_phones,
        slots=_slot_rows(matrix),
        units=_unit_rows(matrix, reservations),
        daily=_daily_rows(matrix, reservations),
        staff_actions=_staff_action_rows(report_range),
        sms=_sms_rows(report_range),
        totals=_totals(reservations, matrix),
    )


# ── واکشی‌ها ──────────────────────────────────────────────────────────────────


def _reservations(
    report_range: ReportRange, *, unit_codes: tuple[str, ...], statuses: tuple[str, ...]
) -> list[Reservation]:
    """رزروهایی که با بازهٔ گزارش هم‌پوشانی دارند (مرتب و پیش‌بارگذاری‌شده)."""
    queryset = (
        Reservation.all_objects.select_related("unit", "unit__floor", "guest", "created_by")
        .filter(
            stay_period__overlap=(report_range.start_moment, report_range.end_moment),
        )
        .order_by("stay_period", "code")
    )
    if unit_codes:
        queryset = queryset.filter(unit__code__in=unit_codes)
    if statuses:
        queryset = queryset.filter(status__in=statuses)
    return list(queryset)


def _filter_matrix(matrix: list[Any], unit_codes: tuple[str, ...]) -> list[Any]:
    """پالایش ماتریس تقویم به واحدهای درخواستی."""
    from dataclasses import replace

    return [
        replace(day, units=tuple(unit for unit in day.units if unit.code in unit_codes))
        for day in matrix
    ]


# ── برگه‌ها ──────────────────────────────────────────────────────────────────


def _reservation_rows(
    reservations: list[Reservation], *, reveal_phones: bool = False
) -> list[dict[str, Any]]:
    """برگهٔ «رزروها» — یک ردیف برای هر رزرو، با تاریخ شمسی و هویت پوشانده."""
    rows: list[dict[str, Any]] = []
    for reservation in reservations:
        start_local = timezone.localtime(reservation.starts_at)
        end_local = timezone.localtime(reservation.ends_at)
        rows.append(
            {
                "code": reservation.code,
                "status_label": reservation.status_label(),
                "status": reservation.status,
                "source": str(SOURCE_LABELS.get(reservation.source, reservation.source)),
                "floor": reservation.unit.floor.number,
                "unit": reservation.unit.code,
                "unit_title": reservation.unit.title,
                "unit_type": reservation.unit.type_label,
                "start_jalali": reservation.stay_date_jalali,
                "start_time": start_local.strftime("%H:%M"),
                "end_jalali": _jalali_date(end_local),
                "end_time": end_local.strftime("%H:%M"),
                "cells": reservation.cells_count,
                "duration": reservation.duration_label,
                "guests": reservation.guests_count,
                "guest_name": reservation.guest.display_name,
                "guest_phone": (
                    display_phone(reservation.guest.phone)
                    if reveal_phones
                    else _mask_phone(reservation.guest.phone)
                ),
                "nationality": country_title(reservation.guest.nationality),
                "province": Province.label_of(reservation.guest.province)
                if reservation.guest.province
                else "—",
                "city": reservation.guest.city or "—",
                "created_by": reservation.created_by.display_name
                if reservation.created_by
                else "—",
                "guest_note": reservation.guest_note or "",
                "staff_note": reservation.staff_note or "",
                "created_at": timezone.localtime(reservation.created_at).strftime("%Y-%m-%d %H:%M"),
            }
        )
    return rows


def _slot_rows(matrix: list[Any]) -> list[dict[str, Any]]:
    """برگهٔ «تقویم سانس‌ها» — واحد × نیم‌روز، وضعیت به‌ازای هر روز."""
    rows: list[dict[str, Any]] = []
    for day in matrix:
        for unit in day.units:
            for cell in unit.cells:
                rows.append(
                    {
                        "day": day.day.isoformat(),
                        "date_jalali": day.date_jalali,
                        "weekday": day.weekday,
                        "unit": unit.code,
                        "floor": unit.floor_number,
                        "unit_title": unit.title,
                        "half": cell.label,
                        "kind": cell.kind,
                        "start": timezone.localtime(cell.start).strftime("%H:%M"),
                        "end": timezone.localtime(cell.end).strftime("%H:%M"),
                        "status": CELL_STATUS_LABELS.get(cell.status, cell.status),
                        "detail": cell.detail,
                    }
                )
    return rows


def _unit_rows(matrix: list[Any], reservations: list[Reservation]) -> list[dict[str, Any]]:
    """برگهٔ «اشغال واحدها» — کارنامهٔ هر واحد در بازهٔ گزارش."""
    nights: Counter[str] = Counter()
    for reservation in reservations:
        if reservation.status in ReservationStatus.active_statuses():
            nights[reservation.unit.code] += reservation.nights

    rows: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for day in matrix:
        for unit in day.units:
            bucket = seen.setdefault(
                unit.code,
                {
                    "unit": unit.code,
                    "floor": unit.floor_number,
                    "unit_title": unit.title,
                    "unit_type": getattr(unit, "unit_type", ""),
                    "capacity": unit.capacity_total,
                    "total": 0,
                    "occupied": 0,
                    "free": 0,
                    "buffer": 0,
                    "blocked": 0,
                },
            )
            bucket["total"] += len(unit.cells)
            for cell in unit.cells:
                if cell.status == "occupied":
                    bucket["occupied"] += 1
                elif cell.status == "free":
                    bucket["free"] += 1
                elif cell.status == "buffer":
                    bucket["buffer"] += 1
                elif cell.status == "blocked":
                    bucket["blocked"] += 1

    for code in sorted(seen):
        bucket = seen[code]
        total = bucket["total"] or 1
        bucket["occupancy"] = round(bucket["occupied"] / total, 4)
        bucket["nights"] = nights.get(code, 0)
        rows.append(
            {
                "unit": bucket["unit"],
                "floor": bucket["floor"],
                "unit_title": bucket["unit_title"],
                "unit_type": _unit_type_label(bucket["unit_type"]),
                "capacity": bucket["capacity"],
                "total": bucket["total"],
                "occupied": bucket["occupied"],
                "free": bucket["free"],
                "buffer": bucket["buffer"],
                "blocked": bucket["blocked"],
                "occupancy": bucket["occupancy"],
                "nights": bucket["nights"],
            }
        )
    return rows


def _daily_rows(matrix: list[Any], reservations: list[Reservation]) -> list[dict[str, Any]]:
    """برگهٔ «آمار روزانه» — نبض روزبه‌روز زائرسرا."""
    arrivals: Counter[str] = Counter()
    departures: Counter[str] = Counter()
    for reservation in reservations:
        arrivals[timezone.localtime(reservation.starts_at).date().isoformat()] += 1
        departures[timezone.localtime(reservation.ends_at).date().isoformat()] += 1

    rows: list[dict[str, Any]] = []
    for day in matrix:
        key = day.day.isoformat()
        occupied = sum(1 for unit in day.units for cell in unit.cells if cell.status == "occupied")
        total = day.total_cells or 1
        rows.append(
            {
                "day": key,
                "date_jalali": day.date_jalali,
                "weekday": day.weekday,
                "is_weekend": day.is_weekend,
                "demand": _demand_label(day.demand_level),
                "total": day.total_cells,
                "occupied": occupied,
                "free": max(day.free_cells, 0),
                "occupancy": round(occupied / total, 4),
                "arrivals": arrivals.get(key, 0),
                "departures": departures.get(key, 0),
            }
        )
    return rows


def _staff_action_rows(report_range: ReportRange) -> list[dict[str, Any]]:
    """برگهٔ «کنش‌های کارکنان» — چه کسی، چه زمانی، چه کاری و چرا."""
    events = (
        ReservationEvent.objects.select_related("reservation", "reservation__unit", "actor")
        .filter(
            kind__in=STAFF_EVENT_KINDS,
            created_at__gte=report_range.start_moment,
            created_at__lt=report_range.end_moment,
        )
        .order_by("created_at")
    )
    rows: list[dict[str, Any]] = []
    for event in events:
        rows.append(
            {
                "moment": timezone.localtime(event.created_at).strftime("%Y-%m-%d %H:%M"),
                "actor": event.actor.display_name if event.actor else event.actor_label or "سیستم",
                "kind": EVENT_LABELS.get(event.kind, event.kind),
                "code": event.reservation.code,
                "unit": event.reservation.unit.code,
                "from_status": event.from_status or "—",
                "to_status": event.to_status or "—",
                "reason": event.reason or "—",
            }
        )
    return rows


def _sms_rows(report_range: ReportRange) -> list[dict[str, Any]]:
    """برگهٔ «پیامک‌ها» — حاکمیت ارسال، با تلفن پوشانده."""
    rows: list[dict[str, Any]] = []
    for delivery in (
        SmsDelivery.objects.filter(
            created_at__gte=report_range.start_moment,
            created_at__lt=report_range.end_moment,
        )
        .order_by("created_at")
        .iterator(chunk_size=500)
    ):
        rows.append(
            {
                "moment": timezone.localtime(delivery.created_at).strftime("%Y-%m-%d %H:%M"),
                "template": delivery.template_key,
                "phone": _mask_phone(delivery.phone_masked),
                "status": _sms_status_label(delivery.status),
                "provider": delivery.provider or "—",
                "attempts": delivery.attempts,
            }
        )
    return rows


def _totals(reservations: list[Reservation], matrix: list[Any]) -> dict[str, Any]:
    """جمع‌بندی مدیریتی — همان چیزی که مدیر در نگاه اول می‌خواهد."""
    by_status = Counter(reservation.status for reservation in reservations)
    by_source = Counter(reservation.source for reservation in reservations)
    total_cells = sum(day.total_cells for day in matrix)
    occupied_cells = sum(
        1
        for day in matrix
        for unit in day.units
        for cell in unit.cells
        if cell.status == "occupied"
    )
    guests = sum(reservation.guests_count for reservation in reservations)
    return {
        "reservations": len(reservations),
        # برچسب‌ها در JSON باید ``str`` باشند (نه proxy تنبل ترجمه) — وگرنه
        # پاسخ API هنگام JSON‌سازی می‌شکند.
        "by_status": {str(ReservationStatus(key).label): value for key, value in by_status.items()},
        "by_source": {str(SOURCE_LABELS.get(key, key)): value for key, value in by_source.items()},
        # همان تفکیک با کد ماشین‌خوان — فرانت نمودار را با کد می‌سازد و برچسب را
        # از ``/config`` می‌خواند؛ کلیدهای برچسبی بالا برای سازگاری می‌مانند.
        "by_status_code": dict(sorted(by_status.items())),
        "by_source_code": dict(sorted(by_source.items())),
        "active": sum(
            count
            for status, count in by_status.items()
            if status in ReservationStatus.active_statuses()
        ),
        "cancelled": sum(
            count
            for status, count in by_status.items()
            if status in ReservationStatus.terminal_statuses()
        ),
        "guests": guests,
        "total_cells": total_cells,
        "occupied_cells": occupied_cells,
        "occupancy": round(occupied_cells / total_cells, 4) if total_cells else 0.0,
        "days": len(matrix),
    }


# ── کمکی‌ها ──────────────────────────────────────────────────────────────────


def _jalali_date(moment: datetime) -> str:
    """تاریخ شمسی یک لحظه (برای ستون خروج)."""
    from apps.core.jalali import format_jalali

    return format_jalali(timezone.localtime(moment).date(), numeric=True)


def _mask_phone(value: str) -> str:
    """پوشاندن تلفن در گزارش — گزارش هم «دادهٔ حساس» است."""
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) < 7:  # noqa: PLR2004
        return "—"
    return f"{digits[-10:-7]}***{digits[-4:]}"


def display_phone(value: str) -> str:
    """شمارهٔ کامل خوانا برای تماس: ایران ⇒ «09123456789»، خارجی ⇒ «+…» دست‌نخورده."""
    if value.startswith("+98") and len(value) == 13:  # noqa: PLR2004
        return f"0{value[3:]}"
    return value


def _sms_status_label(status: str) -> str:
    """برچسب فارسی وضعیت پیامک."""
    return {
        "queued": "در صف",
        "sent": "ارسال‌شده",
        "failed": "ناموفق",
        "suppressed": "خاموش (ساعات سکوت)",
    }.get(status, status)


def _demand_label(level: str) -> str:
    """برچسب فارسی سطح تقاضا."""
    return {"low": "آرام", "normal": "معمولی", "high": "پرکار", "peak": "اوج"}.get(level, level)


def _unit_type_label(value: str) -> str:
    """برچسب فارسی نوع واحد (سوئیت / اتاق بزرگ)."""
    return {"suite": "سوئیت", "large_room": "اتاق بزرگ"}.get(value, value)


def phone_digest(phone: str) -> str:
    """اثر انگشت HMAC یک شماره — برای تطبیق بدون افشای متن روشن."""
    return blind_index(phone, namespace=SMS_NAMESPACE)
