"""همزمانی واقعی روی MariaDB — «سناریوی S2» و قفل سهمیه.

این فایل روی دیتابیس واقعی و با تراکنش‌های مستقل اجرا می‌شود (``transaction=True``)
تا چیزی که در تولید رخ می‌دهد، عیناً در تست هم رخ دهد: دو درخواست موازی، دو
اتصال مستقل، و یک برندهٔ نهایی. اگر روزی کسی تریگر عدم‌هم‌پوشانی، قفل ردیف واحد
یا قفل ردیف کاربر را بردارد، همین تست‌ها می‌شکنند.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.db import IntegrityError, connections
import pytest

from apps.core.errors import PolicyViolationError, ReservationConflictError
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Reservation
from apps.reservation.services import create_reservation
from tests.factories import future_period, make_user

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]

THREAD_TIMEOUT_SECONDS = 30


def _run_in_parallel(targets: list[Callable[[], Any]]) -> list[tuple[str, Any]]:
    """اجرای موازی توابع با هم‌زمان‌سازی دقیق روی خط شروع.

    هر ریسمان اتصال دیتابیس خودش را می‌گیرد و در پایان می‌بندد (وگرنه اتصال
    تا پایان فرآیند باز می‌ماند و کارهای پاک‌سازی تست را قفل می‌کند).
    """
    import threading

    barrier = threading.Barrier(len(targets))
    results: list[tuple[str, Any]] = [("pending", None)] * len(targets)

    def worker(index: int, target: Callable[[], Any]) -> None:
        barrier.wait(timeout=THREAD_TIMEOUT_SECONDS)
        try:
            results[index] = ("ok", target())
        except Exception as exc:
            results[index] = ("error", exc)
        finally:
            connections.close_all()

    threads = [
        threading.Thread(target=worker, args=(index, target), daemon=True)
        for index, target in enumerate(targets)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=THREAD_TIMEOUT_SECONDS)
    return results


def _book(guest, unit, start, end):
    """کوتاه‌نویس ثبت رزرو برای توابع موازی."""
    return create_reservation(guest=guest, unit=unit, start=start, end=end, guests_count=2)


def test_s2_race_produces_exactly_one_winner(units) -> None:
    """دو زائر، یک سانس: دقیقاً یک برنده و یک پیام دامنه‌ای روشن."""
    first = make_user(phone="09120000071")
    second = make_user(phone="09120000072")
    start, end = future_period(days=7, cells=2)

    results = _run_in_parallel(
        [
            lambda: _book(first, units["A1"], start, end),
            lambda: _book(second, units["A1"], start, end),
        ]
    )

    statuses = [kind for kind, _ in results]
    assert sorted(statuses) == ["error", "ok"], results
    loser = next(value for kind, value in results if kind == "error")
    assert isinstance(loser, ReservationConflictError)
    assert loser.extensions["alternatives"], "بازندهٔ تصادم باید بازهٔ جایگزین بگیرد"
    assert Reservation.objects.filter(unit=units["A1"]).count() == 1


def test_parallel_bookings_on_distinct_units_all_succeed(units) -> None:
    """پنج زائر، پنج واحد، یک سانس: همه موفق و هیچ رزرو گم‌شده‌ای نیست."""
    codes = ["A1", "B1", "A2", "B2", "B3"]
    guests = [make_user(phone=f"09120000{80 + index:03d}") for index in range(5)]
    start, end = future_period(days=9, cells=2)

    results = _run_in_parallel(
        [
            (lambda unit_code=code, guest=guest: _book(guest, units[unit_code], start, end))
            for code, guest in zip(codes, guests, strict=True)
        ]
    )

    assert [kind for kind, _ in results] == ["ok"] * 5
    assert Reservation.objects.filter(status=ReservationStatus.CONFIRMED).count() == 5
    assert len({reservation.code for _, reservation in results}) == 5


def test_row_lock_stops_quota_evasion_under_race(units) -> None:
    """یک زائر، دو ضربهٔ موازی روی دو واحد: سهمیهٔ «یک رزرو فعال» نمی‌شکند."""
    guest = make_user(phone="09120000073")
    start, end = future_period(days=11, cells=2)

    results = _run_in_parallel(
        [
            lambda: _book(guest, units["A1"], start, end),
            lambda: _book(guest, units["B1"], start, end),
        ]
    )

    assert sorted(kind for kind, _ in results) == ["error", "ok"], results
    loser = next(value for kind, value in results if kind == "error")
    assert isinstance(loser, PolicyViolationError)
    assert "active_limit" in [item["code"] for item in loser.extensions["violations"]]
    assert Reservation.objects.filter(guest=guest).count() == 1


def test_database_overlap_trigger_is_the_last_line_of_defence(guest, units) -> None:
    """حتی با دور زدن سرویس، دیتابیس هم‌پوشانی را رد می‌کند (I1).

    این تست دقیقاً همان چیزی را اثبات می‌کند که هیچ مقدار منطق پایتونی نمی‌تواند:
    نگهبان نهایی، تریگر ``reservation_no_overlap_bi`` (مهاجرت
    ``reservation/0002_overlap_guards``) روی وضعیت‌های فعال است.
    """
    start, end = future_period(days=13, cells=2)
    first = _book(guest, units["A1"], start, end)
    assert first.status == ReservationStatus.CONFIRMED

    other = make_user(phone="09120000074")
    with pytest.raises(IntegrityError) as excinfo:
        Reservation.objects.create(
            code="M-1405-9090",
            unit=units["A1"],
            guest=other,
            starts_at=start,
            ends_at=end + (end - start),
            block_starts_at=start,
            block_ends_at=end + (end - start),
            cells_count=4,
            guests_count=2,
            status=ReservationStatus.CONFIRMED,
        )
    assert "no_overlapping_active_reservations" in str(excinfo.value)


def test_highest_load_booking_attempts_keep_data_consistent(units) -> None:
    """۲۰ تلاش موازی برای یک سانس: تنها یک رزرو، بدون ۵۰۰ و بدون رکورد یتیم."""
    guests = [make_user(phone=f"0912000{100 + index:04d}") for index in range(20)]
    start, end = future_period(days=15, cells=2)

    results = _run_in_parallel(
        [(lambda guest=guest: _book(guest, units["B3"], start, end)) for guest in guests]
    )

    winners = [value for kind, value in results if kind == "ok"]
    losers = [value for kind, value in results if kind == "error"]
    assert len(winners) == 1
    assert len(losers) == 19
    assert all(isinstance(item, ReservationConflictError) for item in losers)
    assert Reservation.objects.filter(unit=units["B3"]).count() == 1
