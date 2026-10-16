"""تست‌های موتور تخصیص — رتبه‌بندی جانشین‌ها و قواعد ایام پیک.

دامنهٔ تخصیص دو وعده دارد و هر دو این‌جا آزموده می‌شوند:
    • «بهترین گزینهٔ جانشین کدام است؟» ⇒ ``suggest_options`` و ``allocation_plan``
    • «در ایام پیک سهم هر زائر چقدر است؟» ⇒ ``AllocationRule`` و ``peak_limit_for``
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
import pytest

from apps.core.errors import PolicyViolationError
from apps.reservation import allocation, services as reservation_services
from apps.reservation.models import AllocationRule, Reservation
from tests.factories import days_ahead, lattice_period, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _book(*, guest, unit, start, end, guests: int = 1):
    """رزرو فعال بسازد — میان‌بر خوانا برای تست‌ها."""
    return reservation_services.create_reservation(
        guest=guest, unit=unit, start=start, end=end, guests_count=guests
    )


def test_suggest_options_ranks_same_unit_and_same_day_first(guest, units) -> None:
    """«همان روز، همان واحد» بهترین گزینه است و دلیلش هم گفته می‌شود."""
    start, end = lattice_period(start_day=days_ahead(6), index=0, cells=2)
    options = allocation.suggest_options(unit=units["A1"], start=start, end=end, guests_count=2)

    assert options, "برای بازهٔ آزاد باید گزینه پیشنهاد شود"
    best = options[0]
    assert best.unit_code == "A1"
    assert best.start == start
    joined = " ".join(best.reasons)
    assert "همان روز درخواستی" in joined
    assert "همان واحد مورد نظر شما" in joined
    assert best.score == max(option.score for option in options)


def test_options_respect_shift_and_candidate_limits(guest, units) -> None:
    """گزینه‌ها فقط در محدودهٔ جابه‌جایی مجاز و تعداد سقف‌دار پیشنهاد می‌شوند."""
    start, end = lattice_period(start_day=days_ahead(9), index=0, cells=2)
    options = allocation.suggest_options(unit=units["B1"], start=start, end=end, guests_count=2)

    assert options
    assert len(options) <= allocation.MAX_CANDIDATES
    for option in options:
        assert abs(option.shifts) <= max(abs(step) for step in allocation.SHIFT_STEPS)
        assert option.cells == 2
        assert option.unit_code in {unit.code for unit in units.values()}


def test_options_are_read_only(guest, units) -> None:
    """پیشنهاد دادن هرگز رزرو نمی‌سازد — موتور تخصیص فقط می‌خواند."""
    start, end = lattice_period(start_day=days_ahead(7), index=0, cells=2)
    before = Reservation.objects.count()
    allocation.suggest_options(unit=units["A2"], start=start, end=end, guests_count=1)
    allocation.allocation_plan(unit=units["A2"], start=start, end=end, guests_count=1)
    assert Reservation.objects.count() == before


def test_past_period_is_never_offered(units, guest) -> None:
    """بازهٔ گذشته هرگز پیشنهاد نمی‌شود، هرچند واحد در تاریخ خالی بوده است."""
    start, end = lattice_period(start_day=days_ahead(-3), index=0, cells=2)
    assert allocation.suggest_options(unit=units["A1"], start=start, end=end, guests_count=1) == []
    assert timezone.now() > end


def test_plan_reports_options_and_peak_state(guest, units) -> None:
    """برنامهٔ تخصیص، نیم‌روز درخواستی، گزینه‌ها و وضعیت سقف پیک را برمی‌گرداند."""
    start, end = lattice_period(start_day=days_ahead(8), index=0, cells=2)
    plan = allocation.allocation_plan(unit=units["B2"], start=start, end=end, guests_count=2)

    assert plan["requested_cells"] == 2
    assert plan["options"]
    assert plan["peak_limit"] is None  # پیش‌فرض: قاعده‌ای روشن نیست
    assert plan["blocked_by_peak"] is False


def test_peak_limit_defaults_to_disabled(units, guest) -> None:
    """بدون قاعدهٔ فعال، سقف پیک اعمال نمی‌شود (پیش‌فرض محافظه‌کارانه)."""
    start, end = lattice_period(start_day=days_ahead(3), index=0, cells=2)
    assert not AllocationRule.objects.filter(is_active=True).exists()
    assert allocation.peak_limit_for(guest=guest, start=start, end=end) is None


def test_active_rule_caps_cells_and_creation_is_blocked(units, guest, manager) -> None:
    """قاعدهٔ فعال: درخواست بیش از سقف با خطای سیاست و پیام شفاف رد می‌شود."""
    AllocationRule.objects.create(
        name="سقف ایام پیک",
        is_active=True,
        max_cells=2,
        min_cells=1,
        rotation_fairness=False,
    )
    two_days = lattice_period(start_day=days_ahead(11), index=0, cells=4)
    assert allocation.peak_limit_for(guest=guest, start=two_days[0], end=two_days[1]) == 2

    with pytest.raises(PolicyViolationError) as error:
        _book(guest=guest, unit=units["A1"], start=two_days[0], end=two_days[1], guests=1)

    violations = error.value.extensions["violations"]
    assert any(item["code"] == "peak_limit" for item in violations)
    assert "سقف ایام پیک" in violations[0]["message"]


def test_rule_within_limit_still_books(units, guest) -> None:
    """قاعدهٔ پیک، رزرو کوتاه‌تر را نمی‌بندد — فقط سقف می‌گذارد."""
    AllocationRule.objects.create(
        name="سقف ایام پیک",
        is_active=True,
        max_cells=2,
        min_cells=1,
        rotation_fairness=False,
    )
    start, end = lattice_period(start_day=days_ahead(12), index=0, cells=2)
    reservation = _book(guest=guest, unit=units["B3"], start=start, end=end, guests=1)
    assert reservation.status in {"confirmed", "hold"}


def test_rotation_fairness_reduces_allowance(units, guest) -> None:
    """عدالت چرخشی: زائری که سهمش را برده، سقف کمتری می‌گیرد."""
    AllocationRule.objects.create(
        name="سقف چرخشی",
        is_active=True,
        max_cells=3,
        min_cells=1,
        rotation_fairness=True,
    )
    first = lattice_period(start_day=days_ahead(14), index=0, cells=2)
    _book(guest=guest, unit=units["A1"], start=first[0], end=first[1], guests=1)

    second = lattice_period(start_day=days_ahead(20), index=0, cells=2)
    allowance = allocation.peak_limit_for(guest=guest, start=second[0], end=second[1])
    assert allowance == 1  # ۳ منهای ۲ نیم‌روز مصرف‌شده، کف ۱

    fresh = make_user(phone="09120004001")
    assert allocation.peak_limit_for(guest=fresh, start=second[0], end=second[1]) == 3


def test_rule_scope_by_date_window_is_respected(units, guest) -> None:
    """قاعدهٔ زمان‌دار فقط در بازهٔ خودش اعمال می‌شود."""
    AllocationRule.objects.create(
        name="سقف تعطیلات",
        is_active=True,
        max_cells=1,
        min_cells=1,
        rotation_fairness=False,
        starts_on=days_ahead(30),
        ends_on=days_ahead(34),
    )
    inside = lattice_period(start_day=days_ahead(31), index=0, cells=2)
    outside = lattice_period(start_day=days_ahead(40), index=0, cells=2)
    assert allocation.peak_limit_for(guest=guest, start=inside[0], end=inside[1]) == 1
    assert allocation.peak_limit_for(guest=guest, start=outside[0], end=outside[1]) is None


def test_occasion_scoped_rule_only_applies_on_that_occasion(units, guest) -> None:
    """قاعده‌ای که به یک مناسبت گره خورده، در روزهای بی‌مناسبت اعمال نمی‌شود."""
    AllocationRule.objects.create(
        name="سقف مناسبت",
        is_active=True,
        max_cells=1,
        min_cells=1,
        rotation_fairness=False,
        applies_to_occasion="مناسبتی که وجود ندارد",
    )
    start, end = lattice_period(start_day=days_ahead(16), index=0, cells=2)
    assert allocation.peak_limit_for(guest=guest, start=start, end=end) is None


def test_shifted_options_span_close_half_days_only(guest, units) -> None:
    """با پر بودن بازهٔ درخواستی، نزدیک‌ترین نیم‌روزها پیشنهاد می‌شوند."""
    day = days_ahead(18)
    # شبِ روز هدف را در همهٔ واحدها پر می‌کنیم: درخواست «یک شبانه‌روز از ۰۸:۰۰»
    # ناممکن می‌شود، اما نیم‌روز قبل و بعد آزاد است ⇒ موتور باید جابه‌جایی دهد.
    for index, unit in enumerate(units.values()):
        start, end = lattice_period(start_day=day, index=1, cells=1)
        _book(guest=make_user(phone=f"09120005{index:03d}"), unit=unit, start=start, end=end)

    start, end = lattice_period(start_day=day, index=0, cells=2)
    options = allocation.suggest_options(unit=units["A1"], start=start, end=end, guests_count=2)
    assert options, "با پر بودن شب همهٔ واحدها، گزینهٔ جابه‌جاشده باید باشد"
    assert all(option.shifts != 0 for option in options)
    for option in options:
        gap = abs(option.start - start)
        assert gap <= timedelta(days=2)
