"""بازهٔ نیم‌باز (``apps.core.periods``) — همان معنای عملگر ``&&`` پستگرس پیشین.

``Period`` و ``overlap_q`` جایگزین ستون‌های بازه‌ای شده‌اند؛ اگر معنای مرزها
(مجاورت = بدون تداخل) یا بی‌کرانی (``None``) جابه‌جا شود، تقویم و نگهبان‌ها از
هم جدا می‌شوند. پس هر دو — در پایتون و در SQL — همین‌جا با یک جدول حالت آزموده
می‌شوند.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone
import pytest

from apps.core.periods import Period, overlap_q
from apps.inventory.choices import BlockKind
from apps.inventory.models import Block

BASE = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.get_fixed_timezone(210))
HOUR = timedelta(hours=1)

#: (آغاز، پایان) بازهٔ پرس‌وجو نسبت به بازهٔ مرجع [BASE, BASE+4h) ⇒ هم‌پوشان؟
CASES = [
    ((BASE - 2 * HOUR, BASE), False),  # درست پیش از آغاز (مجاور)
    ((BASE + 4 * HOUR, BASE + 5 * HOUR), False),  # درست پس از پایان (مجاور)
    ((BASE - HOUR, BASE + HOUR), True),  # هم‌پوشانی از چپ
    ((BASE + 3 * HOUR, BASE + 6 * HOUR), True),  # هم‌پوشانی از راست
    ((BASE + HOUR, BASE + 2 * HOUR), True),  # درون
    ((BASE - HOUR, BASE + 5 * HOUR), True),  # دربرگیرنده
    ((None, BASE), False),  # بی‌کران چپ تا آغاز
    ((None, BASE + HOUR), True),
    ((BASE + 4 * HOUR, None), False),  # از پایان به بعد
    ((BASE + 3 * HOUR, None), True),
    ((None, None), True),
]


@pytest.mark.parametrize(("bounds", "expected"), CASES)
def test_period_overlaps_is_half_open(bounds, expected) -> None:
    """``Period.overlaps``: مجاورت تداخل نیست؛ ``None`` یعنی بی‌کران."""
    reference = Period(BASE, BASE + 4 * HOUR)
    assert reference.overlaps(*bounds) is expected


def test_empty_period_never_overlaps() -> None:
    """بازهٔ تهی (آغاز ≥ پایان) با هیچ‌چیز هم‌پوشان نیست — همانند ``empty`` پستگرس."""
    empty = Period(BASE, BASE)
    assert empty.isempty
    assert not empty.overlaps(None, None)
    assert not Period(BASE, BASE + HOUR).overlaps(BASE + HOUR, BASE)
    assert not Period(None, BASE).isempty


def test_overlap_q_without_bounds_matches_everything() -> None:
    """``overlap_q`` بی‌مرز شرطی نمی‌سازد (همهٔ ردیف‌ها)."""
    assert overlap_q("starts_at", "ends_at", None, None) == overlap_q("a", "b", None, None)
    assert not overlap_q("starts_at", "ends_at", None, None).children


@pytest.mark.django_db
@pytest.mark.parametrize(("bounds", "expected"), CASES)
def test_overlap_q_agrees_with_period_in_sql(bounds, expected) -> None:
    """شرط SQL دقیقاً همان پاسخ پایتون را می‌دهد (یک تعریف، دو اجرا)."""
    block = Block.objects.create(
        unit=None,
        starts_at=BASE,
        ends_at=BASE + 4 * HOUR,
        kind=BlockKind.ADMIN,
        reason="مرجع",
    )
    found = Block.objects.filter(overlap_q("starts_at", "ends_at", *bounds), pk=block.pk).exists()
    assert found is expected
    assert block.period.overlaps(*bounds) is expected
