"""پرس‌وجوهای خواندنی موجودی — بدون هیچ اثر جانبی.

قاعده: نماها و سرویس‌ها برای خواندن، فقط از این ماژول استفاده می‌کنند تا
«کوئری صحیح» (با ``select_related``/``prefetch_related`` درست) جای واحدی داشته
باشد و N+1 در هیچ نقطه‌ای تکرار نشود.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db.models import Prefetch, QuerySet

from apps.core.periods import overlap_q
from apps.inventory.choices import DemandLevel
from apps.inventory.lattice import local_zone
from apps.inventory.models import Block, Floor, Occasion, Unit, UnitFeature, UnitFeatureLink


def _feature_links() -> QuerySet[UnitFeatureLink]:
    """پیوندهای امکانات با امکان مربوطه (برای ``Prefetch``)."""
    return UnitFeatureLink.objects.select_related("feature").order_by(
        "-feature__is_highlight", "feature__sort_order"
    )


def active_units() -> QuerySet[Unit]:
    """واحدهای قابل‌رزرو با طبقه و امکانات (بدون N+1)."""
    return (
        Unit.objects.filter(is_active=True)
        .select_related("floor")
        .prefetch_related(Prefetch("feature_links", queryset=_feature_links()))
        .order_by("floor__sort_order", "floor__number", "sort_order", "code")
    )


def unit_by_code(code: str) -> Unit | None:
    """یافتن یک واحد با کد نمایشی (بدون حساسیت به بزرگی/کوچکی حرف)."""
    return (
        Unit.objects.filter(code__iexact=code.strip())
        .select_related("floor")
        .prefetch_related("photos", "feature_links__feature")
        .first()
    )


def floors_with_units() -> QuerySet[Floor]:
    """طبقات با واحدهای فعال هر طبقه (برای صفحهٔ معرفی زائرسرا)."""
    return (
        Floor.objects.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "units",
                queryset=Unit.objects.filter(is_active=True)
                .select_related("floor")
                .order_by("sort_order", "code"),
            )
        )
        .order_by("sort_order", "number")
    )


def active_features() -> QuerySet[UnitFeature]:
    """امکانات فعال برای فهرست‌های انتخابی و نمایش عمومی."""
    return UnitFeature.objects.filter(is_active=True).order_by(
        "-is_highlight", "sort_order", "title"
    )


def occasions_between(start: date, end: date) -> QuerySet[Occasion]:
    """مناسبت‌های یک بازهٔ تاریخی (شامل هر دو سر)."""
    return Occasion.objects.filter(
        is_active=True, date_gregorian__gte=start, date_gregorian__lte=end
    ).order_by("date_gregorian")


def demand_level_by_day(start: date, end: date) -> dict[date, str]:
    """نقشهٔ سطح تقاضا به‌ازای هر روز — ورودی رنگ‌آمیزی تقویم."""
    levels: dict[date, str] = {}
    for occasion in occasions_between(start, end):
        day = occasion.date_gregorian
        current = levels.get(day, DemandLevel.NORMAL)
        # اوج بر پرتقاضا و پرتقاضا بر عادی اولویت دارد
        if occasion.demand_level == DemandLevel.PEAK or current == DemandLevel.NORMAL:
            levels[day] = occasion.demand_level
    return levels


def blocks_overlapping(*, start: datetime, end: datetime) -> QuerySet[Block]:
    """بلوک‌های فعال هم‌پوشان با بازه (واحد‌محور یا سراسری)."""
    return Block.objects.filter(
        overlap_q("starts_at", "ends_at", start, end), is_active=True
    ).select_related("unit")


def day_window(day: date) -> tuple[datetime, datetime]:
    """بازهٔ کامل یک شبانه‌روز محلی — از ۰۰:۰۰ تا ۰۰:۰۰ روز بعد."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=local_zone())
    return start, start + timedelta(days=1)
