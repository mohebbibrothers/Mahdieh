"""اثبات همزمانی — ادعای مرکزی سیستم، با تراکنش‌های موازی واقعی.

ادعای مرکزی مهدیه این است: «اگر صدها زائر در یک لحظه یک سانس را بخواهند،
دیتابیس دقیقاً یکی را می‌پذیرد.» این ادعا با تست سریالی اثبات نمی‌شود؛ این فایل
با **تردهای واقعی روی MariaDB واقعی** آن را می‌سنجد:

    ۱) ۲۰۰ درخواست موازی روی یک سانس ⇒ دقیقاً یک برنده، بقیه پیام ارزشمند.
    ۲) یک کلید هم‌توانی، چند درخواست موازی ⇒ یک اثر (نه دو، نه خطای ۵۰۰).
    ۳) دو درخواست انتظار موازی برای یک زائر ⇒ یک ردیف (قید یکتای شرطی).
    ۴) دو پذیرش موازی روی یک رزرو ⇒ یک انتقال، دیگری پیام روشن.

نکتهٔ اجرایی: اندازهٔ استخر تردها کمتر از سقف اتصال‌های MariaDB است؛ همان
چند ترد، رقابت واقعی روی همان ردیف/قید می‌سازند. هر ترد اتصال خودش را می‌گیرد
و در پایان می‌بندد تا تست، اتصال‌ها را وابسته به ترتیب اجرا نکند.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import timedelta
import threading
import time
from typing import Any
import uuid

from django.db import connections
from django.utils import timezone
import pytest
import time_machine

from apps.core.errors import DomainError
from apps.core.idempotency import IdempotentOutcome, execute_idempotent
from apps.identity.models import User
from apps.reservation import services as reservation_services
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Reservation
from apps.waitlist import services as waitlist_services
from apps.waitlist.choices import WaitlistStatus
from apps.waitlist.models import WaitlistEntry
from tests.factories import days_ahead, lattice_period, national_id_for

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]

WORKERS = 16
ATTEMPTS = 200
JOIN_ATTEMPTS = 24
BARRIER_TIMEOUT = 60.0


def _bulk_guests(count: int) -> list[User]:
    """ساخت سریع چند زائر با پروفایل کامل (بدون هزینهٔ هش رمز عبور).

    از سرویس واقعی ``complete_profile`` استفاده می‌شود تا دادهٔ تست مثل دادهٔ
    واقعی باشد (کد ملی رمزنگاری‌شده + ایندکس کور).
    """
    guests: list[User] = []
    for index in range(count):
        phone = f"0912{index:07d}"
        user = User.objects.create_user(phone=phone)
        user.complete_profile(
            first_name="زائر",
            last_name=f"آزمون {index}",
            province="خراسان رضوی",
            city="مشهد",
            national_id=national_id_for(phone, f"{index + 1:09d}"[:9]),
        )
        guests.append(user)
    return guests


def _race(guests: list[User], unit, start, end, *, workers: int = WORKERS) -> list[Any]:
    """اجرای همزمان تلاش‌های رزرو و بازگرداندن سرنوشت هر تلاش.

    یک ``Barrier`` همهٔ تردهای هر دور را هم‌زمان آزاد می‌کند تا رقابت مصنوعی
    نباشد؛ بدون آن، تست به رزرو سریالی تبدیل می‌شود و هیچ چیز اثبات نمی‌کند.
    """
    barrier = threading.Barrier(workers, timeout=BARRIER_TIMEOUT)

    def attempt(guest: User) -> dict[str, Any]:
        """یک تلاش رزرو در ترد جداگانه."""
        with suppress(threading.BrokenBarrierError):  # تنها در شکست غیرمنتظره رخ می‌دهد
            barrier.wait()
        try:
            reservation = reservation_services.create_reservation(
                guest=guest, unit=unit, start=start, end=end, guests_count=2
            )
            return {"outcome": "created", "code": reservation.code}
        except DomainError as error:
            return {
                "outcome": "rejected",
                "error": type(error).__name__,
                "code": getattr(error, "code", ""),
                "message": error.detail,
                "has_alternatives": "alternatives" in (error.extensions or {}),
                "status": error.status,
            }
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(attempt, guests))


@pytest.mark.slow
def test_hundreds_of_parallel_bookings_leave_exactly_one_winner(units) -> None:
    """۲۰۰ تلاش موازی روی یک سانس ⇒ یک رزرو، بقیه «پیام ارزشمند».

    این تست سنگین است (هر بازنده فهرست بازه‌های جایگزین را می‌سازد) و به‌عنوان
    ``slow`` علامت خورده تا گیت سریع محلی سبک بماند؛ در CI و در گیت کامل اجرا
    می‌شود.
    """
    guests = _bulk_guests(ATTEMPTS)
    start, end = lattice_period(start_day=days_ahead(7), index=0, cells=2)
    unit = units["A1"]

    started = time.monotonic()
    results = _race(guests, unit, start, end)
    elapsed = time.monotonic() - started

    created = [row for row in results if row["outcome"] == "created"]
    rejected = [row for row in results if row["outcome"] == "rejected"]

    assert len(created) == 1, f"باید دقیقاً یک برنده باشد؛ {len(created)} برنده دیده شد."
    assert len(rejected) == ATTEMPTS - 1
    assert Reservation.objects.filter(unit=unit).count() == 1

    # پیام‌ها ارزشمندند: متن فارسی، وضعیت ۴۰۹ و فهرست بازه‌های جایگزین.
    for row in rejected:
        assert row["message"], "خطای رد باید پیام انسانی داشته باشد."
        assert row["status"] in (409, 422)
    assert sum(1 for row in rejected if row["has_alternatives"]) >= len(rejected) // 2

    kinds = Counter(row["error"] for row in rejected)
    assert set(kinds) <= {"ReservationConflictError", "PolicyViolationError"}
    assert elapsed < 180, f"رقابت {ATTEMPTS} تلاشی کند بود ({elapsed:.1f} ثانیه)"


def test_parallel_same_idempotency_key_has_single_effect(units) -> None:
    """یک کلید هم‌توانی، ۱۲ درخواست موازی ⇒ یک رزرو و بازپخش/خطای روشن."""
    guests = _bulk_guests(12)
    start, end = lattice_period(start_day=days_ahead(9), index=0, cells=2)
    unit = units["B1"]
    key = uuid.uuid4().hex
    payload = {"unit": unit.code, "start": start.isoformat(), "cells": 2}
    barrier = threading.Barrier(len(guests), timeout=BARRIER_TIMEOUT)
    executions: list[str] = []

    def attempt(guest: User) -> dict[str, Any]:
        """اجرای همان عملیات با همان کلید، در ترد جداگانه."""

        def action() -> tuple[int, Any]:
            """ساخت رزرو واقعی — باید فقط یک بار اجرا شود."""
            reservation = reservation_services.create_reservation(
                guest=guest, unit=unit, start=start, end=end, guests_count=2
            )
            executions.append(reservation.code)
            return 201, {"code": reservation.code}

        with suppress(threading.BrokenBarrierError):
            barrier.wait()
        try:
            outcome: IdempotentOutcome = execute_idempotent(
                key=key,
                scope="tests.concurrency",
                actor_ref=str(guest.pk),
                payload=payload,
                action=action,
            )
            return {"outcome": "ok", "code": (outcome.body or {}).get("code")}
        except DomainError as error:
            return {
                "outcome": "rejected",
                "error": type(error).__name__,
                "status": error.status,
                "message": error.detail,
            }
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(guests)) as pool:
        results = list(pool.map(attempt, guests))

    assert len(executions) == 1, f"عملیات باید یک بار اجرا شود؛ {len(executions)} بار شد."
    assert Reservation.objects.filter(unit=unit).count() == 1
    # هر نتیجه یا بازپخش پاسخ است یا خطای ۴۰۹ با پیام روشن؛ هیچ ۵۰۰ای پذیرفته نیست.
    for row in results:
        if row["outcome"] == "ok":
            assert row["code"] == executions[0]
        else:
            assert row["status"] == 409, row
            assert row["message"]
    replayed = [row for row in results if row["outcome"] == "ok"]
    assert replayed, "دست‌کم یک درخواست باید پاسخ گرفته باشد."


def test_parallel_waitlist_joins_keep_single_active_entry(guest, units, operator) -> None:
    """۲۴ درخواست انتظار موازی از یک زائر ⇒ یک ردیف فعال (قید یکتای شرطی)."""
    start, end = lattice_period(start_day=days_ahead(12), index=0, cells=2)
    unit = units["A2"]
    reservation_services.create_reservation(
        guest=guest, unit=unit, start=start, end=end, guests_count=2
    )
    owner = _bulk_guests(1)[0]
    barrier = threading.Barrier(JOIN_ATTEMPTS, timeout=BARRIER_TIMEOUT)

    def attempt(_index: int) -> dict[str, Any]:
        """تلاش برای ثبت درخواست انتظار روی همان سانس اشغال‌شده."""
        with suppress(threading.BrokenBarrierError):
            barrier.wait()
        try:
            entry = waitlist_services.join_waitlist(
                guest=owner, unit=unit, start=start, end=end, guests_count=2
            )
            return {"outcome": "joined", "pk": str(entry.pk)}
        except DomainError as error:
            return {"outcome": "rejected", "error": type(error).__name__}
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=JOIN_ATTEMPTS) as pool:
        results = list(pool.map(attempt, range(JOIN_ATTEMPTS)))

    assert WaitlistEntry.objects.filter(guest=owner).count() == 1
    joined = [row for row in results if row["outcome"] == "joined"]
    assert len(joined) == 1
    entries = WaitlistEntry.objects.filter(guest=owner, status__in=WaitlistStatus.active_statuses())
    assert entries.count() == 1
    assert operator  # کارمند پذیرش در سناریوی واقعی صف را می‌بیند


def test_parallel_check_in_transitions_once(guest, units, operator) -> None:
    """دو پذیرش موازی روی یک رزرو ⇒ یک انتقال موفق و یک پیام روشن."""
    start, end = lattice_period(start_day=days_ahead(2), index=0, cells=2)
    reservation = reservation_services.create_reservation(
        guest=guest, unit=units["B3"], start=start, end=end, guests_count=2
    )
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT)

    def attempt(_index: int) -> str:
        """تلاش پذیرش مهمان در ترد جداگانه."""
        with suppress(threading.BrokenBarrierError):
            barrier.wait()
        try:
            reservation_services.check_in(reservation=reservation, by=operator)
            return "ok"
        except DomainError:
            return "rejected"
        finally:
            connections.close_all()

    # پذیرش از چند ساعت پیش از آغاز اقامت باز می‌شود (H3).
    with (
        time_machine.travel(start - timedelta(hours=1)),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        results = list(pool.map(attempt, range(2)))

    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.CHECKED_IN
    assert sorted(results) == ["ok", "rejected"]
    assert reservation.events.filter(kind="checked_in").count() == 1


def test_expired_holds_do_not_steal_confirmed_slots(units) -> None:
    """نرمال‌سازی انقضا فقط نگه‌داشت‌های منقضی را می‌بندد، نه رزرو تأییدشده.

    این تست «رقابت قاعده‌ها» را می‌سنجد: فرمان انقضا در برابر رزروی که کاربر
    تازه تأیید کرده است؛ اگر منطق انقضا سهل‌انگار باشد، سانسِ زائر بی‌صدا آزاد
    می‌شود — خطایی که در ایام پیک گران تمام می‌شود.
    """
    guests = _bulk_guests(2)
    start, end = lattice_period(start_day=days_ahead(20), index=0, cells=2)
    unit = units["A1"]
    confirmed = reservation_services.create_reservation(
        guest=guests[0], unit=unit, start=start, end=end, guests_count=2
    )
    # فاصلهٔ دو روزه لازم است: بافر نظافت به «رزرو پشت‌سرهم» اجازه نمی‌دهد.
    hold_start, hold_end = lattice_period(start_day=days_ahead(23), index=0, cells=2)
    hold = reservation_services.create_reservation(
        guest=guests[1], unit=unit, start=hold_start, end=hold_end, guests_count=2
    )
    if hold.status == ReservationStatus.HOLD:
        hold.hold_expires_at = timezone.now() - timedelta(minutes=5)
        hold.save(update_fields=["hold_expires_at"])

    from django.core.management import call_command

    call_command("expire_holds")

    confirmed.refresh_from_db()
    assert confirmed.status == ReservationStatus.CONFIRMED
    assert confirmed.cancelled_at is None
