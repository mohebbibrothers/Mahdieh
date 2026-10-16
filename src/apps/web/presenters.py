"""سازنده‌های مدل‌نمای صفحه (Presenter) — منطق «چه چیزی نمایش داده شود».

چرا لایهٔ جدا؟ قالب باید «ساده و بدون شرط» بماند. هر محاسبه‌ای که معنی دامنه
دارد (تبدیل ماتریس روز×واحد به سطرها، گروه‌بندی برد کارکنان، آمار تقویم) اینجا
انجام می‌شود تا هم قابل تست باشد و هم قالب‌ها فقط بچرخند و چاپ کنند.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from django.utils import timezone

from apps.core.jalali import format_jalali, gregorian_to_jalali, is_weekend, weekday_short
from apps.inventory.choices import UnitType
from apps.reservation.availability import (
    STATUS_BLOCKED,
    STATUS_BUFFER,
    STATUS_FREE,
    STATUS_OCCUPIED,
    STATUS_PAST,
    CellAvailability,
    DayAvailability,
)
from apps.reservation.choices import ReservationStatus

CELL_HINTS: Final[dict[str, str]] = {
    STATUS_FREE: "آزاد برای رزرو",
    STATUS_OCCUPIED: "رزروشده",
    STATUS_BUFFER: "فاصلهٔ نظافت و تحویل",
    STATUS_BLOCKED: "بسته (نگهداری یا تصمیم مدیریتی)",
    STATUS_PAST: "زمان گذشته",
}

UNIT_ACCENTS: Final[dict[str, str]] = {
    UnitType.SUITE: "accent-suite",
    UnitType.LARGE_ROOM: "accent-room",
}


@dataclass(frozen=True, slots=True)
class DayHeading:
    """سرستون تقویم — همهٔ چیزی که برای ساخت هدر یک روز لازم است."""

    day: date
    jalali_day: str
    jalali_month: str
    jalali_long: str
    weekday: str
    is_weekend: bool
    demand_level: str
    free_cells: int
    total_cells: int


@dataclass(frozen=True, slots=True)
class DayColumn:
    """یک ستون تقویم = سرستون روز + سلول‌های همهٔ واحدها در آن روز."""

    heading: DayHeading
    cells: tuple[CellAvailability, ...]


@dataclass(frozen=True, slots=True)
class CalendarRow:
    """یک سطر تقویم = یک واحد اقامتی در تمام روزهای بازه."""

    code: str
    title: str
    floor_number: int
    unit_type: str
    capacity_total: int
    columns: tuple[DayColumn, ...]
    free_cells: int
    occupancy: str


def cell_hint(cell: CellAvailability) -> str:
    """متن راهنمای سلول (tooltip) — «روز ۰۸:۰۰→۲۰:۰۰ · آزاد»."""
    base = f"{cell.label}"
    if cell.detail:
        return f"{base} · {cell.detail}"
    return f"{base} · {CELL_HINTS.get(cell.status, cell.status)}"


def unit_accent(unit_or_type: Any) -> str:
    """کلاس رنگ برند برای یک واحد (سوئیت = فیروزه، اتاق = طلا)."""
    unit_type = getattr(unit_or_type, "unit_type", unit_or_type)
    return UNIT_ACCENTS.get(str(unit_type), "accent-room")


def is_peak(day: date) -> bool:
    """آیا روز در اوج تقاضا (پنجشنبه/جمعه) است؟"""
    return is_weekend(day)


def day_headings(matrix: Sequence[DayAvailability]) -> list[DayHeading]:
    """سرستون‌های تقویم از ماتریس موجودی."""
    headings: list[DayHeading] = []
    for day in matrix:
        jy, jm, jd = gregorian_to_jalali(day.day)
        headings.append(
            DayHeading(
                day=day.day,
                jalali_day=f"{jd}",
                jalali_month=f"{jy:04d}/{jm:02d}",
                jalali_long=format_jalali(day.day),
                weekday=weekday_short(day.day),
                is_weekend=is_peak(day.day),
                demand_level=day.demand_level,
                free_cells=day.free_cells,
                total_cells=day.total_cells,
            )
        )
    return headings


def calendar_rows(matrix: Sequence[DayAvailability]) -> list[CalendarRow]:
    """تبدیل «روز×واحد» به «سطر واحد × ستون روز» — شکل طبیعی جدول تقویم."""
    if not matrix:
        return []
    headings = day_headings(matrix)
    reference = matrix[0].units
    rows: list[CalendarRow] = []
    for position, unit in enumerate(reference):
        columns = tuple(
            DayColumn(heading=headings[index], cells=day.units[position].cells)
            for index, day in enumerate(matrix)
        )
        free = sum(1 for column in columns for cell in column.cells if cell.status == STATUS_FREE)
        total = sum(len(column.cells) for column in columns) or 1
        rows.append(
            CalendarRow(
                code=unit.code,
                title=unit.title,
                floor_number=unit.floor_number,
                unit_type=unit.unit_type,
                capacity_total=unit.capacity_total,
                columns=columns,
                free_cells=free,
                occupancy=f"{round(100 * (1 - free / total))}",
            )
        )
    return rows


def matrix_stats(matrix: Sequence[DayAvailability]) -> dict[str, Any]:
    """آمار خلاصهٔ یک بازهٔ تقویم — برای نوار وضعیت بالای صفحه."""
    total = sum(day.total_cells for day in matrix)
    free = sum(day.free_cells for day in matrix)
    busiest = max(matrix, key=lambda day: day.occupancy_rate, default=None)
    quietest = min(matrix, key=lambda day: day.occupancy_rate, default=None)
    return {
        "days": len(matrix),
        "total_cells": total,
        "free_cells": free,
        "occupancy": round(1 - (free / total), 4) if total else 0.0,
        "fully_free_days": [day.day for day in matrix if day.free_cells == day.total_cells],
        "busiest": busiest,
        "quietest": quietest,
    }


def units_by_floor(units: Iterable[Any]) -> list[dict[str, Any]]:
    """گروه‌بندی واحدها بر پایهٔ طبقه — برای صفحهٔ معرفی زائرسرا."""
    grouped: dict[int, dict[str, Any]] = {}
    for unit in units:
        number = int(unit.floor_number)
        bucket = grouped.setdefault(
            number,
            {"number": number, "title": f"طبقهٔ {number}", "units": []},
        )
        bucket["units"].append(unit)
    return [grouped[key] for key in sorted(grouped)]


def group_board(rows: Iterable[Any], *, day: date) -> dict[str, list[Any]]:
    """گروه‌بندی برد کارکنان به «در اقامت / ورودی امروز / سایر».

    تعریف «در اقامت»: پذیرش‌شده و هنوز تخلیه‌نشده. «ورودی امروز»: رزرو
    فعالی که اقامتش امروز آغاز می‌شود. بقیه در گروه سوم می‌مانند.
    """
    in_house: list[Any] = []
    arrivals: list[Any] = []
    others: list[Any] = []
    for reservation in rows:
        status = str(reservation.status)
        starts_today = reservation.starts_at.astimezone().date() == day
        if status == ReservationStatus.CHECKED_IN:
            in_house.append(reservation)
        elif starts_today and status in ReservationStatus.active_statuses():
            arrivals.append(reservation)
        else:
            others.append(reservation)
    return {"in_house": in_house, "arrivals": arrivals, "others": others}


def board_kpis(grouped: dict[str, list[Any]], *, units_total: int) -> list[dict[str, str]]:
    """کارت‌های شاخص تابلوی کارکنان."""
    in_house = grouped["in_house"]
    arrivals = grouped["arrivals"]
    guests = sum(reservation.guests_count for reservation in in_house)
    return [
        {"value": str(len(arrivals)), "label": "ورودی امروز", "hint": "پذیرش در انتظار"},
        {"value": str(len(in_house)), "label": "در اقامت", "hint": "واحدهای فعال"},
        {"value": str(guests), "label": "نفر حاضر", "hint": "جمع مهمانان"},
        {"value": f"{len(in_house)}/{units_total}", "label": "اشغال", "hint": "واحد در اقامت"},
    ]


def upcoming_occasions(occasions: Iterable[Any], *, limit: int = 4) -> list[dict[str, str]]:
    """مناسبت‌های پیش‌رو برای نوار صفحهٔ اصلی."""
    items: list[dict[str, str]] = []
    for occasion in occasions:
        if len(items) >= limit:
            break
        items.append(
            {
                "title": str(occasion.title),
                "date": format_jalali(occasion.date_gregorian),
                "level": str(occasion.demand_level),
            }
        )
    return items


def moment_label(moment: datetime) -> str:
    """برچسب کوتاه یک لحظه: «۲۳ مهر ۱۴۰۵ · ۲۰:۰۰» — همیشه به وقت محلی."""
    local = timezone.localtime(moment) if timezone.is_aware(moment) else moment
    return f"{format_jalali(local)} · {local.hour:02d}:{local.minute:02d}"
