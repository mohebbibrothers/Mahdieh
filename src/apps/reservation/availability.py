"""موجودی محاسبه‌شده — تقویم «واحد × نیم‌روز» با کش نسخه‌دار.

این ماژول پاسخ یک پرسش است: «در هر نیم‌روز از روزهای آینده، هر واحد در چه
وضعیتی است؟» — و آن را به‌صورت دادهٔ آماده برای فرانت‌اند برمی‌گرداند.

دو تصمیم مهم:
    • **چهار وضعیت، نه دو**: آزاد / اشغال / بافر نظافت / بلوک‌شده. کاربر باید
      بفهمد چرا یک سانس آزاد نیست («در حال نظافت» با «تعمیر» یکی نیست).
    • **کش با Epoch**: هر تغییر رزرو یا بلوک، نسخه را بالا می‌برد؛ نتیجه: تقویم
      همیشه تازه است و هیچ کلید کهنه‌ای جا نمی‌ماند — بدون حذف کلیدبه‌کلید.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from typing import Any

from django.db.models import Q
from django.utils import timezone
from psycopg.types.range import Range

from apps.core.cache import get_or_set_versioned, invalidate
from apps.core.jalali import format_jalali, is_weekend, weekday_name
from apps.inventory.choices import DemandLevel
from apps.inventory.lattice import (
    Cell,
    LatticeConfig,
    cell_at_boundary,
    cells_for_day,
    period_from_index,
)
from apps.inventory.models import Block, Unit
from apps.inventory.selectors import active_units, blocks_overlapping, demand_level_by_day
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Reservation
from apps.reservation.policy import lattice_config

logger = logging.getLogger(__name__)

AVAILABILITY_NAMESPACE = "availability"
SCOPE_PUBLIC = "public"
SCOPE_STAFF = "staff"
MATRIX_TTL_SECONDS = 60
MAX_MATRIX_DAYS = 62
MAX_ALTERNATIVES = 3
SEARCH_WINDOW_DAYS = 14
SECONDS_PER_HOUR = 3600

STATUS_FREE = "free"
STATUS_OCCUPIED = "occupied"
STATUS_BUFFER = "buffer"
STATUS_BLOCKED = "blocked"
STATUS_PAST = "past"

STATUS_LABELS: dict[str, str] = {
    STATUS_FREE: "آزاد",
    STATUS_OCCUPIED: "اشغال",
    STATUS_BUFFER: "بافر نظافت",
    STATUS_BLOCKED: "غیرقابل‌رزرو",
    STATUS_PAST: "گذشته",
}


@dataclass(frozen=True, slots=True)
class CellAvailability:
    """وضعیت یک نیم‌روز روی یک واحد."""

    index: int
    kind: str
    label: str
    start: datetime
    end: datetime
    status: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class UnitAvailability:
    """وضعیت یک واحد در یک شبانه‌روز."""

    code: str
    title: str
    floor_number: int
    unit_type: str
    capacity_total: int
    cells: tuple[CellAvailability, ...]

    @property
    def free_cells(self) -> int:
        """تعداد نیم‌روزهای آزاد این واحد در این روز."""
        return sum(1 for cell in self.cells if cell.status == STATUS_FREE)

    @property
    def is_fully_free(self) -> bool:
        """آیا کل شبانه‌روز آزاد است؟"""
        return bool(self.cells) and all(cell.status == STATUS_FREE for cell in self.cells)


@dataclass(frozen=True, slots=True)
class DayAvailability:
    """تصویر یک شبانه‌روز از کل زائرسرا."""

    day: date
    date_jalali: str
    weekday: str
    is_weekend: bool
    demand_level: str
    units: tuple[UnitAvailability, ...]
    free_cells: int
    total_cells: int

    @property
    def occupancy_rate(self) -> float:
        """نرخ اشغال روز (۰ تا ۱) — برای نمایش و آمار."""
        if not self.total_cells:
            return 0.0
        return round(1 - (self.free_cells / self.total_cells), 4)


def _status_for_cell(
    cell: Cell,
    *,
    reservations: list[Reservation],
    blocks: list[Block],
    unit_id: Any,
    now: datetime,
    staff: bool,
) -> CellAvailability:
    """تعیین وضعیت یک سلول بر پایهٔ رزروها و بلوک‌های همان روز."""
    status = STATUS_FREE
    detail = ""

    for reservation in reservations:
        if reservation.unit_id != unit_id:
            continue
        if reservation.starts_at < cell.end and reservation.ends_at > cell.start:
            status = STATUS_OCCUPIED
            detail = reservation.code if staff else ""
            break
        if (
            reservation.block_period.lower < cell.end
            and reservation.block_period.upper > cell.start
        ):
            status = STATUS_BUFFER
            detail = "بافر نظافت" if staff else ""
            break

    if status == STATUS_FREE:
        for block in blocks:
            if block.unit_id is not None and block.unit_id != unit_id:
                continue
            lower, upper = block.period.lower, block.period.upper
            if lower < cell.end and upper > cell.start:
                status = STATUS_BLOCKED
                detail = block.reason if staff else str(block.get_kind_display())
                break

    if cell.end <= now:
        status = STATUS_PAST
        detail = ""

    return CellAvailability(
        index=cell.index,
        kind=cell.kind,
        label=cell.label,
        start=cell.start,
        end=cell.end,
        status=status,
        detail=detail,
    )


def _window(start_day: date, days: int, config: LatticeConfig) -> tuple[datetime, datetime]:
    """پنجرهٔ زمانی ماتریس — از آغاز نخستین سلول تا پایان آخرین سلول."""
    first = cells_for_day(start_day, config)[0]
    last_day = start_day + timedelta(days=days - 1)
    last = cells_for_day(last_day, config)[-1]
    return first.start, last.end


def _build_matrix(
    *, start_day: date, days: int, staff: bool, config: LatticeConfig
) -> list[DayAvailability]:
    """ساخت ماتریس موجودی از دیتابیس (بدون کش) — تنها دو کوئری برای کل ماتریس."""
    window_start, window_end = _window(start_day, days, config)
    now = timezone.now()
    units = list(active_units())
    reservations = list(
        Reservation.objects.filter(
            status__in=ReservationStatus.active_statuses(),
            block_period__overlap=Range(window_start, window_end),
        ).order_by("stay_period")
    )
    blocks = list(blocks_overlapping(start=window_start, end=window_end))
    demand = demand_level_by_day(start_day, start_day + timedelta(days=days - 1))

    matrix: list[DayAvailability] = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        unit_rows: list[UnitAvailability] = []
        for unit in units:
            cells = tuple(
                _status_for_cell(
                    cell,
                    reservations=reservations,
                    blocks=blocks,
                    unit_id=unit.id,
                    now=now,
                    staff=staff,
                )
                for cell in cells_for_day(day, config)
            )
            unit_rows.append(
                UnitAvailability(
                    code=unit.code,
                    title=unit.title,
                    floor_number=unit.floor_number,
                    unit_type=unit.unit_type,
                    capacity_total=unit.capacity_total,
                    cells=cells,
                )
            )
        total_cells = sum(len(row.cells) for row in unit_rows)
        free_cells = sum(row.free_cells for row in unit_rows)
        matrix.append(
            DayAvailability(
                day=day,
                date_jalali=format_jalali(day, numeric=True),
                weekday=weekday_name(day),
                is_weekend=is_weekend(day),
                demand_level=demand.get(day, DemandLevel.NORMAL),
                units=tuple(unit_rows),
                free_cells=free_cells,
                total_cells=total_cells,
            )
        )
    return matrix


def availability_matrix(
    *, start_day: date, days: int = 14, staff: bool = False, use_cache: bool = True
) -> list[DayAvailability]:
    """ماتریس موجودی با کش نسخه‌دار (پیش‌فرض: دو هفته)."""
    if days < 1 or days > MAX_MATRIX_DAYS:
        msg = f"تعداد روزهای ماتریس باید بین ۱ و {MAX_MATRIX_DAYS} باشد."
        raise ValueError(msg)
    config = lattice_config()
    if not use_cache:
        return _build_matrix(start_day=start_day, days=days, staff=staff, config=config)
    scope = SCOPE_STAFF if staff else SCOPE_PUBLIC
    return get_or_set_versioned(
        AVAILABILITY_NAMESPACE,
        scope,
        f"{start_day.isoformat()}:{days}",
        lambda: _build_matrix(start_day=start_day, days=days, staff=staff, config=config),
        timeout=MATRIX_TTL_SECONDS,
    )


def invalidate_availability() -> None:
    """ابطال فوری تقویم — پس از هر تغییر رزرو/بلوک فراخوانی می‌شود."""
    invalidate(AVAILABILITY_NAMESPACE, SCOPE_PUBLIC)
    invalidate(AVAILABILITY_NAMESPACE, SCOPE_STAFF)


def is_unit_free(*, unit_id: Any, start: datetime, end: datetime) -> bool:
    """آیا این واحد در بازهٔ داده‌شده هیچ رزرو فعال یا بلوکی ندارد؟"""
    if Reservation.objects.filter(
        unit_id=unit_id,
        status__in=ReservationStatus.active_statuses(),
        block_period__overlap=Range(start, end),
    ).exists():
        return False
    overlapping = Block.objects.filter(
        is_active=True,
        period__overlap=Range(start, end),
    ).filter(unit_or_global_q(unit_id))
    return not overlapping.exists()


def unit_or_global_q(unit_id: Any) -> Q:
    """شرط «بلوک همین واحد یا بلوک سراسری» — یک تعریف، چند مصرف."""
    return Q(unit_id=unit_id) | Q(unit__isnull=True)


def free_units_for(*, start: datetime, end: datetime) -> list[Unit]:
    """واحدهای کاملاً آزاد در یک بازهٔ مشخص."""
    return [unit for unit in active_units() if is_unit_free(unit_id=unit.id, start=start, end=end)]


def alternative_periods(
    *,
    unit: Unit,
    start: datetime,
    end: datetime,
    config: LatticeConfig | None = None,
    limit: int = MAX_ALTERNATIVES,
    search_days: int = SEARCH_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """پیشنهاد بازه‌های جایگزین پس از تصادم — «چه چیزی الان آزاد است؟».

    جست‌وجو: همان طول اقامت، دو هفته قبل تا دو هفته بعد، ابتدا روی همان واحد و
    سپس همهٔ واحدهای فعال (به‌ترتیب نزدیکی زمانی). نتیجه: کاربر پیام «نشد»
    نمی‌بیند، انتخاب می‌بیند — همان‌طور که در سناریوی S2 خواسته شده است.
    """
    cfg = config or lattice_config()
    hours = (end - start).total_seconds() / SECONDS_PER_HOUR
    cells = max(1, round(hours / cfg.cell_hours))
    base_index = cell_at_boundary(start, cfg).index
    horizon_index = cell_at_boundary(timezone.now(), cfg).index
    span = search_days * cfg.cells_per_day

    ordered_units = [unit, *[u for u in active_units() if u.id != unit.id]]
    shifts = sorted(range(-span, span + 1), key=lambda value: (abs(value), value))

    candidates: list[dict[str, Any]] = []
    for candidate_unit in ordered_units:
        for shift in shifts:
            index = base_index + shift
            if index < horizon_index:
                continue
            candidate_start, candidate_end = period_from_index(index, cells, cfg)
            if not is_unit_free(
                unit_id=candidate_unit.id, start=candidate_start, end=candidate_end
            ):
                continue
            candidates.append(
                {
                    "unit_code": candidate_unit.code,
                    "unit_title": candidate_unit.title,
                    "floor_number": candidate_unit.floor_number,
                    "start": candidate_start.isoformat(),
                    "end": candidate_end.isoformat(),
                    "date_jalali": format_jalali(
                        timezone.localtime(candidate_start).date(), numeric=True
                    ),
                    "is_same_unit": candidate_unit.id == unit.id,
                }
            )
            if len(candidates) >= limit:
                return candidates
    return candidates
