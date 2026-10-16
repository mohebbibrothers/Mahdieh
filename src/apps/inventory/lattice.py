"""شبکهٔ نیم‌روز — ریاضیات خالص زمان در مهدیه.

چرا این ماژول از دیتابیس و مدل جدا است؟
    • **قابل تست بودن بدون دیتابیس**: صدها حالت مرزی (نوروز، نیمه‌شب، بافر
      نظافت) بدون ساخت هیچ رکوردی قابل بررسی است.
    • **یک منبع حقیقت**: «نیم‌روز» در رزرو، تقویم، اکسل و آزمون‌ها یکسان محاسبه
      می‌شود؛ هیچ‌جای دیگر مدت اقامت محاسبه نمی‌شود.

مدل زمان (بخش ۳.۲ سند دامنه):
    سلول روزانه:  D 08:00 → D 20:00
    سلول شبانه:   D 20:00 → D+1 08:00
    هر رزرو = بازهٔ پیوستهٔ ``[k, k+N)`` سلول، 1 ≤ N ≤ 14
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Final
from zoneinfo import ZoneInfo

from django.conf import settings

from apps.core.text import to_persian_digits

DEFAULT_GRID_START_HOUR: Final[int] = 8
DEFAULT_CELL_HOURS: Final[int] = 12
DEFAULT_CLEANING_BUFFER_MINUTES: Final[int] = 60
DEFAULT_CHECKIN_HOUR: Final[int] = 14
DEFAULT_CHECKOUT_HOUR: Final[int] = 12
HOURS_PER_DAY: Final[int] = 24
MINUTES_PER_HOUR: Final[int] = 60

CELL_KIND_DAY: Final[str] = "day"
CELL_KIND_NIGHT: Final[str] = "night"


@lru_cache(maxsize=4)
def _zone(name: str) -> ZoneInfo:
    """منطقهٔ زمانی از کش — ساخت ZoneInfo هزینه‌بر است."""
    return ZoneInfo(name)


def local_zone() -> ZoneInfo:
    """منطقهٔ زمانی محلی زائرسرا (Asia/Tehran در تولید)."""
    return _zone(str(settings.TIME_ZONE))


@dataclass(frozen=True, slots=True)
class LatticeConfig:
    """پیکربندی شبکه — در تولید از جدول Policy خوانده می‌شود، در تست صریح."""

    grid_start_hour: int = DEFAULT_GRID_START_HOUR
    cell_hours: int = DEFAULT_CELL_HOURS
    cleaning_buffer_minutes: int = DEFAULT_CLEANING_BUFFER_MINUTES
    checkin_hour: int = DEFAULT_CHECKIN_HOUR
    checkout_hour: int = DEFAULT_CHECKOUT_HOUR

    def __post_init__(self) -> None:
        """اعتبارسنجی: طول سلول باید شبانه‌روز را بی‌باقی‌مانده تقسیم کند."""
        if self.cell_hours <= 0 or HOURS_PER_DAY % self.cell_hours != 0:
            msg = f"طول سلول ({self.cell_hours} ساعت) باید مقسوم‌علیه ۲۴ باشد."
            raise ValueError(msg)
        if not 0 <= self.grid_start_hour < HOURS_PER_DAY:
            msg = "ساعت آغاز شبکه باید در بازهٔ ۰ تا ۲۳ باشد."
            raise ValueError(msg)
        if self.cleaning_buffer_minutes < 0:
            msg = "بافر نظافت نمی‌تواند منفی باشد."
            raise ValueError(msg)

    @property
    def cells_per_day(self) -> int:
        """تعداد سلول در شبانه‌روز (پیش‌فرض: ۲ — روزانه و شبانه)."""
        return HOURS_PER_DAY // self.cell_hours


DEFAULT_LATTICE = LatticeConfig()


@dataclass(frozen=True, slots=True)
class Cell:
    """یک نیم‌روز — کوچک‌ترین واحد قابل‌رزرو موجودی."""

    index: int
    start: datetime
    end: datetime
    kind: str
    day: date

    @property
    def label(self) -> str:
        """برچسب انسانی سلول: «روزانه» یا «شبانه»."""
        return "روزانه" if self.kind == CELL_KIND_DAY else "شبانه"


def cell_start_offset(offset: int, config: LatticeConfig) -> int:
    """ساعت آغاز سلول ``offset`` با احتساب ساعت شروع شبکه."""
    return (config.grid_start_hour + offset * config.cell_hours) % HOURS_PER_DAY


def cell_bounds(
    day: date, offset: int, config: LatticeConfig = DEFAULT_LATTICE
) -> tuple[datetime, datetime]:
    """مرزهای زمانی یک سلول در یک روز (آگاه به منطقهٔ زمانی).

    اگر ساعت پایان از ساعت آغاز کوچک‌تر باشد، سلول از نیمه‌شب عبور کرده است
    (سلول شبانه) و روز پایان یک روز جلوتر می‌رود.
    """
    zone = local_zone()
    per_day = config.cells_per_day
    start_hour = cell_start_offset(offset, config)
    end_hour = cell_start_offset((offset + 1) % per_day, config)
    start = datetime.combine(day, time(hour=start_hour), tzinfo=zone)
    end_day = day if end_hour > start_hour else day + timedelta(days=1)
    end = datetime.combine(end_day, time(hour=end_hour), tzinfo=zone)
    return start, end


def cells_for_day(day: date, config: LatticeConfig = DEFAULT_LATTICE) -> tuple[Cell, ...]:
    """سلول‌های یک شبانه‌روز (پیش‌فرض: روزانه + شبانه)."""
    per_day = config.cells_per_day
    cells: list[Cell] = []
    for offset in range(per_day):
        start, end = cell_bounds(day, offset, config)
        cells.append(
            Cell(
                index=cell_index(day, offset, config),
                start=start,
                end=end,
                kind=CELL_KIND_DAY if offset == 0 else CELL_KIND_NIGHT,
                day=day,
            )
        )
    return tuple(cells)


def cell_index(day: date, offset: int, config: LatticeConfig = DEFAULT_LATTICE) -> int:
    """شمارهٔ مطلق (یکنوا) یک سلول — مبنای بازهٔ رزرو و مقایسه‌ها."""
    return day.toordinal() * config.cells_per_day + offset


def index_to_cell(index: int, config: LatticeConfig = DEFAULT_LATTICE) -> Cell:
    """تبدیل شمارهٔ مطلق به سلول (روز + مرزها)."""
    per_day = config.cells_per_day
    ordinal, offset = divmod(index, per_day)
    day = date.fromordinal(ordinal)
    start, end = cell_bounds(day, offset, config)
    return Cell(
        index=index,
        start=start,
        end=end,
        kind=CELL_KIND_DAY if offset == 0 else CELL_KIND_NIGHT,
        day=day,
    )


def is_grid_boundary(moment: datetime, config: LatticeConfig = DEFAULT_LATTICE) -> bool:
    """آیا این لحظه دقیقاً روی مرز شبکه است؟"""
    local = moment.astimezone(local_zone())
    if local.minute or local.second or local.microsecond:
        return False
    per_day = config.cells_per_day
    return any(local.hour == cell_start_offset(offset, config) for offset in range(per_day))


def is_on_grid(start: datetime, end: datetime, config: LatticeConfig = DEFAULT_LATTICE) -> bool:
    """آیا بازه دقیقاً روی مرز سلول‌ها نشسته است (قاعدهٔ A1)؟"""
    return start < end and is_grid_boundary(start, config) and is_grid_boundary(end, config)


def cells_spanning(
    start: datetime, end: datetime, config: LatticeConfig = DEFAULT_LATTICE
) -> list[Cell]:
    """سلول‌هایی که بازه را «فرا می‌گیرند» — حتی اگر بازه روی مرز نباشد.

    کاربرد: نمایش تقویم و محاسبهٔ هم‌پوشانی؛ برای اعتبارسنجی از ``is_on_grid``
    استفاده می‌شود، نه این تابع.
    """
    if end <= start:
        return []
    first_ordinal = start.astimezone(local_zone()).date().toordinal() - 1
    last_ordinal = end.astimezone(local_zone()).date().toordinal() + 1
    cells: list[Cell] = []
    for ordinal in range(first_ordinal, last_ordinal + 1):
        for cell in cells_for_day(date.fromordinal(ordinal), config):
            if cell.start < end and cell.end > start:
                cells.append(cell)
    return cells


def cell_at_boundary(moment: datetime, config: LatticeConfig = DEFAULT_LATTICE) -> Cell:
    """سلولی که در این مرز **آغاز** می‌شود.

    نکتهٔ ظریف: مرز پایان یک رزرو (مثلاً ۰۸:۰۰ صبح) هم‌زمان آغاز سلول بعدی
    است؛ پس تعداد نیم‌روز = «سلول پایان منهای سلول آغاز» بدون جمع یک.
    """
    local = moment.astimezone(local_zone())
    offset = (local.hour - config.grid_start_hour) % HOURS_PER_DAY // config.cell_hours
    return index_to_cell(cell_index(local.date(), offset, config), config)


def count_cells(start: datetime, end: datetime, config: LatticeConfig = DEFAULT_LATTICE) -> int:
    """تعداد نیم‌روزهای یک بازه؛ اگر بازه روی شبکه نباشد، ``ValueError``."""
    if not is_on_grid(start, end, config):
        msg = "بازه روی مرزهای شبکهٔ نیم‌روز نیست."
        raise ValueError(msg)
    return cell_at_boundary(end, config).index - cell_at_boundary(start, config).index


def period_from_index(
    start_index: int, cells: int, config: LatticeConfig = DEFAULT_LATTICE
) -> tuple[datetime, datetime]:
    """ساخت بازهٔ زمانی از شمارهٔ سلول آغاز و تعداد نیم‌روز."""
    if cells < 1:
        msg = "تعداد نیم‌روز باید حداقل ۱ باشد."
        raise ValueError(msg)
    first = index_to_cell(start_index, config)
    last = index_to_cell(start_index + cells - 1, config)
    return first.start, last.end


def snap_forward(moment: datetime, config: LatticeConfig = DEFAULT_LATTICE) -> datetime:
    """گرد کردن یک لحظه به «مرز بعدی» شبکه (برای پیشنهاد بازهٔ جایگزین)."""
    local = moment.astimezone(local_zone())
    candidate = cell_at_boundary(local, config)
    if candidate.start >= local:
        return candidate.start
    return index_to_cell(candidate.index + 1, config).start


def with_cleaning_buffer(
    start: datetime, end: datetime, *, minutes: int | None = None
) -> tuple[datetime, datetime]:
    """بازهٔ مسدودکننده = اقامت ± بافر نظافت/تحویل (قاعدهٔ A3)."""
    buffer_minutes = DEFAULT_CLEANING_BUFFER_MINUTES if minutes is None else max(0, minutes)
    delta = timedelta(minutes=buffer_minutes)
    return start - delta, end + delta


def duration_hours(start: datetime, end: datetime) -> float:
    """طول بازه به ساعت — برای نمایش و گزارش."""
    return (end - start).total_seconds() / (MINUTES_PER_HOUR * MINUTES_PER_HOUR)


def stay_label(cells: int) -> str:
    """برچسب فارسی مدت اقامت بر پایهٔ تعداد نیم‌روز — با ارقام فارسی.

    چرا ارقام فارسی این‌جا و نه در لایهٔ نمایش؟ این تابع خودش «برچسب فارسی»
    است («نصف روز»، «شبانه‌روز کامل»)؛ اگر عددش لاتین بماند، خروجی دو خطِ
    متفاوت می‌شود و همان متن در پیامک، گواهی و گزارش اکسل تکرار می‌شود.
    """
    days = cells / 2
    if cells == 1:
        return "نصف روز"
    if days == int(days):
        whole = int(days)
        return "یک شبانه‌روز کامل" if whole == 1 else f"{to_persian_digits(whole)} شبانه‌روز کامل"
    return f"{to_persian_digits(cells)} نیم‌روز"
