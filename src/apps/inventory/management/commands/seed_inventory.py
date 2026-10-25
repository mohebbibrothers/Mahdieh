"""دستور ساخت دادهٔ پایهٔ زائرسرا — ۳ طبقه، ۵ واحد، امکانات و مناسبت‌ها.

قاعده: این دستور **idempotent** است؛ اجرای مکرر، دادهٔ تکراری نمی‌سازد و
مقادیر موجود را به‌روز می‌کند. بنابراین اجرای آن در استقرار (deploy) بی‌خطر است.

ساختار ثابت (طبق شرح کارفرما):
    طبقهٔ ۱: سوئیت + اتاق بزرگ
    طبقهٔ ۲: سوئیت + اتاق بزرگ
    طبقهٔ ۳: اتاق بزرگ
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.inventory.choices import DemandLevel, OccasionKind, UnitType
from apps.inventory.models import Floor, Occasion, Unit, UnitFeature, UnitFeatureLink
from apps.inventory.services import create_block  # noqa: F401 - مرجع قرارداد سرویس

FLOORS: tuple[dict[str, Any], ...] = (
    {
        "number": 1,
        "title": "طبقهٔ اول",
        "description": "دسترسی آسان از ورودی؛ مناسب زائران کم‌توان و سالمند.",
        "accessibility_notes": "بدون پله تا ورودی واحدها",
        "sort_order": 1,
    },
    {
        "number": 2,
        "title": "طبقهٔ دوم",
        "description": "طبقهٔ میانی با دسترسی آسان به آبدارخانه و نمازخانه.",
        "accessibility_notes": "دسترسی با پله و آسانسور",
        "sort_order": 2,
    },
    {
        "number": 3,
        "title": "طبقهٔ سوم",
        "description": "آرام‌ترین طبقه؛ یک اتاق بزرگ با دید باز.",
        "accessibility_notes": "دسترسی با آسانسور",
        "sort_order": 3,
    },
)

UNITS: tuple[dict[str, Any], ...] = (
    {
        "code": "A1",
        "floor": 1,
        "unit_type": UnitType.SUITE,
        "title": "سوئیت طبقهٔ اول",
        "capacity_adults": 4,
        "capacity_children": 2,
        "sort_order": 1,
    },
    {
        "code": "B1",
        "floor": 1,
        "unit_type": UnitType.LARGE_ROOM,
        "title": "اتاق بزرگ طبقهٔ اول",
        "capacity_adults": 6,
        "capacity_children": 2,
        "sort_order": 2,
    },
    {
        "code": "A2",
        "floor": 2,
        "unit_type": UnitType.SUITE,
        "title": "سوئیت طبقهٔ دوم",
        "capacity_adults": 4,
        "capacity_children": 2,
        "sort_order": 3,
    },
    {
        "code": "B2",
        "floor": 2,
        "unit_type": UnitType.LARGE_ROOM,
        "title": "اتاق بزرگ طبقهٔ دوم",
        "capacity_adults": 6,
        "capacity_children": 2,
        "sort_order": 4,
    },
    {
        "code": "B3",
        "floor": 3,
        "unit_type": UnitType.LARGE_ROOM,
        "title": "اتاق بزرگ طبقهٔ سوم",
        "capacity_adults": 6,
        "capacity_children": 2,
        "sort_order": 5,
    },
)

FEATURES: tuple[dict[str, Any], ...] = (
    {"code": "wifi", "title": "اینترنت بی‌سیم", "icon": "📶", "is_highlight": True, "sort_order": 1},
    {
        "code": "private-bath",
        "title": "سرویس بهداشتی اختصاصی",
        "icon": "🚿",
        "is_highlight": True,
        "sort_order": 2,
    },
    {
        "code": "kitchen",
        "title": "آشپزخانهٔ مشترک",
        "icon": "🍲",
        "is_highlight": True,
        "sort_order": 3,
    },
    {"code": "prayer", "title": "سجاده و مهر", "icon": "🕌", "is_highlight": True, "sort_order": 4},
    {"code": "parking", "title": "پارکینگ", "icon": "🚗", "is_highlight": False, "sort_order": 5},
    {"code": "elevator", "title": "آسانسور", "icon": "🛗", "is_highlight": False, "sort_order": 6},
    {"code": "fridge", "title": "یخچال", "icon": "🧊", "is_highlight": False, "sort_order": 7},
    {
        "code": "heating",
        "title": "گرمایش و سرمایش",
        "icon": "🌡",
        "is_highlight": False,
        "sort_order": 8,
    },
    {
        "code": "laundry",
        "title": "رختشویخانه",
        "icon": "🧺",
        "is_highlight": False,
        "sort_order": 9,
    },
    {
        "code": "locker",
        "title": "صندوق امانات",
        "icon": "🔐",
        "is_highlight": False,
        "sort_order": 10,
    },
)

# مناسبت‌ها فقط برای نمونهٔ امسال؛ دادهٔ کامل هر سال توسط مدیر وارد می‌شود.
OCCASIONS: tuple[dict[str, Any], ...] = (
    {
        "title": "نوروز",
        "kind": OccasionKind.HOLIDAY,
        "date_gregorian": date(2027, 3, 21),
        "demand_level": DemandLevel.PEAK,
        "is_fixed_every_year": True,
        "description": (
            "آغاز سال نو و پرتقاضاترین روزهای سفر زیارتی؛ زودتر برنامه‌ریزی کنید و اگر "
            "جایی نماند، در صف انتظار بمانید."
        ),
    },
    {
        "title": "ایام پایانی ماه صفر",
        "kind": OccasionKind.RELIGIOUS,
        "date_gregorian": date(2027, 1, 20),
        "demand_level": DemandLevel.PEAK,
        "is_fixed_every_year": False,
        "description": (
            "ایام سوگواری پایان صفر و سالروز شهادت امام رضا (ع)؛ از پرتقاضاترین روزهای "
            "زیارتی سال — تاریخ قمری هر سال بازبینی می‌شود."
        ),
    },
)


class Command(BaseCommand):
    """ساخت دادهٔ پایهٔ موجودی زائرسرا (idempotent)."""

    help = "سه طبقه، پنج واحد، امکانات و مناسبت‌های نمونه را می‌سازد یا به‌روز می‌کند."

    def add_arguments(self, parser: Any) -> None:
        """گزینه‌های اجرای دستور."""
        parser.add_argument(
            "--with-occasions",
            action="store_true",
            help="درج مناسبت‌های نمونه (پیش‌فرض: بدون مناسبت)",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> str:
        """اجرای ساخت دادهٔ پایه."""
        del args
        floors: dict[int, Floor] = {}
        for payload in FLOORS:
            floor, _ = Floor.objects.update_or_create(
                number=payload["number"],
                defaults={
                    "title": payload["title"],
                    "description": payload["description"],
                    "accessibility_notes": payload["accessibility_notes"],
                    "sort_order": payload["sort_order"],
                    "is_active": True,
                },
            )
            floors[payload["number"]] = floor

        features: dict[str, UnitFeature] = {}
        for payload in FEATURES:
            feature, _ = UnitFeature.objects.update_or_create(
                code=payload["code"],
                defaults={
                    "title": payload["title"],
                    "icon": payload["icon"],
                    "is_highlight": payload["is_highlight"],
                    "sort_order": payload["sort_order"],
                    "is_active": True,
                },
            )
            features[payload["code"]] = feature

        for payload in UNITS:
            unit, _ = Unit.objects.update_or_create(
                code=payload["code"],
                defaults={
                    "floor": floors[payload["floor"]],
                    "unit_type": payload["unit_type"],
                    "title": payload["title"],
                    "capacity_adults": payload["capacity_adults"],
                    "capacity_children": payload["capacity_children"],
                    "cleaning_buffer_minutes": None,  # تابع سیاست سراسری
                    "sort_order": payload["sort_order"],
                    "is_active": True,
                },
            )
            shared = ["wifi", "prayer", "fridge", "heating"]
            suite_only = (
                ["private-bath", "kitchen", "parking", "elevator"]
                if unit.unit_type == UnitType.SUITE
                else []
            )
            codes = [*shared, *suite_only, "laundry", "locker"]
            for code in codes:
                UnitFeatureLink.objects.get_or_create(unit=unit, feature=features[code])

        created_occasions = 0
        if options.get("with_occasions"):
            for payload in OCCASIONS:
                _occasion, created = Occasion.objects.update_or_create(
                    title=payload["title"],
                    date_gregorian=payload["date_gregorian"],
                    defaults={
                        "kind": payload["kind"],
                        "demand_level": payload["demand_level"],
                        "is_fixed_every_year": payload["is_fixed_every_year"],
                        "description": payload["description"],
                        "is_active": True,
                    },
                )
                created_occasions += int(created)

        summary = (
            f"طبقات: {Floor.objects.count()} · واحدها: {Unit.objects.count()} · "
            f"امکانات: {UnitFeature.objects.count()} · مناسبت‌های تازه: {created_occasions} "
            f"· زمان: {timezone.localtime():%Y-%m-%d %H:%M}"
        )
        self.stdout.write(self.style.SUCCESS(summary))
        return summary
