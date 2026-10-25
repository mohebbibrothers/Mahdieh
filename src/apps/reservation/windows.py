"""کشف بازهٔ آزاد — «نخستین زمان آزاد هر واحد» در کل افق رزرو.

صفحهٔ تقویم (P-04) فقط چند روز را نشان می‌دهد؛ اما پرسش زائر این است: «زودترین
زمانی که این واحد برای ما آزاد است کی است؟» — حتی اگر دو هفتهٔ پیش رو پر باشد.
این ماژول همان پرسش را با **قواعد خود موتور** پاسخ می‌دهد:

    • فقط وضعیت «آزاد» ماتریس موجودی شمرده می‌شود (رزرو، نگه‌داشت، بافر نظافت و
      بلوک تعمیر ران را می‌شکنند) — پس پیشنهاد همان چیزی است که ثبت رزرو هم می‌پذیرد؛
    • آغاز بازه دست‌کم ``min_lead_time_hours`` پس از «اکنون» و حداکثر تا افق رزرو است؛
    • واحدی که ظرفیتش از تعداد همسفران کمتر است «جا ندارد» (نه پیشنهاد گمراه‌کننده)؛
    • طول ران کامل گزارش می‌شود (زائر بداند چقدر پیوسته جا هست) و جدا از آن،
      بلندترین اقامت مجاز در همان ران (سقف سیاست) برای انتخاب یک‌کلیکی.

ماتریس از کش نسخه‌دار ``availability_matrix`` خوانده می‌شود (تکه‌های ≤۶۲ روزه)،
پس هزینهٔ این پرسش پس از نخستین بار تقریباً صفر است.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.utils import timezone

from apps.core.jalali import format_jalali, weekday_name
from apps.inventory.lattice import stay_label
from apps.reservation.availability import (
    MAX_MATRIX_DAYS,
    STATUS_FREE,
    CellAvailability,
    DayAvailability,
    availability_matrix,
)
from apps.reservation.policy import policy_int


@dataclass(frozen=True, slots=True)
class FreeWindow:
    """نخستین ران پیوستهٔ آزاد یک واحد."""

    start: datetime
    end: datetime
    day: date
    date_jalali: str
    weekday: str
    period: str
    start_label: str
    cells: int
    bookable_cells: int
    open_ended: bool

    @property
    def stay_label(self) -> str:
        """برچسب انسانی طول ران («۲ شبانه‌روز»)."""
        return stay_label(self.cells)


@dataclass(frozen=True, slots=True)
class UnitWindow:
    """وضعیت کشف یک واحد: مشخصات + نخستین بازه (یا هیچ)."""

    code: str
    title: str
    floor_number: int
    unit_type: str
    capacity_total: int
    fits_guests: bool
    window: FreeWindow | None


@dataclass(frozen=True, slots=True)
class WindowsResult:
    """پاسخ کامل کشف: مرزهای جست‌وجو + یک ردیف برای هر واحد فعال."""

    not_before: datetime
    horizon_end: datetime
    guests: int
    min_cells: int
    max_cells: int
    units: tuple[UnitWindow, ...]


def _horizon_matrix(start_day: date, days: int) -> list[DayAvailability]:
    """ماتریس عمومی کل افق، در تکه‌های مجاز ``availability_matrix`` (هر تکه کش جدا)."""
    matrix: list[DayAvailability] = []
    cursor = start_day
    remaining = days
    while remaining > 0:
        chunk = min(remaining, MAX_MATRIX_DAYS)
        matrix.extend(availability_matrix(start_day=cursor, days=chunk, staff=False))
        cursor += timedelta(days=chunk)
        remaining -= chunk
    return matrix


def _cells_by_unit(matrix: list[DayAvailability]) -> dict[str, list[CellAvailability]]:
    """نیم‌روزهای هر واحد به ترتیب زمان (اندیس شبکه یکتا و پیوسته است)."""
    rows: dict[str, dict[int, CellAvailability]] = {}
    for day in matrix:
        for unit in day.units:
            row = rows.setdefault(unit.code, {})
            for cell in unit.cells:
                row.setdefault(cell.index, cell)
    return {code: [row[i] for i in sorted(row)] for code, row in rows.items()}


def _first_window(
    cells: list[CellAvailability],
    *,
    not_before: datetime,
    latest_start: datetime,
    min_cells: int,
    max_cells: int,
) -> FreeWindow | None:
    """نخستین ران آزادِ دست‌کم ``min_cells`` که آغازش در بازهٔ مجاز ثبت است."""
    total = len(cells)
    index = 0
    while index < total:
        head = cells[index]
        if head.status != STATUS_FREE or head.start < not_before:
            index += 1
            continue
        if head.start > latest_start:
            return None
        run_start = index
        while index < total and cells[index].status == STATUS_FREE:
            index += 1
        length = index - run_start
        if length < min_cells:
            continue
        last = cells[index - 1]
        local_day = timezone.localtime(head.start).date()
        return FreeWindow(
            start=head.start,
            end=last.end,
            day=local_day,
            date_jalali=format_jalali(local_day),
            weekday=weekday_name(local_day),
            period=head.kind,
            start_label=head.label,
            cells=length,
            bookable_cells=min(length, max_cells),
            open_ended=index == total,
        )
    return None


def first_free_windows(
    *, guests: int = 1, min_cells: int | None = None, now: datetime | None = None
) -> WindowsResult:
    """نخستین بازهٔ آزاد هر واحد فعال در کل افق رزرو (ترتیب واحدها = ترتیب ماتریس)."""
    moment = now or timezone.now()
    horizon_days = policy_int("reservation.booking_horizon_days")
    max_cells = policy_int("reservation.max_cells")
    floor = max(1, min_cells if min_cells is not None else policy_int("reservation.min_cells"))
    not_before = moment + timedelta(hours=policy_int("reservation.min_lead_time_hours"))
    latest_start = moment + timedelta(days=horizon_days)

    start_day = timezone.localtime(moment).date()
    # یک روز بیشتر: ران آغازشده در واپسین روز افق تا پایانش دیده شود.
    matrix = _horizon_matrix(start_day, horizon_days + 2)
    by_unit = _cells_by_unit(matrix)

    units: list[UnitWindow] = []
    for unit in matrix[0].units if matrix else ():
        fits = unit.capacity_total >= guests
        window = (
            _first_window(
                by_unit.get(unit.code, []),
                not_before=not_before,
                latest_start=latest_start,
                min_cells=floor,
                max_cells=max_cells,
            )
            if fits
            else None
        )
        units.append(
            UnitWindow(
                code=unit.code,
                title=unit.title,
                floor_number=unit.floor_number,
                unit_type=unit.unit_type,
                capacity_total=unit.capacity_total,
                fits_guests=fits,
                window=window,
            )
        )
    return WindowsResult(
        not_before=not_before,
        horizon_end=latest_start,
        guests=guests,
        min_cells=floor,
        max_cells=max_cells,
        units=tuple(units),
    )
