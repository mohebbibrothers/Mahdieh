"""تحلیل‌های مدیریتی — سمت خواندن (CQRS read side) بدون هیچ مدل تازه.

اصل: این ماژول **فقط می‌خواند**. هیچ‌جا نمی‌نویسد، هیچ کشی را باطل نمی‌کند و
هیچ رخدادی منتشر نمی‌کند؛ پس می‌توان آزادانه و بی‌نگرانی آن را روی دادهٔ تولید
اجرا کرد. اگر روزی حجم داده بالا رفت، تنها همین ماژول به یک انبار داده منتقل
می‌شود — بدون لمس دامنه.

پرسش‌هایی که پاسخ می‌دهد:
    • نبض اشغال: نرخ اشغال روزبه‌روز، پرتقاضاترین و خالی‌ترین روزها.
    • کارنامهٔ واحدها: کدام طبقه/واحد بیشترین بازده را دارد؟
    • پروفایل هفته: کدام روزهای هفته پرتقاضاترند؟
    • کیفیت مهمانان: نرخ عدم‌حضور و لغو، مهمانان بازگشتی.
    • فشار پیش‌رو: روزهای کاملاً خالی و روزهای در آستانهٔ پرشدن.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

from django.db.models import Count, Q, Sum
from django.utils import timezone
from psycopg.types.range import Range
import structlog

from apps.core.jalali import format_jalali, weekday_name
from apps.inventory.lattice import LatticeConfig, cells_for_day, cells_spanning
from apps.inventory.selectors import active_units
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Reservation
from apps.reservation.policy import lattice_config, policy_int

logger = structlog.get_logger(__name__)

#: مجموعهٔ تهی مشترک — تا در حلقه‌ها شیء تازه ساخته نشود.
EMPTY_CELLS: Final[frozenset[int]] = frozenset()

#: آستانه‌ای که از آن به بعد روز «پر» شمرده می‌شود (ریسک ازدحام).
TIGHT_RATE: Final[float] = 0.8

#: کمترین تعداد نمونهٔ تاریخی برای اعلام «اطمینان بالا» در پیش‌بینی.
MIN_FORECAST_SAMPLES: Final[int] = 4

#: کمترین رقم لازم شمارهٔ تلفن تا ماسک‌کردن معنا داشته باشد.
MIN_PHONE_DIGITS: Final[int] = 6

DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 366
WEEKDAYS = 7

#: چند روز تاریخ، مبنای پیش‌بینی «همان روز هفته» است (چهار هفته = چهار نمونه).
HISTORY_DAYS = 28


#: وضعیت‌هایی که «سانس را واقعاً گرفته‌اند» — مبنای سنجش اشغال.
#:
#: تفاوت ظریف اما مهم با ``active_statuses``: «پایان‌یافته» هم اشغال بوده است
#: (تاریخ زائرسرا)، ولی «لغو‌شده/منقضی/جانشین‌شده» سانس را آزاد کرده و «عدم‌حضور»
#: واحد را خالی گذاشته است — پس هیچ‌یک اشغال شمرده نمی‌شوند. عدم‌حضور جای خودش
#: را در گزارش کیفیت مهمان دارد، نه در نرخ اشغال.
OCCUPIED_STATUSES: Final[tuple[str, ...]] = (
    *ReservationStatus.active_statuses(),
    ReservationStatus.COMPLETED,
)


@dataclass(slots=True)
class OccupancyPoint:
    """یک نقطهٔ سری زمانی اشغال — «چند نیم‌روز از ظرفیت، واقعاً پر بود؟»

    نکتهٔ روش‌شناسی (چرا این‌گونه محاسبه می‌شود): نرخ اشغال از **رزروها**
    استخراج می‌شود، نه از شبکهٔ موجودی لحظه‌ای. اگر از موجودی می‌خواندیم، هر روز
    گذشته «پرشده» به نظر می‌رسید (چون سانس‌های گذشته دیگر آزاد نیستند) و روند
    تاریخی بی‌معنا می‌شد. این تفکیک، مرز میان «تاریخ» و «موجودی» است.
    """

    day: date
    occupied_cells: int
    total_cells: int

    @property
    def free_cells(self) -> int:
        """نیم‌روزهای بی‌رزرو در این روز."""
        return max(0, self.total_cells - self.occupied_cells)

    @property
    def occupied(self) -> int:
        """تعداد نیم‌روزهای اشغال‌شده."""
        return self.occupied_cells

    @property
    def rate(self) -> float:
        """نرخ اشغال (۰ تا ۱)."""
        return round(self.occupied_cells / self.total_cells, 4) if self.total_cells else 0.0

    @property
    def is_past(self) -> bool:
        """آیا روز در گذشته است؟ (تاریخ در برابر آینده)"""
        return self.day < timezone.localdate()

    @property
    def state(self) -> str:
        """حالت روز برای رابط کاربری: ``past`` / ``today`` / ``future``."""
        today = timezone.localdate()
        if self.day < today:
            return "past"
        return "today" if self.day == today else "future"

    @property
    def date_jalali(self) -> str:
        """تاریخ جلالی برای جدول‌ها و نمودار."""
        return format_jalali(self.day)

    @property
    def weekday(self) -> str:
        """نام روز هفته."""
        return weekday_name(self.day)


def _clip_window(start_day: date, days: int) -> tuple[date, int]:
    """اعتبارسنجی پنجرهٔ تحلیل و کوتاه‌کردن آن به افق دادهٔ موجود."""
    if days < 1:
        msg = "بازهٔ تحلیل باید دست‌کم یک روز باشد."
        raise ValueError(msg)
    days = min(days, MAX_WINDOW_DAYS)
    today = timezone.localdate()
    horizon = today + timedelta(days=policy_int("reservation.booking_horizon_days"))
    if start_day > horizon:  # پنجرهٔ آیندهٔ بی‌معنا ⇒ به امروز برگردان
        start_day = today
    return start_day, days


#: بازهٔ تاریخی که «پایان‌یافته» هم اشغال شمرده می‌شود؛ بقیهٔ وضعیت‌ها یا سانس را
#: آزاد کرده‌اند یا هرگز آن را نگرفته‌اند (``OCCUPIED_STATUSES``).


def window_bounds(start_day: date, days: int, config: LatticeConfig) -> tuple[datetime, datetime]:
    """مرزهای زمانی یک پنجرهٔ روزانه — از آغاز نخستین سلول تا پایان آخرین سلول."""
    first = cells_for_day(start_day, config)[0]
    last = cells_for_day(start_day + timedelta(days=days - 1), config)[-1]
    return first.start, last.end


def occupied_cells_by_unit(
    *, start_day: date, days: int, config: LatticeConfig
) -> dict[str, set[int]]:
    """کدام سلول‌های شبکه (به‌تفکیک واحد) در این پنجره رزرو شده‌اند؟

    خروجی یک «مجموعهٔ شمارهٔ سلول» برای هر واحد است؛ به همین دلیل شمارش روزانه
    بعدی فقط جست‌وجو در یک مجموعه است و نه مقایسهٔ بازه‌ای. یک کوئری، سپس
    محاسبهٔ خالص — بدون N+1.
    """
    start, end = window_bounds(start_day, days, config)
    rows = Reservation.objects.filter(
        status__in=OCCUPIED_STATUSES,
        stay_period__overlap=Range(start, end),
    ).values_list("unit__code", "stay_period")

    cells_by_unit: dict[str, set[int]] = defaultdict(set)
    for unit_code, period in rows:
        if period is None or getattr(period, "isempty", False):
            continue
        for cell in cells_spanning(period.lower, period.upper, config):
            cells_by_unit[str(unit_code)].add(cell.index)
    return cells_by_unit


def occupancy_series(
    *, start_day: date | None = None, days: int = DEFAULT_WINDOW_DAYS
) -> list[OccupancyPoint]:
    """سری زمانی اشغال روزبه‌روز — یک محاسبه، دو کاربرد (روند و فشار پیش‌رو)."""
    start_day, days = _clip_window(start_day or timezone.localdate(), days)
    config = lattice_config()
    unit_codes = [unit.code for unit in active_units()]
    cells_by_unit = occupied_cells_by_unit(start_day=start_day, days=days, config=config)

    points: list[OccupancyPoint] = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        day_cells = cells_for_day(day, config)
        occupied = sum(
            1
            for cell in day_cells
            for code in unit_codes
            if cell.index in cells_by_unit.get(code, EMPTY_CELLS)
        )
        points.append(
            OccupancyPoint(
                day=day,
                occupied_cells=occupied,
                total_cells=len(day_cells) * len(unit_codes),
            )
        )
    return points


def unit_day_matrix(
    *, start_day: date | None = None, days: int = DEFAULT_WINDOW_DAYS
) -> dict[str, Any]:
    """ماتریس واحد×روز نرخ اشغال — ورودی نقشهٔ حرارتی داشبورد."""
    start_day, days = _clip_window(start_day or timezone.localdate(), days)
    config = lattice_config()
    cells_by_unit = occupied_cells_by_unit(start_day=start_day, days=days, config=config)
    day_cells = [
        cells_for_day(start_day + timedelta(days=offset), config) for offset in range(days)
    ]

    rows: list[dict[str, Any]] = []
    for unit in active_units():
        used = cells_by_unit.get(unit.code, EMPTY_CELLS)
        rates = [
            round(
                sum(1 for cell in cells if cell.index in used) / len(cells),
                4,
            )
            if cells
            else 0.0
            for cells in day_cells
        ]
        rows.append(
            {
                "code": unit.code,
                "title": unit.title,
                "floor": unit.floor.number,
                "rates": rates,
                "average": round(sum(rates) / len(rates), 4) if rates else 0.0,
            }
        )
    return {
        "start_day": start_day,
        "days": days,
        "labels": [format_jalali(start_day + timedelta(days=offset)) for offset in range(days)],
        "rows": rows,
    }


def summary(*, start_day: date | None = None, days: int = DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    """خلاصهٔ مدیریتی: نرخ اشغال، روند نسبت به نیمهٔ پیشین، روزهای شاخص.

    پیش‌فرض پنجره، **گذشته** است (``days`` روز منتهی به امروز): نرخ اشغال یک
    شاخص تاریخی است و پنجرهٔ آینده دربارهٔ «آنچه رخ داده» چیزی نمی‌گوید. نمای
    آیندهٔ زائرسرا جای دیگری دارد (تقویم موجودی و «فشار پیش‌رو»).
    """
    end_day = timezone.localdate()
    start_day = start_day or (end_day - timedelta(days=days - 1))
    series = occupancy_series(start_day=start_day, days=days)
    if not series:
        return {
            "points": [],
            "occupancy": 0.0,
            "trend": 0.0,
            "best": None,
            "worst": None,
            "start_day": start_day,
            "end_day": end_day,
        }

    total = sum(point.total_cells for point in series)
    occupied = sum(point.occupied_cells for point in series)
    overall = round(occupied / total, 4) if total else 0.0

    half = max(1, len(series) // 2)
    first, second = series[:half], series[half:]
    first_rate = _rate_of(first)
    second_rate = _rate_of(second)
    trend = round(second_rate - first_rate, 4)

    ranked = sorted(series, key=lambda point: point.rate)
    fully_free = [point for point in series if point.occupied_cells == 0]
    return {
        "points": series,
        "occupancy": overall,
        "trend": trend,
        "first_half": first_rate,
        "second_half": second_rate,
        "busiest": ranked[-1],
        "quietest": ranked[0],
        "fully_free_days": fully_free,
        "days": len(series),
        "total_cells": total,
        "occupied_cells": occupied,
        "free_cells": total - occupied,
        "start_day": start_day,
        "end_day": end_day,
    }


def _rate_of(points: list[OccupancyPoint]) -> float:
    """نرخ اشغال یک بازهٔ نقطه‌ای."""
    total = sum(point.total_cells for point in points)
    occupied = sum(point.occupied_cells for point in points)
    return round(occupied / total, 4) if total else 0.0


def unit_utilization(
    *, days: int = DEFAULT_WINDOW_DAYS, start_day: date | None = None
) -> list[dict[str, Any]]:
    """کارنامهٔ واحدها: نیم‌روزهای رزروشده، سهم از کل و رتبه."""
    start_day, days = _clip_window(start_day or timezone.localdate(), days)
    start, end = window_bounds(start_day, days, lattice_config())

    units = list(active_units())
    totals: dict[str, int] = defaultdict(int)
    # هم‌پوشانی بازه، نه مقایسهٔ «ستون آغاز»: اقامتی که پیش از پنجره شروع شده هم
    # بخشی از ظرفیت این پنجره را مصرف کرده است، پس باید شمرده شود.
    rows_raw = list(
        Reservation.objects.filter(
            status__in=ReservationStatus.active_statuses(),
            stay_period__overlap=Range(start, end),
        )
        .values("unit__code")
        .annotate(cells=Sum("cells_count"))
    )
    for raw in rows_raw:
        totals[str(raw["unit__code"])] = int(raw["cells"] or 0)

    capacity_per_unit = days * 2 * 1  # دو نیم‌روز در هر شبانه‌روز، هر واحد
    grand_total = sum(totals.values()) or 1
    rows: list[dict[str, Any]] = []
    for unit in units:
        used = totals.get(unit.code, 0)
        rows.append(
            {
                "code": unit.code,
                "title": unit.title,
                "floor": unit.floor.number,
                "unit_type": unit.unit_type,
                "cells_used": used,
                "capacity": capacity_per_unit,
                "occupancy": min(1.0, round(used / capacity_per_unit, 4))
                if capacity_per_unit
                else 0.0,
                "share": round(used / grand_total, 4),
                "revenue_free": True,
            }
        )
    rows.sort(key=lambda row: (-int(row["cells_used"]), str(row["code"])))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def weekday_profile(*, days: int = 90) -> list[dict[str, Any]]:
    """پروفایل هفته: کدام روزهای هفته پرتقاضاترند (و کدام‌ها فرصت بازاریابی‌اند)."""
    start_day = timezone.localdate() - timedelta(days=days - 1)
    series = occupancy_series(start_day=start_day, days=days)
    buckets: dict[int, list[OccupancyPoint]] = defaultdict(list)
    for point in series:
        buckets[point.day.weekday()].append(point)

    rows: list[dict[str, Any]] = []
    for index in range(WEEKDAYS):
        points = buckets.get(index, [])
        anchor = start_day + timedelta(days=(index - start_day.weekday()) % 7)
        rows.append(
            {
                "weekday_index": index,
                "weekday": weekday_name(anchor),
                "samples": len(points),
                "occupancy": _rate_of(points) if points else 0.0,
                "average_occupied_cells": round(
                    sum(point.occupied_cells for point in points) / len(points), 2
                )
                if points
                else 0.0,
                "is_weekend": index in (3, 4),  # پنجشنبه/جمعه در گاه‌شماری ایرانی
            }
        )
    rows.sort(key=lambda row: -row["occupancy"])
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


def guest_quality(*, days: int = 90, limit: int = 12) -> dict[str, Any]:
    """کیفیت مهمانان: نرخ عدم‌حضور، لغو دیرهنگام و مهمانان بازگشتی."""
    since = timezone.now() - timedelta(days=days)
    scoped = Reservation.objects.filter(created_at__gte=since)
    totals = scoped.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status=ReservationStatus.COMPLETED)),
        no_show=Count("id", filter=Q(status=ReservationStatus.NO_SHOW)),
        cancelled=Count(
            "id",
            filter=Q(
                status__in=[
                    ReservationStatus.CANCELLED_BY_GUEST,
                    ReservationStatus.CANCELLED_BY_STAFF,
                ]
            ),
        ),
        late_cancel=Count("id", filter=Q(is_late_cancel=True)),
    )
    total = totals["total"] or 0
    repeat = (
        Reservation.objects.filter(created_at__gte=since)
        .values("guest_id")
        .annotate(visits=Count("id"))
        .filter(visits__gt=1)
        .count()
    )
    risky = (
        Reservation.objects.filter(created_at__gte=since, status=ReservationStatus.NO_SHOW)
        .values("guest__phone", "guest__first_name", "guest__last_name")
        .annotate(no_shows=Count("id"))
        .order_by("-no_shows")[:limit]
    )
    return {
        "total": total,
        "no_show": totals["no_show"],
        "no_show_rate": round((totals["no_show"] or 0) / total, 4) if total else 0.0,
        "cancel_rate": round((totals["cancelled"] or 0) / total, 4) if total else 0.0,
        "late_cancel_rate": round((totals["late_cancel"] or 0) / total, 4) if total else 0.0,
        "completion_rate": round((totals["completed"] or 0) / total, 4) if total else 0.0,
        "repeat_guests": repeat,
        "risky_guests": [
            {
                "name": f"{row['guest__first_name']} {row['guest__last_name']}".strip(),
                "phone_masked": _mask(row["guest__phone"]),
                "no_shows": row["no_shows"],
            }
            for row in risky
        ],
    }


def upcoming_pressure(*, days: int = 21) -> dict[str, Any]:
    """فشار پیش‌رو: روزهای کاملاً خالی (فرصت تبلیغ) و روزهای پر (ریسک ازدحام)."""
    start_day = timezone.localdate()
    series = occupancy_series(start_day=start_day, days=days)
    free_days = [point for point in series if point.occupied_cells == 0]
    tight_days = [point for point in series if point.rate >= TIGHT_RATE]
    return {
        "points": series,
        "empty_days": free_days[:10],
        "tight_days": tight_days[:10],
        "empty_count": len(free_days),
        "tight_count": len(tight_days),
        "average_lead_time_days": _avg_lead_time_days(),
        "start_day": start_day,
        "days": days,
    }


def _avg_lead_time_days() -> float:
    """میانگین فاصلهٔ «ثبت رزرو» تا «آغاز اقامت» — ورودی برنامه‌ریزی تبلیغ."""
    rows = (
        Reservation.objects.filter(
            status__in=ReservationStatus.active_statuses(),
            stay_period__startswith__gte=timezone.now() - timedelta(days=120),
        )
        .order_by("-created_at")
        .values_list("created_at", "stay_period")[:500]
    )
    if not rows:
        return 0.0
    deltas = [
        (period.lower - created_at).total_seconds() / 86400
        for created_at, period in rows
        if period is not None and period.lower is not None and period.lower > created_at
    ]
    return round(sum(deltas) / len(deltas), 1) if deltas else 0.0


def demand_forecast(*, days: int = 14) -> list[dict[str, Any]]:
    """پیش‌بینی ساده و صادقانه: میانگین متحرک اشغال همان روز هفته + روند.

    این «هوش مصنوعی» نیست و ادعایش هم نمی‌شود: میانگین وزنیِ سادهٔ چهار هفتهٔ
    گذشته برای همان روز هفته، با وزن بیشتر برای هفتهٔ نزدیک‌تر. برای تصمیم
    عملیاتی پذیرش کافی است و قابل توضیح می‌ماند.
    """
    history = occupancy_series(
        start_day=timezone.localdate() - timedelta(days=HISTORY_DAYS - 1), days=HISTORY_DAYS
    )
    by_weekday: dict[int, list[float]] = defaultdict(list)
    for point in history:
        by_weekday[point.day.weekday()].append(point.rate)

    forecast: list[dict[str, Any]] = []
    tomorrow = timezone.localdate() + timedelta(days=1)
    for offset in range(days):
        day = tomorrow + timedelta(days=offset)
        samples = by_weekday.get(day.weekday(), [])
        if samples:
            weights = [index + 1 for index in range(len(samples))]
            estimate = sum(value * weight for value, weight in zip(samples, weights, strict=True))
            estimate /= sum(weights)
        else:
            estimate = 0.0
        forecast.append(
            {
                "day": day,
                "date_jalali": format_jalali(day),
                "weekday": weekday_name(day),
                "expected_occupancy": round(estimate, 4),
                "confidence": "بالا" if len(samples) >= MIN_FORECAST_SAMPLES else "پایین",
                "samples": len(samples),
            }
        )
    return forecast


def _mask(phone: str) -> str:
    """پوشاندن شمارهٔ تلفن در تحلیل‌ها (سیاست PII)."""
    digits = "".join(char for char in phone if char.isdigit())
    if len(digits) < MIN_PHONE_DIGITS:
        return "***"
    return f"{digits[:4]}***{digits[-2:]}"


def dashboard(*, start_day: date | None = None, days: int = DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    """همهٔ تحلیل‌ها در یک ساختار — برای صفحهٔ داشبورد و API.

    این تابع «یک بار بخوان، همه‌جا نشان بده» است: هیچ شاخصی جداگانه دوباره
    محاسبه نمی‌شود و همهٔ بخش‌ها به یک پنجرهٔ زمانی اشاره می‌کنند، پس اعداد صفحه
    هرگز با هم ناسازگار به نظر نمی‌رسند.
    """
    data = summary(start_day=start_day, days=days)
    data.update(
        {
            "unit_utilization": unit_utilization(days=days, start_day=data["start_day"]),
            "weekday_profile": weekday_profile(days=max(days, HISTORY_DAYS)),
            "guest_quality": guest_quality(days=max(days, 90)),
            "pressure": upcoming_pressure(days=days),
            "forecast": demand_forecast(),
            "planning": unit_day_matrix(start_day=timezone.localdate(), days=days),
            "window_days": days,
        }
    )
    logger.info(
        "analytics_dashboard_built",
        days=days,
        points=len(data["points"]),
        start_day=data["start_day"].isoformat(),
    )
    return data
