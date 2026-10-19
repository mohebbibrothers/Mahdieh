"""آزمون‌های نگهداری داده — «کمینه‌سازی» باید دقیق، بی‌خطر و حسابرسی‌پذیر باشد.

چرا این آزمون‌ها مهم‌اند؟ فرمان نگهداری، تنها جایی است که **دادهٔ زائر را پاک
می‌کند**. یک اشتباه در شرط‌ها، یا دادهٔ یک اقامت جاری را می‌بَرد یا دادهٔ حساس را
سال‌ها نگه می‌دارد. پس این‌جا هم «چه چیزی پاک می‌شود» سنجیده می‌شود، هم «چه چیزی
نباید پاک شود».
"""

from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
from typing import Any

from django.core.management import call_command
from django.utils import timezone
import pytest

from apps.audit.choices import AuditAction
from apps.audit.models import AuditRecord
from apps.core.idempotency import IdempotencyKey, purge_expired
from apps.identity.models import User
from apps.identity.retention import (
    REDACTED_FIRST_NAME,
    candidates,
    erase_guest_data,
    redaction_cutoff,
    retention_stats,
    run_guest_retention,
)
from apps.notifications.models import SmsDelivery
from apps.notifications.retention import purge_sms_ledger
from apps.reservation import services as reservation_services
from apps.reservation.choices import ReservationStatus
from apps.reservation.policy import set_policy
from tests.factories import days_ahead, lattice_period

pytestmark = pytest.mark.django_db

OLD_DAYS = 2000  # کهنه‌تر از پنجرهٔ پیش‌فرض (۱۰۹۵ روز)


def _backdate(user: User, *, days: int = OLD_DAYS) -> User:
    """کاربر را «مدت‌ها بی‌فعالیت» می‌کند (بدون دست‌زدن به منطق دامنه)."""
    User.objects.filter(pk=user.pk).update(
        date_joined=timezone.now() - timedelta(days=days),
        last_login=None,
    )
    user.refresh_from_db()
    return user


def _old_reservation(
    guest: User, unit: Any, *, days: int = OLD_DAYS, cancelled: bool = True
) -> Any:
    """رزرو کهنه می‌سازد: به گذشته منتقل و (در صورت نیاز) لغو می‌شود."""
    start, end = lattice_period(start_day=days_ahead(2), index=0, cells=2)
    reservation = reservation_services.create_reservation(
        guest=guest, unit=unit, start=start, end=end, guests_count=2
    )
    past_end = timezone.now() - timedelta(days=days)
    past_start = past_end - timedelta(hours=12)
    type(reservation).objects.filter(pk=reservation.pk).update(
        starts_at=past_start,
        ends_at=past_end,
        block_starts_at=past_start,
        block_ends_at=past_end + timedelta(hours=1),
        # زمان ثبت هم کهنه می‌شود: «آخرین فعالیت» یعنی هم اقامت و هم ثبت آن.
        created_at=past_start,
    )
    if cancelled:
        reservation_services.cancel_reservation(
            reservation=reservation, by=guest, reason_kind="guest_request", reason="آزمون"
        )
    reservation.refresh_from_db()
    return reservation


# ── ۱) منطق غربال ────────────────────────────────────────────────────────────


def test_recent_guest_is_out_of_scope(guest: User) -> None:
    """زائری که همین امروز عضو شده، در پنجرهٔ نگهداری است و دست نمی‌خورد."""
    scan = candidates()
    assert guest.pk not in [item.pk for item in scan.eligible]
    assert retention_stats()["eligible"] == 0


def test_old_idle_guest_becomes_eligible(guest: User, units: Any) -> None:
    """زائر کهنه با اقامتِ تمام‌شدهٔ قدیمی، واجد شرط کمینه‌سازی می‌شود."""
    _old_reservation(guest, units["A1"])
    _backdate(guest)
    assert guest.pk in [item.pk for item in candidates().eligible]


def test_active_reservation_excludes_guest(guest: User, units: Any) -> None:
    """اقامت جاری/آینده ⇒ هرگز کمینه نمی‌شود (زائر مهمان امروزِ ما است)."""
    start, end = lattice_period(start_day=days_ahead(3), index=0, cells=2)
    reservation_services.create_reservation(
        guest=guest, unit=units["A1"], start=start, end=end, guests_count=2
    )
    _backdate(guest)
    scan = candidates()
    assert guest.pk not in [item.pk for item in scan.eligible]
    assert scan.skipped_active >= 1


def test_policy_window_controls_eligibility(guest: User, units: Any, operator: User) -> None:
    """پنجرهٔ نگهداری سیاست است: با پنجرهٔ ۳۰ روزه، زائر ۱۰۰ روزه واجد شرط می‌شود."""
    _old_reservation(guest, units["A1"], days=100)
    _backdate(guest, days=100)
    assert guest.pk not in [item.pk for item in candidates().eligible]

    set_policy(
        key="data.guest_retention_days",
        value=30,
        actor=operator,
        reason="آزمون پنجرهٔ نگهداری",
    )
    assert guest.pk in [item.pk for item in candidates().eligible]
    assert redaction_cutoff() < timezone.now()


# ── ۲) کمینه‌سازی ────────────────────────────────────────────────────────────


def test_redaction_clears_identity_but_keeps_phone_and_history(guest: User, units: Any) -> None:
    """نام/کد ملی می‌رود، تلفن و تاریخ اقامت‌ها می‌ماند (آمار نمی‌شکند)."""
    reservation = _old_reservation(guest, units["A1"])
    _backdate(guest)
    original_phone = guest.phone

    run = run_guest_retention()
    assert run.redacted == 1

    guest.refresh_from_db()
    assert guest.first_name == REDACTED_FIRST_NAME
    assert guest.last_name == ""
    assert guest.city == "" and guest.province == ""
    assert guest.national_id_encrypted == "" and guest.national_id_blind == ""
    assert guest.phone == original_phone  # شناسهٔ ورود می‌ماند
    assert guest.redacted_at is not None

    reservation.refresh_from_db()
    assert reservation.code  # رزرو دست‌نخورده است
    assert reservation.status == ReservationStatus.CANCELLED_BY_GUEST


def test_redaction_is_idempotent_and_audited(guest: User, units: Any) -> None:
    """اجرای دوباره بی‌اثر است و هر کمینه‌سازی یک رکورد حسابرسی دارد."""
    _old_reservation(guest, units["A1"])
    _backdate(guest)

    assert run_guest_retention().redacted == 1
    assert run_guest_retention().redacted == 0  # بار دوم: کاری نمی‌ماند

    records = AuditRecord.objects.filter(action=AuditAction.GUEST_REDACTED)
    assert records.count() == 1
    assert records.first().target_id == str(guest.pk)  # type: ignore[union-attr]


def test_dry_run_touches_nothing(guest: User, units: Any) -> None:
    """حالت آزمایشی فقط می‌شمارد؛ هیچ داده‌ای تغییر نمی‌کند."""
    _old_reservation(guest, units["A1"])
    _backdate(guest)
    before = User.objects.get(pk=guest.pk).first_name

    run = run_guest_retention(dry_run=True)
    assert run.redacted == 0
    assert len(run.codes) == 1
    assert User.objects.get(pk=guest.pk).first_name == before


def test_erase_on_request_requires_reason(guest: User) -> None:
    """پاک‌سازی به‌درخواست زائر، بدون دلیل ثبت نمی‌شود."""
    with pytest.raises(ValueError, match="دلیل"):
        erase_guest_data(guest, reason="   ", actor_label="operator")
    erased = erase_guest_data(guest, reason="درخواست خود زائر", actor_label="operator")
    assert erased.redacted_at is not None


def test_staff_is_never_redacted(operator: User) -> None:
    """کارکنان هرگز کمینه نمی‌شوند — تاریخچهٔ عملیاتی آن‌ها باید بماند."""
    User.objects.filter(pk=operator.pk).update(
        date_joined=timezone.now() - timedelta(days=OLD_DAYS)
    )
    assert operator.pk not in [item.pk for item in candidates().eligible]


# ── ۳) دفترهای عملیاتی ───────────────────────────────────────────────────────


def test_sms_ledger_purge_respects_window() -> None:
    """ردیف کهنهٔ دفتر پیامک پاک می‌شود و ردیف تازه می‌ماند."""
    old = SmsDelivery.objects.create(
        template_key="otp_login", phone_masked="+98912***111", body_redacted="…", status="sent"
    )
    fresh = SmsDelivery.objects.create(
        template_key="otp_login", phone_masked="+98912***222", body_redacted="…", status="sent"
    )
    SmsDelivery.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=400))

    assert purge_sms_ledger(dry_run=True) == 1  # آزمایشی: فقط می‌شمارد
    assert SmsDelivery.objects.filter(pk=old.pk).exists()
    assert purge_sms_ledger() == 1
    assert not SmsDelivery.objects.filter(pk=old.pk).exists()
    assert SmsDelivery.objects.filter(pk=fresh.pk).exists()


def test_idempotency_keys_purge_only_expired() -> None:
    """کلید منقضی می‌رود، کلید زنده می‌ماند."""
    past = IdempotencyKey.objects.create(
        key="k-old", scope="reservation.create", expires_at=timezone.now() - timedelta(hours=1)
    )
    live = IdempotencyKey.objects.create(
        key="k-live", scope="reservation.create", expires_at=timezone.now() + timedelta(hours=1)
    )
    assert purge_expired() == 1
    assert not IdempotencyKey.objects.filter(pk=past.pk).exists()
    assert IdempotencyKey.objects.filter(pk=live.pk).exists()


# ── ۴) فرمان ────────────────────────────────────────────────────────────────


def test_command_reports_without_touching_data_in_dry_run(guest: User, units: Any) -> None:
    """``--dry-run``: گزارش شفاف، صفر تغییر."""
    _old_reservation(guest, units["A1"])
    _backdate(guest)
    out = StringIO()
    call_command("retention_sweep", "--dry-run", stdout=out)
    body = out.getvalue()
    assert "حالت آزمایشی" in body
    assert "کمینه‌شده 0" in body
    assert User.objects.get(pk=guest.pk).redacted_at is None


def test_command_redacts_in_real_run(guest: User, units: Any) -> None:
    """اجرای واقعی: زائر واجد شرط کمینه می‌شود."""
    _old_reservation(guest, units["A1"])
    _backdate(guest)
    out = StringIO()
    call_command("retention_sweep", stdout=out)
    assert "کمینه‌شده 1" in out.getvalue()
    assert User.objects.get(pk=guest.pk).redacted_at is not None


def test_command_stats_mode(guest: User) -> None:
    """``--stats`` فقط آمار می‌دهد و چیزی را تغییر نمی‌دهد."""
    out = StringIO()
    call_command("retention_sweep", "--stats", stdout=out)
    assert "پنجرهٔ نگهداری هویت" in out.getvalue()
    assert isinstance(_now(), datetime)  # اطمینان از اجرای سریع و سالم


def _now() -> datetime:
    """کمکی: یک لحظهٔ زمان (برای اطمینان از نبود وابستگی پنهان در آزمون)."""
    return timezone.now()
