"""تقویم رسمی ایران — بازتولید دقیق تعطیلات ۱۴۰۵، برآورد تقریبی، همگام‌سازی idempotent و API.

مرجع: تقویم رسمی کشور ۱۴۰۵ (مرکز تقویم مؤسسهٔ ژئوفیزیک دانشگاه تهران). اگر جدول ماه‌های قمری یا
فهرست مناسبت‌ها حتی یک روز جابه‌جا شود، این آزمون‌ها می‌شکنند.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.core.management import call_command
import pytest
import time_machine

from apps.core.jalali import jalali_to_gregorian
from apps.inventory import official_calendar as oc
from apps.inventory.choices import DemandLevel, OccasionSource
from apps.inventory.models import LunarMonthStart, Occasion
from apps.inventory.selectors import demand_level_by_day
from apps.inventory.services import sync_official_occasions

STARTS = {(y, m): d for y, m, d in oc.OFFICIAL_MONTH_STARTS_1405}

#: هر ۲۶ تعطیل رسمی مناسبتی ۱۴۰۵ (ماه، روز) — به‌جز جمعه‌ها.
HOLIDAYS_1405 = {
    (1, 1), (1, 2), (1, 3), (1, 4), (1, 12), (1, 13), (1, 25),
    (3, 6), (3, 14), (3, 15),
    (4, 3), (4, 4),
    (5, 13), (5, 21), (5, 22), (5, 30),
    (6, 8),
    (8, 22),
    (10, 2), (10, 16),
    (11, 4), (11, 22),
    (12, 9), (12, 19), (12, 20), (12, 29),
}  # fmt: skip

YEAR_1405 = (jalali_to_gregorian(1405, 1, 1), jalali_to_gregorian(1405, 12, 29))


def _j(month: int, day: int) -> date:
    return jalali_to_gregorian(1405, month, day)


@pytest.mark.unit
def test_every_official_1405_holiday_is_reproduced_exactly() -> None:
    """مجموعهٔ روزهای تعطیل تولیدشده = فهرست رسمی ۱۴۰۵؛ نه یک روز بیشتر، نه کمتر."""
    days = {g.day for g in oc.generate(*YEAR_1405, STARTS) if g.event.holiday}
    assert days == {_j(m, d) for m, d in HOLIDAYS_1405}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("slug", "month", "day", "hijri"),
    [
        ("zaynab-birth", 7, 24, "۵ جمادی‌الاول ۱۴۴۸"),  # روز پرستار — ۲۴ مهر
        ("masumeh-demise", 6, 31, "۱۰ ربیع‌الثانی ۱۴۴۸"),
        ("askari-birth", 6, 29, "۸ ربیع‌الثانی ۱۴۴۸"),
        ("reza-birth", 2, 9, "۱۱ ذی‌القعده ۱۴۴۷"),
        ("masumeh-birth", 1, 30, "۱ ذی‌القعده ۱۴۴۷"),  # آغاز دههٔ کرامت
        ("arafah", 3, 5, "۹ ذی‌الحجه ۱۴۴۷"),
        ("reza-martyr", 5, 22, "۲۹ صفر ۱۴۴۸"),  # «آخر صفر» ⇒ صفرِ ۲۹روزه
        ("fatima-martyr", 8, 22, "۳ جمادی‌الثانی ۱۴۴۸"),
    ],
)
def test_religious_occasions_match_official_calendar(
    slug: str, month: int, day: int, hijri: str
) -> None:
    """مناسبت‌های غیرتعطیل و «آخر ماه» هم با تقویم رسمی می‌خوانند (روز شمسی + برچسب قمری)."""
    found = [g for g in oc.generate(*YEAR_1405, STARTS) if g.event.slug == slug]
    assert len(found) == 1
    assert found[0].day == _j(month, day)
    assert found[0].hijri_label == hijri
    assert found[0].provisional is False


@pytest.mark.unit
def test_months_beyond_official_table_are_estimated_and_flagged() -> None:
    """ماه نیامده در تقویم رسمی ⇒ برآورد با میانگین ماه هلالی (±۱ روز) و علامت «تقریبی»."""
    day, official = oc.month_start(1448, 12, STARTS)
    assert official is False
    expected = STARTS[1448, 10] + timedelta(days=2 * oc.SYNODIC_MONTH)
    assert abs((day - expected).days) <= 1
    # «آخر صفر» وقتی ماه بعدش تقریبی است، خودش هم تقریبی است.
    _day, official = oc.hijri_to_gregorian(1449, 2, oc.LAST_DAY, STARTS)
    assert official is False


@pytest.mark.unit
def test_jalali_occasions_carry_the_lunar_date_label() -> None:
    """مناسبت شمسی هم برچسب قمری دارد: ۲۲ بهمن ۱۴۰۵ = ۴ رمضان ۱۴۴۸."""
    revolution = next(g for g in oc.generate(*YEAR_1405, STARTS) if g.event.slug == "revolution")
    assert revolution.hijri_label == "۴ رمضان ۱۴۴۸"
    assert revolution.event.kind == "holiday"


@pytest.mark.django_db
@pytest.mark.integration
def test_database_month_starts_override_the_builtin_table() -> None:
    """ردیف پایگاه‌داده (اصلاح مدیر) بر جدول داخلی مقدم است؛ جدول خالی هم کار را نمی‌خواباند."""
    LunarMonthStart.all_objects.all().hard_delete()
    report = sync_official_occasions(start=_j(12, 1), end=_j(12, 29))
    assert report.total > 0
    assert Occasion.objects.get(source_key="ir:fitr:1448").date_gregorian == date(2027, 3, 10)
    LunarMonthStart.objects.create(hijri_year=1448, hijri_month=10, starts_on=date(2027, 3, 11))
    sync_official_occasions(start=_j(12, 1), end=_j(12, 29))
    assert Occasion.objects.get(source_key="ir:fitr:1448").date_gregorian == date(2027, 3, 11)


@pytest.mark.django_db
@pytest.mark.integration
def test_sync_is_idempotent_and_keeps_admin_tuning() -> None:
    """اجرای دوباره چیزی نمی‌سازد؛ تنظیم مدیر (تقاضا، توضیح، غیرفعال، حذف) محفوظ می‌ماند."""
    start, end = _j(8, 1), _j(12, 29)
    first = sync_official_occasions(start=start, end=end)
    assert first.created == first.total > 20
    assert first.provisional == 0
    again = sync_official_occasions(start=start, end=end)
    assert (again.created, again.updated, again.removed) == (0, 0, 0)

    fatima = Occasion.objects.get(source_key="ir:fatima-martyr:1448")
    assert fatima.is_holiday and fatima.source == OccasionSource.OFFICIAL
    assert fatima.date_jalali == "۱۴۰۵/۰۸/۲۲"
    fatima.demand_level = DemandLevel.PEAK
    fatima.description = "یادداشت مدیر"
    fatima.save()
    hidden = Occasion.objects.get(source_key="ir:mabath:1448")
    hidden.delete()  # حذف نرم مدیر
    sync_official_occasions(start=start, end=end)
    fatima.refresh_from_db()
    assert (fatima.demand_level, fatima.description) == (DemandLevel.PEAK, "یادداشت مدیر")
    assert not Occasion.objects.filter(source_key="ir:mabath:1448").exists()
    assert Occasion.all_objects.filter(source_key="ir:mabath:1448").count() == 1


@pytest.mark.django_db
@pytest.mark.integration
def test_moving_a_lunar_month_moves_its_occasions_immediately(
    django_capture_on_commit_callbacks,
) -> None:
    """اعلام رؤیت (مثلاً عید فطر یک روز دیرتر) ⇒ سیگنال، همگام‌سازی و جابه‌جایی همان مناسبت‌ها."""
    with time_machine.travel(date(2027, 2, 1), tick=False):
        call_command("sync_occasions", "--quiet")
        fitr = Occasion.objects.get(source_key="ir:fitr:1448")
        assert fitr.date_gregorian == date(2027, 3, 10)
        with django_capture_on_commit_callbacks(execute=True):
            LunarMonthStart.objects.update_or_create(
                hijri_year=1448, hijri_month=10, defaults={"starts_on": date(2027, 3, 11)}
            )
        fitr.refresh_from_db()
        assert fitr.date_gregorian == date(2027, 3, 11)
        assert fitr.date_jalali == "۱۴۰۵/۱۲/۲۰"
        # ماه پیشین (رمضان) ۳۱روزه نمی‌شود؛ شب قدرِ بیست‌وسوم سر جایش می‌ماند.
        assert Occasion.objects.get(source_key="ir:qadr-23:1448").date_gregorian == date(2027, 3, 1)


@pytest.mark.django_db
@pytest.mark.integration
def test_calendar_demand_and_occasions_api_expose_official_data(api) -> None:
    """رنگ تقاضای روزها و پاسخ ‎/occasions‎ از همان داده‌های رسمی تغذیه می‌شوند."""
    sync_official_occasions(start=_j(8, 1), end=_j(11, 30))
    levels = demand_level_by_day(_j(8, 1), _j(11, 30))
    assert levels[_j(11, 4)] == DemandLevel.PEAK  # نیمهٔ شعبان
    assert levels[_j(8, 22)] == DemandLevel.HIGH
    res = api.get("/api/v1/occasions", {"from": "2026-11-12", "to": "2026-11-14"})
    assert res.status_code == 200
    (row,) = [o for o in res.json() if o["title"].startswith("شهادت حضرت فاطمهٔ")]
    assert row["is_holiday"] is True
    assert row["hijri_date"] == "۳ جمادی‌الثانی ۱۴۴۸"
    assert row["is_provisional"] is False
    assert row["source"] == "official"
