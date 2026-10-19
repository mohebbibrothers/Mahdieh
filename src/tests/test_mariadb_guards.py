"""نگهبان‌های پایگاه‌داده روی MariaDB — جایگزین‌های قید EXCLUDE و ایندکس جزئی.

این آزمون‌ها همان چیزی را می‌سنجند که در ADR-0020 ادعا شده است:

* تریگرهای عدم‌هم‌پوشانی (رزرو و بلوک) در **درج و ویرایش** کار می‌کنند، بازهٔ
  مجاور را تداخل نمی‌شمارند و خطایشان در جنگو ``IntegrityError`` است؛
* فهرست وضعیت‌های فعالِ منجمد در تریگر با کد زنده یکی است؛
* یکتایی شرطی (ستون تولیدی + ``UNIQUE``) جای ایندکس جزئی را می‌گیرد؛
* collation حساس به حروف و جست‌وجوی ``i*`` غیرحساس است؛
* درایور منطقهٔ زمانی بدون جدول tz کار می‌کند و نگهبان ``mahdieh.E020`` بیدار است.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.utils import timezone
import pytest

from apps.core.checks import TIMEZONE_ERROR, fixed_offset_timezone_check, utc_offset_transitions
from apps.core.db.backends.mariadb.operations import format_offset, numeric_offset
from apps.core.errors import ConflictError
from apps.identity.models import User
from apps.inventory import services as inventory_services
from apps.inventory.choices import BlockKind
from apps.inventory.models import Block
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Reservation
from apps.reservation.services import create_reservation
from apps.waitlist.models import WaitlistEntry
from tests.factories import future_period, make_user

pytestmark = pytest.mark.django_db

TRIGGERS = {
    "reservation_no_overlap_bi": ("INSERT", "reservation_reservation"),
    "reservation_no_overlap_bu": ("UPDATE", "reservation_reservation"),
    "block_no_overlap_bi": ("INSERT", "inventory_block"),
    "block_no_overlap_bu": ("UPDATE", "inventory_block"),
}


def _triggers() -> dict[str, tuple[str, str, str, str]]:
    """تریگرهای نصب‌شده روی پایگاه‌دادهٔ جاری: نام ⇒ (رویداد، جدول، زمان، متن)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT TRIGGER_NAME, EVENT_MANIPULATION, EVENT_OBJECT_TABLE, ACTION_TIMING, "
            "ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE()"
        )
        return {row[0]: (row[1], row[2], row[3], row[4]) for row in cursor.fetchall()}


def _raw_reservation(guest: User, unit, start: datetime, end: datetime, **extra) -> Reservation:
    """درج مستقیم (دور زدن سرویس) — فقط تریگر پایگاه‌داده میان ما و تداخل است."""
    fields = {
        "code": extra.pop("code", f"M-1405-{Reservation.objects.count() + 9000:04d}"),
        "unit": unit,
        "guest": guest,
        "starts_at": start,
        "ends_at": end,
        "block_starts_at": start,
        "block_ends_at": end,
        "cells_count": 2,
        "guests_count": 2,
        "status": ReservationStatus.CONFIRMED,
    }
    fields.update(extra)
    return Reservation.objects.create(**fields)


# ── ۱) تریگرها نصب‌اند و با کد زنده هم‌خوان‌اند ──────────────────────────────


def test_all_overlap_triggers_are_installed_before_write() -> None:
    """هر چهار تریگر (درج/ویرایش × رزرو/بلوک) با زمان BEFORE نصب شده‌اند."""
    installed = _triggers()
    for name, (event, table) in TRIGGERS.items():
        assert name in installed, f"تریگر {name} نصب نشده است"
        assert installed[name][:3] == (event, table, "BEFORE"), name


def test_overlap_trigger_tracks_active_statuses() -> None:
    """وضعیت‌های منجمد در تریگر رزرو == ``ReservationStatus.active_statuses()``.

    اگر روزی وضعیت فعال تازه‌ای افزوده شود و مهاجرت تریگر به‌روز نشود، دو رزرو
    هم‌پوشان با وضعیت تازه از نگهبان رد می‌شدند — این آزمون همان را می‌گیرد.
    """
    expected = {str(status) for status in ReservationStatus.active_statuses()}
    for name in ("reservation_no_overlap_bi", "reservation_no_overlap_bu"):
        body = _triggers()[name][3]
        groups = re.findall(r"status IN \(([^)]*)\)", body)
        assert len(groups) == 2, body  # ردیف تازه + ردیف‌های موجود
        for group in groups:
            assert set(re.findall(r"'([a-z_]+)'", group)) == expected, name


# ── ۲) رفتار تریگر رزرو ─────────────────────────────────────────────────────


def test_trigger_error_surfaces_as_integrity_error_with_code(guest, other_guest, units) -> None:
    """``SIGNAL 23000 / 4025`` ⇒ ``IntegrityError`` با پیام ماشین‌خوان."""
    start, end = future_period(days=20, cells=2)
    _raw_reservation(guest, units["A1"], start, end)
    with pytest.raises(IntegrityError) as excinfo, transaction.atomic():
        _raw_reservation(other_guest, units["A1"], start + timedelta(hours=1), end)
    assert excinfo.value.args[0] == 4025
    assert "no_overlapping_active_reservations" in str(excinfo.value)


def test_adjacent_reservations_do_not_overlap(guest, other_guest, units) -> None:
    """بازهٔ نیم‌باز: پایان یکی = آغاز بعدی ⇒ مجاز (همان معنای ``&&``)."""
    start, end = future_period(days=21, cells=2)
    _raw_reservation(guest, units["A1"], start, end)
    _raw_reservation(other_guest, units["A1"], end, end + (end - start))
    assert Reservation.objects.filter(unit=units["A1"]).count() == 2


def test_other_unit_and_inactive_status_are_not_conflicts(guest, other_guest, units) -> None:
    """واحد دیگر یا رزرو غیرفعال (لغو/منقضی) سانس را نگه نمی‌دارد."""
    start, end = future_period(days=22, cells=2)
    first = _raw_reservation(guest, units["A1"], start, end)
    _raw_reservation(other_guest, units["B1"], start, end)

    Reservation.objects.filter(pk=first.pk).update(status=ReservationStatus.CANCELLED_BY_GUEST)
    third = make_user(phone="09120000881")
    _raw_reservation(third, units["A1"], start, end)
    # رزرو لغوشده هنوز بازه‌اش را دارد (سابقه)، ولی تداخل نمی‌سازد.
    assert Reservation.objects.filter(unit=units["A1"]).count() == 2


def test_update_path_is_guarded_and_ignores_the_row_itself(guest, other_guest, units) -> None:
    """ویرایش هم نگهبان دارد؛ ولی ردیف با خودش تداخل حساب نمی‌شود."""
    start, end = future_period(days=23, cells=2)
    first = _raw_reservation(guest, units["A1"], start, end)
    later_start, later_end = end + timedelta(days=1), end + timedelta(days=1) + (end - start)
    second = _raw_reservation(other_guest, units["A1"], later_start, later_end)

    # ویرایش بی‌ربط روی ردیف فعال نباید با خودش برخورد کند.
    first.guest_note = "یادداشت تازه"
    first.save(update_fields=["guest_note", "updated_at"])

    with pytest.raises(IntegrityError), transaction.atomic():
        Reservation.objects.filter(pk=second.pk).update(
            starts_at=start, ends_at=end, block_starts_at=start, block_ends_at=end
        )
    # فعال‌سازی دوبارهٔ رزرو لغوشده روی سانس اشغال‌شده هم رد می‌شود.
    Reservation.objects.filter(pk=second.pk).update(status=ReservationStatus.CANCELLED_BY_GUEST)
    Reservation.objects.filter(pk=second.pk).update(
        starts_at=start, ends_at=end, block_starts_at=start, block_ends_at=end
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Reservation.objects.filter(pk=second.pk).update(status=ReservationStatus.CONFIRMED)


def test_block_period_not_stay_period_is_what_the_trigger_guards(guest, other_guest, units) -> None:
    """تریگر روی بازهٔ **مسدودکننده** (اقامت + بافر) کار می‌کند، نه فقط اقامت."""
    start, end = future_period(days=24, cells=2)
    _raw_reservation(guest, units["A1"], start, end, block_ends_at=end + timedelta(hours=1))
    with pytest.raises(IntegrityError), transaction.atomic():
        _raw_reservation(other_guest, units["A1"], end, end + (end - start))


def test_service_maps_trigger_race_to_friendly_conflict(guest, other_guest, units) -> None:
    """سرویس: تداخل ⇒ ``ReservationConflictError`` دوستانه، نه ۵۰۰."""
    from apps.core.errors import ReservationConflictError

    start, end = future_period(days=25, cells=2)
    create_reservation(guest=guest, unit=units["A1"], start=start, end=end, guests_count=2)
    with pytest.raises(ReservationConflictError):
        create_reservation(
            guest=other_guest, unit=units["A1"], start=start, end=end, guests_count=2
        )


# ── ۳) رفتار تریگر بلوک ─────────────────────────────────────────────────────


def _block(unit, start: datetime, end: datetime, **extra) -> Block:
    """درج مستقیم بلوک."""
    return Block.objects.create(
        unit=unit, starts_at=start, ends_at=end, kind=BlockKind.MAINTENANCE, reason="آزمون", **extra
    )


def test_unit_blocks_cannot_overlap_but_may_touch(units) -> None:
    """دو بلوک یک واحد هم‌پوشان نیستند؛ مجاورت و واحد دیگر مجاز است."""
    start, end = future_period(days=26, cells=2)
    _block(units["A1"], start, end)
    _block(units["A1"], end, end + timedelta(hours=4))
    _block(units["B1"], start, end)
    with pytest.raises(IntegrityError) as excinfo, transaction.atomic():
        _block(units["A1"], start + timedelta(hours=1), end)
    assert "block_no_overlap_same_unit" in str(excinfo.value)


def test_inactive_block_still_holds_its_period(units) -> None:
    """معنای قید پیشین حفظ شده: ``is_active`` فیلتر نمی‌شود."""
    start, end = future_period(days=27, cells=2)
    _block(units["A1"], start, end, is_active=False)
    with pytest.raises(IntegrityError), transaction.atomic():
        _block(units["A1"], start, end)


def test_global_blocks_are_exempt_from_the_unit_guard(units) -> None:
    """بلوک سراسری (بی‌واحد) مشمول قاعدهٔ «یک واحد» نیست."""
    start, end = future_period(days=28, cells=2)
    _block(None, start, end)
    _block(None, start, end)
    _block(units["A1"], start, end)
    assert Block.objects.filter(unit__isnull=True).count() == 2


def test_block_update_path_is_guarded(units) -> None:
    """جابه‌جایی بلوک روی بلوک دیگرِ همان واحد رد می‌شود."""
    start, end = future_period(days=29, cells=2)
    _block(units["A1"], start, end)
    later = _block(units["A1"], end + timedelta(days=1), end + timedelta(days=2))
    with pytest.raises(IntegrityError), transaction.atomic():
        Block.objects.filter(pk=later.pk).update(starts_at=start, ends_at=end)


def test_block_service_maps_overlap_to_conflict(manager, units) -> None:
    """سرویس بلوک: تداخل ⇒ ``ConflictError(code="block_overlap")``."""
    start, end = future_period(days=30, cells=2)
    common = {"kind": BlockKind.MAINTENANCE, "reason": "تعمیر", "unit": units["A2"]}
    inventory_services.create_block(start=start, end=end, actor=manager, **common)
    with pytest.raises(ConflictError) as excinfo:
        inventory_services.create_block(start=start, end=end, actor=manager, **common)
    assert excinfo.value.code == "block_overlap"


# ── ۴) یکتایی شرطی با ستون تولیدی (جانشین ایندکس جزئی) ───────────────────────


def _waitlist(guest: User, unit, *, status: str = "waiting") -> WaitlistEntry:
    """درج مستقیم درخواست فهرست انتظار."""
    start, end = future_period(days=31, cells=2)
    return WaitlistEntry.objects.create(
        guest=guest,
        unit=unit,
        starts_at=start,
        ends_at=end,
        cells_count=2,
        guests_count=2,
        status=status,
    )


def test_one_active_waitlist_entry_per_guest_is_enforced(guest, units) -> None:
    """دو درخواست فعال برای یک زائر ⇒ رد؛ درخواست بسته‌شده محدودیت نمی‌سازد."""
    first = _waitlist(guest, units["A1"])
    with pytest.raises(IntegrityError), transaction.atomic():
        _waitlist(guest, units["B1"])
    WaitlistEntry.objects.filter(pk=first.pk).update(status="withdrawn")
    _waitlist(guest, units["B1"])
    WaitlistEntry.objects.filter(guest=guest).update(status="withdrawn")
    # چند درخواست بسته‌شده کنار هم مجازند (NULL در UNIQUE تکراری‌پذیر است).
    assert WaitlistEntry.objects.filter(guest=guest, active_guest_key__isnull=True).count() == 2


def test_conditional_unique_is_validated_by_full_clean(guest, units) -> None:
    """فرم‌های پنل مدیریت (``full_clean``) هم یکتایی شرطی را پیش از ذخیره می‌بینند."""
    _waitlist(guest, units["A1"])
    start, end = future_period(days=32, cells=2)
    duplicate = WaitlistEntry(
        guest=guest,
        unit=units["B1"],
        starts_at=start,
        ends_at=end,
        cells_count=2,
        guests_count=2,
        status="waiting",
    )
    with pytest.raises(DjangoValidationError):
        duplicate.full_clean()


def test_national_id_unique_only_when_present(guest) -> None:
    """کد ملی تکراری رد می‌شود؛ ولی چند کاربرِ بی‌کد ملی کنار هم مجازند."""
    bare = make_user(phone="09120000901", complete_profile=False)
    make_user(phone="09120000902", complete_profile=False)
    assert guest.national_id_blind
    assert User.objects.filter(national_id_key__isnull=True).count() >= 2
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=bare.pk).update(national_id_blind=guest.national_id_blind)


# ── ۵) collation و جست‌وجو ──────────────────────────────────────────────────


def test_exact_lookup_is_case_sensitive_and_icontains_is_not(guest, units) -> None:
    """``code="m-…"`` (دقیق) ≠ ``M-…``؛ ولی ``iexact``/``icontains`` پیدا می‌کنند."""
    start, end = future_period(days=33, cells=2)
    reservation = _raw_reservation(guest, units["A1"], start, end, code="M-1405-7777")
    assert not Reservation.objects.filter(code="m-1405-7777").exists()
    assert Reservation.objects.get(code__iexact="m-1405-7777") == reservation
    assert Reservation.objects.filter(code__icontains="m-1405-77").count() == 1


def test_database_uses_accent_and_case_sensitive_collation() -> None:
    """پایگاه‌دادهٔ آزمون با همان collation تولید ساخته شده است."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT @@collation_database, @@character_set_database")
        collation, charset = cursor.fetchone()
    assert charset == "utf8mb4"
    assert collation.endswith("_as_cs"), collation


# ── ۶) منطقهٔ زمانی بدون جدول tz ─────────────────────────────────────────────


def test_numeric_offset_never_depends_on_server_tz_tables() -> None:
    """نام منطقه ⇒ اختلاف عددی؛ ورودی عددی دست‌نخورده می‌ماند."""
    assert numeric_offset("UTC") == "+00:00"
    assert numeric_offset("Asia/Tehran") == "+03:30"
    assert numeric_offset("+05:45") == "+05:45"
    assert format_offset(timedelta(hours=-2, minutes=-30)) == "-02:30"


def test_date_lookup_uses_local_day(guest, units) -> None:
    """``__date`` با روز **محلی** کار می‌کند (۰۰:۳۰ تهران = روز قبل به UTC)."""
    local_midnight = timezone.localtime().replace(hour=0, minute=30, second=0, microsecond=0)
    moment = local_midnight + timedelta(days=40)
    start, end = moment, moment + timedelta(hours=12)
    reservation = _raw_reservation(guest, units["A1"], start, end)
    assert moment.astimezone(UTC).date() != moment.date()  # واقعاً مرز روز را می‌آزماییم
    assert Reservation.objects.filter(starts_at__date=moment.date()).get() == reservation


def test_fixed_offset_check_passes_for_tehran() -> None:
    """ایران از ۱۴۰۱ ساعت تابستانی ندارد ⇒ چک سبز."""
    assert utc_offset_transitions("Asia/Tehran") == []
    assert fixed_offset_timezone_check() == []


@override_settings(TIME_ZONE="Europe/Paris")
def test_fixed_offset_check_blocks_dst_zones() -> None:
    """منطقهٔ دارای ساعت تابستانی ⇒ خطای ``mahdieh.E020``."""
    issues = fixed_offset_timezone_check()
    assert [issue.id for issue in issues] == [TIMEZONE_ERROR]


@override_settings(TIME_ZONE="Mars/Olympus")
def test_fixed_offset_check_rejects_unknown_zone() -> None:
    """منطقهٔ ناشناخته هم خطا است، نه استثنای مبهم."""
    issues = fixed_offset_timezone_check()
    assert [issue.id for issue in issues] == [TIMEZONE_ERROR]
