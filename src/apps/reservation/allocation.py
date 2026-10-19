"""موتور تخصیص — «کدام بازه/واحد برای این زائر بهترین است؟»

دو کاربرد:
    ۱) **پیشنهاد هوشمند** به زائر وقتی بازهٔ دلخواهش ممکن نیست: چند گزینهٔ
       رتبه‌بندی‌شده با دلیل («چرا این گزینه اول است؟»).
    ۲) **قواعد ایام پیک** (``AllocationRule``): سقف نیم‌روز در ایام پرتقاضا،
       الزام تأیید مدیر، و عدالت چرخشی میان زائران (پیش‌فرض: خاموش).

اصل طراحی: این ماژول **هیچ چیزی نمی‌نویسد**؛ فقط می‌خواند و رتبه می‌دهد.
هر تصمیم نوشتنی همچنان از ``services`` می‌گذرد.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final

from django.db.models import Sum
from django.utils import timezone
import structlog

from apps.inventory.lattice import (
    cell_at_boundary,
    count_cells,
    index_to_cell,
    local_zone,
)
from apps.inventory.models import Unit
from apps.inventory.selectors import active_units, occasions_between
from apps.reservation import selectors
from apps.reservation.availability import free_units_for
from apps.reservation.models import AllocationRule
from apps.reservation.policy import lattice_config, policy_int

logger = structlog.get_logger(__name__)

MAX_CANDIDATES: Final[int] = 6
#: آستانه‌های تفسیر «تناسب ظرفیت»: از این‌جا به بعد، واحد برای گروه بزرگ است.
GENEROUS_FIT: float = 0.5
LOOSE_FIT: float = 0.4

#: آستانهٔ اشاره به ایام پرتقاضا در متن دلیل‌ها.
PEAK_PRESSURE: float = 0.6

SHIFT_STEPS: Final[tuple[int, ...]] = (0, 1, -1, 2, -2)


@dataclass(slots=True)
class AllocationOption:
    """یک گزینهٔ پیشنهادی با دلیل رتبه‌بندی."""

    unit_code: str
    unit_title: str
    unit_id: Any
    floor_number: int
    capacity_total: int
    start: datetime
    end: datetime
    cells: int
    score: float
    reasons: list[str] = field(default_factory=list)
    is_same_unit: bool = False
    shifts: int = 0

    @property
    def date_jalali(self) -> str:
        """تاریخ جلالی آغاز برای نمایش."""
        from apps.core.jalali import format_jalali

        return format_jalali(timezone.localtime(self.start))

    def as_dict(self) -> dict[str, Any]:
        """نمایش دیکشنری برای API و قالب."""
        return {
            "unit_code": self.unit_code,
            "unit_title": self.unit_title,
            "floor_number": self.floor_number,
            "capacity_total": self.capacity_total,
            "cells": self.cells,
            "score": round(self.score, 3),
            "reasons": list(self.reasons),
            "is_same_unit": self.is_same_unit,
            "shifts": self.shifts,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "date_jalali": self.date_jalali,
        }


def _day_pressure(day: date) -> float:
    """فشار تقاضای یک روز (۰ تا ۱).

    از ``availability_matrix`` استفاده می‌کنیم، نه یک پرس‌وجوی موازی: هر
    تعریف دومی از «اشغال» دیر یا زود واگرا می‌شود. کش خاموش است چون این
    محاسبه در مسیر رتبه‌بندی گزینه‌ها چند بار تکرار می‌شود.
    """
    from apps.reservation.availability import availability_matrix

    window = availability_matrix(start_day=day, days=1, use_cache=False)
    return round(window[0].occupancy_rate, 4) if window else 0.0


def _occasions_hint(start: datetime, end: datetime) -> set[str]:
    """نام مناسبت‌های پیک در بازه (برای امتیاز منفی و شفافیت)."""
    first = timezone.localtime(start).date()
    last = timezone.localtime(end - timedelta(seconds=1)).date()
    return {occasion.title for occasion in occasions_between(first, last)}


def _score_option(
    *,
    unit: Unit,
    start: datetime,
    end: datetime,
    cells: int,
    guests_count: int,
    preferred_unit_id: Any | None,
    shifts: int,
) -> tuple[float, list[str]]:
    """امتیاز و دلیل‌های یک گزینه — قاعده‌محور و قابل توضیح.

    معیارها (وزن‌ها آگاهانه و مستند):
        • نزدیکی به بازهٔ درخواستی (کمترین جابه‌جایی روزانه) — ۳٫۰ به‌ازای هر روز.
        • همان واحد درخواستی — ۲٫۵.
        • تناسب ظرفیت با تعداد نفرات (اتلاف ظرفیت = جریمه) — تا ۲٫۰.
        • فشار تقاضای روزهای بازه (ایام پیک) — تا ۱٫۵ جریمه.
        • طول سانس کوتاه‌تر (خیر عمومی) — تا ۱٫۰.
    """
    reasons: list[str] = []
    score = 0.0

    day_shift = abs(shifts)
    score -= 3.0 * day_shift
    if day_shift == 0:
        reasons.append("همان روز درخواستی")
    else:
        direction = "بعد" if shifts > 0 else "قبل"
        reasons.append(f"{day_shift} نیم‌روز {direction} بازهٔ دلخواه")

    if preferred_unit_id is not None and unit.id == preferred_unit_id:
        score += 2.5
        reasons.append("همان واحد مورد نظر شما")

    capacity = unit.capacity_total
    fit = guests_count / capacity if capacity else 0.0
    score += 2.0 * fit
    if capacity and guests_count > capacity:  # pragma: no cover - نگهبان زودهنگام
        score -= 5.0
    if GENEROUS_FIT <= fit <= 1.0:  # type: ignore[operator]
        reasons.append("ظرفیت مناسب تعداد نفرات")
    elif fit < LOOSE_FIT:
        reasons.append("ظرفیت بزرگ‌تر از نیاز شما")

    start_day = timezone.localtime(start).date()
    end_day = timezone.localtime(end - timedelta(seconds=1)).date()
    days = (end_day - start_day).days + 1
    pressure = sum(_day_pressure(start_day + timedelta(days=offset)) for offset in range(days))
    pressure = pressure / days if days else 0.0
    score -= 1.5 * pressure
    if pressure >= PEAK_PRESSURE:
        reasons.append("ایام پرتقاضا — این گزینه کمیاب است")

    occasions = _occasions_hint(start, end)
    if occasions:
        reasons.append("هم‌زمان با " + "، ".join(sorted(occasions)[:2]))

    score -= 0.15 * cells
    return round(score, 3), reasons


def suggest_options(
    *,
    unit: Unit | None,
    start: datetime,
    end: datetime,
    guests_count: int,
    limit: int = MAX_CANDIDATES,
    include_shifts: bool = True,
) -> list[AllocationOption]:
    """گزینه‌های جانشین رتبه‌بندی‌شده برای یک درخواست.

    جست‌وجو در دو محور:
        • **واحد:** همهٔ واحدهای فعال (با اولویت واحد درخواستی).
        • **زمان:** همان بازه، و بازه‌های جابه‌جاشده به اندازهٔ نیم‌روز (روز/شب)
          تا دو روز قبل و بعد — چون ۹۰٪ ناکامی‌ها با یک نیم‌روز جابه‌جایی حل می‌شود.
    """
    config = lattice_config()
    requested_cells = count_cells(start, end, config)
    units = list(active_units())
    options: list[AllocationOption] = []
    seen: set[tuple[str, str]] = set()

    shifts = SHIFT_STEPS if include_shifts else (0,)
    for shift in shifts:
        start_index = cell_at_boundary(start, config).index
        candidate_start = index_to_cell(start_index + shift, config).start
        candidate_end = index_to_cell(start_index + shift + requested_cells, config).start
        if candidate_start < timezone.now() + timedelta(
            hours=policy_int("reservation.min_lead_time_hours")
        ):
            continue
        free_units = {
            unit.id: unit for unit in free_units_for(start=candidate_start, end=candidate_end)
        }
        for candidate_unit in units:
            if candidate_unit.id not in free_units:
                continue
            if guests_count > candidate_unit.capacity_total:
                continue
            key = (str(candidate_unit.id), candidate_start.isoformat())
            if key in seen:
                continue
            seen.add(key)
            score, reasons = _score_option(
                unit=candidate_unit,
                start=candidate_start,
                end=candidate_end,
                cells=requested_cells,
                guests_count=guests_count,
                preferred_unit_id=unit.id if unit else None,
                shifts=shift,
            )
            options.append(
                AllocationOption(
                    unit_code=candidate_unit.code,
                    unit_title=candidate_unit.title,
                    unit_id=candidate_unit.id,
                    floor_number=candidate_unit.floor.number,
                    capacity_total=candidate_unit.capacity_total,
                    start=candidate_start,
                    end=candidate_end,
                    cells=requested_cells,
                    score=score,
                    reasons=reasons,
                    is_same_unit=bool(unit and candidate_unit.id == unit.id),
                    shifts=shift,
                )
            )

    options.sort(key=lambda option: (-option.score, option.shifts, option.unit_code))
    logger.debug("allocation_options_built", count=len(options), limit=limit)
    return options[:limit]


def peak_limit_for(*, guest: Any, start: datetime, end: datetime) -> int | None:
    """سقف نیم‌روز در ایام پیک برای یک زائر (یا ``None`` اگر قاعده‌ای فعال نیست).

    قواعد تخصیص **پیش‌فرض خاموش‌اند**؛ مدیر در ایام خاص (مثلاً دههٔ آخر صفر)
    آن‌ها را روشن می‌کند. قاعدهٔ «عدالت چرخشی» به زائری که در ایام پیک سهم
    بیشتری برده، سقف کمتری می‌دهد تا نوبت به دیگران هم برسد.
    """
    rules = list(AllocationRule.objects.filter(is_active=True).order_by("sort_order"))
    if not rules:
        return None

    first_day = timezone.localtime(start).date()
    last_day = timezone.localtime(end - timedelta(seconds=1)).date()
    window_occasions = list(occasions_between(first_day, last_day))
    titles = {occasion.title for occasion in window_occasions}
    levels = {str(occasion.demand_level) for occasion in window_occasions}

    limit: int | None = None
    for rule in rules:
        if rule.applies_to_occasion and rule.applies_to_occasion not in titles:
            continue
        if rule.starts_on and first_day < rule.starts_on:
            continue
        if rule.ends_on and last_day > rule.ends_on:
            continue
        if rule.min_demand_level and rule.min_demand_level not in levels:
            continue
        allowance = rule.max_cells
        if rule.rotation_fairness and guest is not None:
            used = _peak_usage(guest=guest, since=first_day - timedelta(days=90))
            allowance = max(rule.min_cells, rule.max_cells - used)
        limit = allowance if limit is None else min(limit, allowance)
    return limit


def _peak_usage(*, guest: Any, since: date) -> int:
    """مجموع نیم‌روزهای رزروشدهٔ یک زائر از یک تاریخ (برای عدالت چرخشی).

    فیلتر روی آغاز اقامت (``starts_at``) است که ایندکس ``res_status_stay_idx``
    دارد؛ اقامتی که پیش از ``since`` آغاز شده در سهم دورهٔ قبل حساب شده است.
    """
    start = datetime.combine(since, datetime.min.time(), tzinfo=local_zone())
    total = (
        selectors.reservations_for_user(guest, scope="all")
        .filter(starts_at__gte=start)
        .aggregate(total=Sum("cells_count"))["total"]
    )
    return int(total or 0)


def allocation_plan(
    *,
    unit: Unit | None,
    start: datetime,
    end: datetime,
    guests_count: int,
    limit: int = MAX_CANDIDATES,
) -> dict[str, Any]:
    """برنامهٔ تخصیص کامل: گزینه‌ها + سقف پیک + هشدارها."""
    options = suggest_options(
        unit=unit, start=start, end=end, guests_count=guests_count, limit=limit
    )
    peak_limit = peak_limit_for(guest=None, start=start, end=end)
    requested_cells = count_cells(start, end, lattice_config())
    return {
        "requested_cells": requested_cells,
        "options": options,
        "peak_limit": peak_limit,
        "blocked_by_peak": bool(peak_limit is not None and requested_cells > peak_limit),
    }
