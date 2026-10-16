"""تست‌های سرویس رزرو — نگهبان‌ها، ماشین حالت و آزادسازی سانس.

این فایل، «قرارداد رفتار» دامنهٔ رزرو است: هر ثابت I2–I14 یک تست دارد.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
import pytest
import time_machine

from apps.audit.choices import AuditAction
from apps.audit.models import AuditRecord
from apps.core.errors import (
    ConflictError,
    PermissionDeniedError,
    PolicyViolationError,
    StaleResourceError,
)
from apps.core.outbox import OutboxEvent
from apps.notifications.models import SmsDelivery
from apps.reservation import selectors
from apps.reservation.choices import (
    CancelReasonKind,
    ReservationEventKind,
    ReservationSource,
    ReservationStatus,
)
from apps.reservation.policy import set_policy
from apps.reservation.services import (
    build_quote,
    cancel_reservation,
    check_in,
    check_out,
    create_reservation,
    expire_holds,
    mark_no_show,
    move_reservation,
)
from tests.factories import (
    days_ahead,
    future_period,
    lattice_period,
    make_staff,
    make_user,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

CODE_PATTERN = r"^M-\d{4}-\d{4}$"


def _book(guest, unit, **kwargs):
    """رزرو امن در آینده — کوتاه‌نویس تست‌ها."""
    start, end = kwargs.pop("period", future_period(days=5, cells=2))
    return create_reservation(
        guest=guest,
        unit=unit,
        start=start,
        end=end,
        guests_count=kwargs.pop("guests_count", 2),
        **kwargs,
    )


# ─── ثبت رزرو ────────────────────────────────────────────────────────────────


def test_create_reservation_happy_path(guest, units) -> None:
    """رزرو موفق: کد خوانا، بافر نظافت، رخداد، Outbox، حسابرسی و پیامک."""
    start, end = future_period(days=5, cells=2)
    reservation = create_reservation(
        guest=guest, unit=units["A1"], start=start, end=end, guests_count=3
    )
    assert reservation.code.startswith("M-")
    assert reservation.code[2:6].isdigit()
    assert reservation.status == ReservationStatus.CONFIRMED
    assert reservation.cells_count == 2
    assert reservation.duration_label == "یک شبانه‌روز کامل"
    assert reservation.stay_period.lower == start
    assert reservation.block_period.lower == start - timedelta(minutes=60)
    assert reservation.block_period.upper == end + timedelta(minutes=60)
    assert reservation.events.filter(kind=ReservationEventKind.CREATED).count() == 1
    assert OutboxEvent.all_objects.filter(event_type="reservation.confirmed").count() == 1
    assert AuditRecord.all_objects.filter(action=AuditAction.RESERVATION_CREATED).exists()
    assert SmsDelivery.objects.filter(template_key="booking_confirmed").exists()


def test_codes_increment_per_jalali_year(guest, other_guest, units) -> None:
    """کدها پشت‌سرهم و یکتا هستند (شمارندهٔ قفل‌شده)."""
    first = _book(guest, units["A1"])
    second = _book(other_guest, units["B1"])
    year = first.code.split("-")[1]
    assert first.code == f"M-{year}-0001"
    assert second.code == f"M-{year}-0002"


def test_minimum_and_maximum_stay_are_enforced(guest, units) -> None:
    """مرزهای I2: یک نیم‌روز مجاز، پانزده نیم‌روز ممنوع."""
    half_day = _book(guest, units["A1"], period=future_period(days=5, cells=1))
    assert half_day.cells_count == 1

    mother = make_user(phone="09120000031")
    with pytest.raises(PolicyViolationError) as excinfo:
        _book(mother, units["B1"], period=future_period(days=5, cells=15))
    assert "cells_out_of_range" in [item["code"] for item in excinfo.value.extensions["violations"]]

    week = _book(
        make_user(phone="09120000032"), units["A2"], period=future_period(days=5, cells=14)
    )
    assert week.cells_count == 14


def test_off_grid_request_is_rejected(guest, units) -> None:
    """رزرو باید روی مرز شبکه باشد (I3) — مگر سیاست خلافش را بگوید."""
    start, end = future_period(days=5, cells=1)
    with pytest.raises(PolicyViolationError) as excinfo:
        create_reservation(
            guest=guest,
            unit=units["A1"],
            start=start + timedelta(hours=2),
            end=end + timedelta(hours=2),
            guests_count=2,
        )
    assert "off_grid" in [item["code"] for item in excinfo.value.extensions["violations"]]


def test_guest_count_cannot_exceed_unit_capacity(guest, units) -> None:
    """ظرفیت واحد سقف سخت تعداد نفرات است (I4)."""
    with pytest.raises(PolicyViolationError) as excinfo:
        _book(guest, units["A1"], guests_count=9)
    assert "capacity_exceeded" in [item["code"] for item in excinfo.value.extensions["violations"]]


def test_profile_must_be_complete_for_web_booking(units) -> None:
    """زائر بدون پروفایل کامل، از مسیر وب رزرو نمی‌کند (I5)."""
    incomplete = make_user(phone="09120000033", complete_profile=False)
    with pytest.raises(PolicyViolationError) as excinfo:
        _book(incomplete, units["A1"])
    assert "profile_incomplete" in [item["code"] for item in excinfo.value.extensions["violations"]]


def test_past_and_horizon_and_lead_time_guards(guest, units) -> None:
    """I7/I8/I9: گذشته، افق ۹۰ روز و حداقل فاصله از اکنون."""
    with pytest.raises(PolicyViolationError) as past:
        create_reservation(
            guest=guest,
            unit=units["A1"],
            start=timezone.now() - timedelta(days=1),
            end=timezone.now() - timedelta(hours=12),
            guests_count=2,
        )
    codes = [item["code"] for item in past.value.extensions["violations"]]
    assert "in_past" in codes

    far_start, far_end = lattice_period(start_day=days_ahead(120), cells=2)
    with pytest.raises(PolicyViolationError) as horizon:
        create_reservation(
            guest=guest, unit=units["A1"], start=far_start, end=far_end, guests_count=2
        )
    assert "beyond_horizon" in [item["code"] for item in horizon.value.extensions["violations"]]

    soon_start, soon_end = lattice_period(start_day=timezone.localdate(), index=1, cells=1)
    if soon_start < timezone.now() + timedelta(hours=2):
        with pytest.raises(PolicyViolationError) as soon:
            create_reservation(
                guest=guest, unit=units["A1"], start=soon_start, end=soon_end, guests_count=2
            )
        assert "too_soon" in [item["code"] for item in soon.value.extensions["violations"]]


def test_active_reservation_limit_and_quota_window(guest, other_guest, units) -> None:
    """I10/I11: سقف رزرو فعال و سهمیهٔ نیم‌روز در پنجرهٔ چرخشی."""
    _book(guest, units["A1"])
    with pytest.raises(PolicyViolationError) as excinfo:
        _book(guest, units["B1"])
    assert "active_limit" in [item["code"] for item in excinfo.value.extensions["violations"]]

    # سهمیهٔ پنجره‌ای برای کاربر بدون رزرو فعال: ۱۴ نیم‌روز در ۳۰ روز
    user = make_user(phone="09120000034")
    _book(user, units["B2"], period=future_period(days=6, cells=14))
    late = lattice_period(start_day=days_ahead(50), cells=2)[0]
    # رزرو دوم برای همان کاربر ابتدا با سقف رزرو فعال برخورد می‌کند؛ پس سقف
    # فعال را برای این سناریو آزاد می‌کنیم تا سهمیهٔ پنجره دیده شود.
    set_policy(
        key="reservation.max_active_per_user",
        value=2,
        actor=make_staff(phone="09120000035"),
        reason="تست سهمیهٔ پنجره‌ای",
    )
    quote = build_quote(
        unit=units["B3"],
        start=late,
        end=lattice_period(start_day=days_ahead(50), cells=2)[1],
        guests_count=2,
        guest=user,
    )
    assert quote.allowed is False
    assert "window_quota" in [item["code"] for item in quote.violations]


def test_gap_between_bookings_is_enforced(guest, units) -> None:
    """فاصلهٔ ۲۴ ساعته بین دو رزرو متوالی (قاعدهٔ ضدچرخش)."""
    start, end = future_period(days=5, cells=2)
    _book(guest, units["A1"], period=(start, end))
    set_policy(
        key="reservation.max_active_per_user",
        value=3,
        actor=make_staff(phone="09120000036"),
        reason="تست فاصلهٔ بین رزروها",
    )
    right_after = lattice_period(start_day=timezone.localtime(end).date(), cells=1)
    quote = build_quote(
        unit=units["B1"], start=right_after[0], end=right_after[1], guests_count=2, guest=guest
    )
    assert "min_gap" in [item["code"] for item in quote.violations]


# ─── تصادم و جایگزین ─────────────────────────────────────────────────────────


def test_same_slot_cannot_be_booked_twice(guest, other_guest, units) -> None:
    """سناریوی S2 در حالت سری: رزرو دوم پیام دامنه‌ای + بازهٔ جایگزین می‌گیرد."""
    start, end = future_period(days=7, cells=2)
    _book(guest, units["A1"], period=(start, end))
    from apps.core.errors import ReservationConflictError

    with pytest.raises(ReservationConflictError) as excinfo:
        _book(other_guest, units["A1"], period=(start, end))
    alternatives = excinfo.value.extensions["alternatives"]
    assert alternatives, "باید بازه‌های جایگزین پیشنهاد شود"
    assert alternatives[0]["unit_code"] == "A1"
    assert alternatives[0]["is_same_unit"] is True


def test_different_units_same_time_are_independent(guest, other_guest, units) -> None:
    """یک سانس روی دو واحد مختلف، دو رزرو مستقل است."""
    start, end = future_period(days=8, cells=2)
    first = _book(guest, units["A1"], period=(start, end))
    second = _book(other_guest, units["B1"], period=(start, end))
    assert first.pk != second.pk


def test_quote_reports_all_violations_at_once(guest, units) -> None:
    """سبد رزرو: کاربر به‌جای پنج تلاش، همهٔ دلایل را یک‌جا می‌بیند."""
    quote = build_quote(
        unit=units["A1"],
        start=timezone.now() - timedelta(days=2),
        end=timezone.now() - timedelta(days=1),
        guests_count=99,
        guest=guest,
    )
    codes = [item["code"] for item in quote.violations]
    assert quote.allowed is False
    assert "in_past" in codes
    assert {"capacity_exceeded", "guests_invalid"} & set(codes)


# ─── لغو ─────────────────────────────────────────────────────────────────────


def test_cancel_frees_the_slot_immediately(guest, other_guest, units) -> None:
    """لغو زائر ⇒ سانس همان لحظه آزاد و قابل رزرو دوباره می‌شود."""
    start, end = future_period(days=9, cells=2)
    reservation = _book(guest, units["A1"], period=(start, end))
    cancel_reservation(reservation=reservation, by=guest, reason="برنامه عوض شد")

    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.CANCELLED_BY_GUEST
    assert reservation.cancelled_at is not None
    assert OutboxEvent.all_objects.filter(event_type="reservation.cancelled").exists()
    assert AuditRecord.all_objects.filter(action=AuditAction.RESERVATION_CANCELLED).exists()

    replacement = _book(other_guest, units["A1"], period=(start, end))
    assert replacement.status == ReservationStatus.CONFIRMED


def test_cancel_by_other_guest_is_denied(guest, other_guest, units) -> None:
    """هیچ‌کس رزرو دیگری را لغو نمی‌کند."""
    reservation = _book(guest, units["A1"])
    with pytest.raises(PermissionDeniedError):
        cancel_reservation(reservation=reservation, by=other_guest)


def test_staff_cancel_requires_reason_and_allows_release(units, operator) -> None:
    """لغو کارمندی فقط با دلیل؛ رزرو غیرفعال دیگر لغو نمی‌شود."""
    guest = make_user(phone="09120000041")
    reservation = _book(guest, units["A1"])
    with pytest.raises(PolicyViolationError):
        cancel_reservation(reservation=reservation, by=operator, is_staff=True, reason="")
    updated = cancel_reservation(
        reservation=reservation,
        by=operator,
        is_staff=True,
        reason="اصلاح رزرو تکراری زائر",
        reason_kind=CancelReasonKind.DUPLICATE,
    )
    assert updated.status == ReservationStatus.CANCELLED_BY_STAFF
    with pytest.raises(ConflictError):
        cancel_reservation(reservation=updated, by=operator, is_staff=True, reason="دوباره لغو")


def test_late_cancel_marks_flag_and_starts_cooldown(units) -> None:
    """لغو دیرهنگام: علامت‌گذاری + خنک‌شدن موقت برای رزرو تازه."""
    guest = make_user(phone="09120000042")
    start, end = future_period(days=5, cells=1)
    reservation = _book(guest, units["A1"], period=(start, end))
    with time_machine.travel(start - timedelta(hours=3), tick=False):
        cancel_reservation(reservation=reservation, by=guest, reason="دیر شد")
        reservation.refresh_from_db()
        assert reservation.is_late_cancel is True
        quote = build_quote(
            unit=units["B1"],
            start=start + timedelta(days=2),
            end=end + timedelta(days=2),
            guests_count=2,
            guest=guest,
        )
    assert "cooldown" in [item["code"] for item in quote.violations]


# ─── جابه‌جایی و ماشین حالت کارکنان ────────────────────────────────────────────


def test_move_supersedes_old_reservation(units, operator) -> None:
    """سناریوی S4: رزرو قدیم جانشین می‌شود و زنجیره حفظ می‌گردد."""
    guest = make_user(phone="09120000043")
    original = _book(guest, units["A1"])
    target_start, target_end = future_period(days=12, cells=2)

    replacement = move_reservation(
        reservation=original,
        unit=units["B2"],
        start=target_start,
        end=target_end,
        by=operator,
        reason="درخواست زائر برای تغییر واحد",
    )
    original.refresh_from_db()
    assert original.status == ReservationStatus.SUPERSEDED
    assert original.superseded_by_id == replacement.pk
    assert replacement.status == ReservationStatus.CONFIRMED
    assert replacement.unit.code == "B2"
    assert original.events.filter(kind=ReservationEventKind.MOVED).count() == 1
    assert OutboxEvent.all_objects.filter(event_type="reservation.superseded").exists()
    assert AuditRecord.all_objects.filter(action=AuditAction.RESERVATION_MOVED).exists()
    # سانس پیشین آزاد شده است
    assert not selectors.active_conflicts(
        unit=units["A1"], start=original.starts_at, end=original.ends_at
    ).exists()


def test_move_requires_reason_and_fresh_version(units, operator) -> None:
    """جابه‌جایی: دلیل اجباری و کنترل نسخه (قفل خوش‌بینانه)."""
    guest = make_user(phone="09120000044")
    original = _book(guest, units["A1"])
    target_start, target_end = future_period(days=13, cells=2)
    with pytest.raises(PolicyViolationError):
        move_reservation(
            reservation=original,
            unit=units["B1"],
            start=target_start,
            end=target_end,
            by=operator,
            reason="کم",
        )
    with pytest.raises(StaleResourceError):
        move_reservation(
            reservation=original,
            unit=units["B1"],
            start=target_start,
            end=target_end,
            by=operator,
            reason="جابه‌جایی به اتاق بزرگ",
            expected_version=original.version + 5,
        )


def test_check_in_and_check_out_lifecycle(units, operator) -> None:
    """پذیرش و تخلیه: انتقال‌های مجاز و رخدادهای ثبت‌شده."""
    guest = make_user(phone="09120000045")
    start, end = future_period(days=5, cells=2)
    reservation = _book(guest, units["A1"], period=(start, end))

    with time_machine.travel(start + timedelta(hours=1), tick=False):
        checked_in = check_in(reservation=reservation, by=operator)
        assert checked_in.status == ReservationStatus.CHECKED_IN
        assert checked_in.checked_in_at is not None
        completed = check_out(reservation=checked_in, by=operator)
        assert completed.status == ReservationStatus.COMPLETED
        assert completed.checked_out_at is not None

    assert OutboxEvent.all_objects.filter(event_type="reservation.checked_in").exists()
    assert reservation.events.count() == 3  # ایجاد، پذیرش، تخلیه


def test_check_in_guards(units, operator, guest) -> None:
    """پذیرش روی رزرو لغوشده یا توسط زائر مجاز نیست."""
    reservation = _book(guest, units["A1"])
    with pytest.raises(PermissionDeniedError):
        check_in(reservation=reservation, by=guest)
    cancel_reservation(reservation=reservation, by=guest)
    with pytest.raises(ConflictError):
        check_in(reservation=reservation, by=operator)


def test_no_show_requires_grace_window(units, operator) -> None:
    """سناریوی S5: عدم‌حضور پس از پایان مهلت ورود ثبت می‌شود."""
    guest = make_user(phone="09120000046")
    start, end = future_period(days=5, cells=1)
    reservation = _book(guest, units["A1"], period=(start, end))

    with pytest.raises(ConflictError):
        mark_no_show(reservation=reservation, by=operator)

    with time_machine.travel(start + timedelta(hours=8), tick=False):
        updated = mark_no_show(reservation=reservation, by=operator, reason="زائر مراجعه نکرد")
    assert updated.status == ReservationStatus.NO_SHOW
    assert AuditRecord.all_objects.filter(action=AuditAction.RESERVATION_NO_SHOW).exists()
    assert OutboxEvent.all_objects.filter(event_type="reservation.no_show").exists()


def test_holds_expire_and_release_slot(units) -> None:
    """نگه‌داشت موقت: انقضا ⇒ آزادسازی سانس برای دیگران."""
    manager = make_staff(phone="09120000047")
    set_policy(
        key="reservation.holds_enabled",
        value=True,
        actor=manager,
        reason="فعال‌سازی نگه‌داشت برای تست",
    )
    first_guest = make_user(phone="09120000048")
    second_guest = make_user(phone="09120000049")
    start, end = future_period(days=6, cells=2)
    hold = _book(first_guest, units["A1"], period=(start, end))
    assert hold.status == ReservationStatus.HOLD
    assert hold.hold_expires_at is not None

    with time_machine.travel(hold.hold_expires_at + timedelta(minutes=1), tick=False):
        released = expire_holds()
        hold.refresh_from_db()
    assert released == 1
    assert hold.status == ReservationStatus.EXPIRED
    assert OutboxEvent.all_objects.filter(event_type="hold.expired").exists()

    after = _book(second_guest, units["A1"], period=(start, end))
    assert after.status == ReservationStatus.HOLD


def test_staff_can_book_by_phone_for_new_guest(units, operator) -> None:
    """مسیر تلفنی: ساخت حساب مهمان و ثبت رزرو توسط کارمند."""
    from apps.reservation.services import staff_create_reservation

    start, end = future_period(days=11, cells=2)
    reservation = staff_create_reservation(
        phone="09120000050",
        unit=units["A1"],
        start=start,
        end=end,
        guests_count=4,
        by=operator,
        source=ReservationSource.PHONE,
        first_name="علی",
        last_name="اکبری",
        province="tehran",
        city="تهران",
    )
    assert reservation.source == ReservationSource.PHONE
    assert reservation.guest.phone == "+989120000050"
    assert reservation.guest.is_profile_complete is True
    assert reservation.created_by_id == operator.pk


def test_staff_path_can_book_without_complete_profile(units, operator) -> None:
    """استثنای مستند مسیر پذیرش: نبود پروفایل کامل مانع رزرو تلفنی نیست."""
    start, end = future_period(days=15, cells=1)
    existing = make_user(phone="09120000051", complete_profile=False)
    from apps.reservation.services import staff_create_reservation

    reservation = staff_create_reservation(
        phone="09120000051",
        unit=units["B1"],
        start=start,
        end=end,
        guests_count=2,
        by=operator,
    )
    assert reservation.guest_id == existing.pk
    assert reservation.status == ReservationStatus.CONFIRMED


def test_reservation_list_scopes(guest, units) -> None:
    """دامنه‌های فهرست رزرو: آینده و گذشته."""
    _book(guest, units["A1"])
    assert selectors.reservations_for_user(guest).count() == 1
    assert selectors.reservations_for_user(guest, scope=selectors.PAST).count() == 0


def test_sms_confirmation_is_recorded_without_plain_code(guest, units) -> None:
    """پیامک تأیید ثبت می‌شود و متن خام (کد) در دیتابیس نمی‌ماند."""
    reservation = _book(guest, units["A1"])
    delivery = SmsDelivery.objects.filter(template_key="booking_confirmed").first()
    assert delivery is not None
    from apps.notifications.providers import MockSmsProvider

    sent = MockSmsProvider.last_for(reservation.guest.phone)
    assert sent is not None
    assert reservation.code in sent.body  # زائر کد رزرو را می‌بیند
    assert reservation.code not in delivery.body_redacted  # نسخهٔ ذخیره‌شده برای ممیزی پوشانده است
    assert delivery.phone_blind == ""  # تلفن خام ذخیره نمی‌شود
    assert delivery.phone_masked.endswith(reservation.guest.phone[-4:])
