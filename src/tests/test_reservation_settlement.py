"""چرخهٔ پایان اقامت — فاز زنده، سقف رزرو فعالِ زمان‌آگاه و بستن خودکار پرونده.

سناریوی واقعی (گزارش کارفرما، ۱۴۰۵/۰۸/۲۱): رزرو «۳ الی ۵ آبان» یک هفته پس از پایان هنوز
«تأییدشده» بود؛ پرتال «۱ رزرو فعال» می‌گفت، رزرو تازه بسته بود و دفتر دل‌نوشته باز نمی‌شد —
چون پیشخوان پذیرش و تخلیه را ثبت نکرده بود و هیچ زمان‌بندی پرونده را نمی‌بست.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
import pytest
import time_machine

from apps.audit.choices import AuditAction
from apps.audit.models import AuditRecord
from apps.core.outbox import OutboxEvent
from apps.reservation import (
    desk,
    feedback as feedback_services,
    selectors,
    services as reservation_services,
    settlement,
    standing,
)
from apps.reservation.choices import ReservationEventKind, ReservationStatus
from apps.reservation.models import Reservation
from apps.reservation.policy import set_policy
from tests.factories import days_ahead, lattice_period, make_staff, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _booked(guest, unit, *, days_ago: int, cells: int = 4) -> Reservation:
    """رزروی که چند روز پیش تمام شده و پیشخوان هیچ کنشی رویش ثبت نکرده است."""
    start, end = lattice_period(start_day=days_ahead(-days_ago), index=0, cells=cells)
    with time_machine.travel(start - timedelta(days=3), tick=False):
        return reservation_services.create_reservation(
            guest=guest, unit=unit, start=start, end=end, guests_count=4
        )


def _checked_in(guest, unit, operator, *, days_ago: int) -> Reservation:
    """رزروی که پذیرش شده ولی تخلیه‌اش هرگز ثبت نشده است."""
    reservation = _booked(guest, unit, days_ago=days_ago, cells=2)
    with time_machine.travel(reservation.starts_at + timedelta(hours=1), tick=False):
        return reservation_services.check_in(reservation=reservation, by=operator)


# ─── لایهٔ ۱: سنجهٔ زمان (فوری، مستقل از کرون) ─────────────────────────────


def test_ended_stay_no_longer_blocks_new_booking(guest, units) -> None:
    """همان گزارش کارفرما: اقامت تمام‌شدهٔ بسته‌نشده، سقف رزرو فعال را پر نمی‌کند."""
    reservation = _booked(guest, units["B1"], days_ago=8)
    assert reservation.status == ReservationStatus.CONFIRMED
    assert selectors.active_count(guest) == 0
    state = standing.guest_standing(guest)
    assert state.active.used == 0
    assert "active_limit" not in [item["code"] for item in state.blockers]


def test_stay_still_counts_as_active_until_its_last_moment(guest, units) -> None:
    """مرز دقیق: تا لحظهٔ پایان «فعال» است؛ از همان لحظه دیگر نه."""
    start, end = lattice_period(start_day=days_ahead(3), index=0, cells=2)
    reservation = reservation_services.create_reservation(
        guest=guest, unit=units["A1"], start=start, end=end, guests_count=2
    )
    with time_machine.travel(end - timedelta(seconds=1), tick=False):
        assert selectors.active_count(guest) == 1
        assert settlement.stay_phase(reservation) == settlement.ONGOING
    with time_machine.travel(end, tick=False):
        assert selectors.active_count(guest) == 0
        assert settlement.stay_phase(reservation) == settlement.SETTLING


def test_stay_phases_follow_the_clock(guest, units, operator) -> None:
    """پیش رو ⇐ در جریان ⇐ در حال بستن ⇐ بسته‌شده."""
    start, end = lattice_period(start_day=days_ahead(3), index=0, cells=2)
    reservation = reservation_services.create_reservation(
        guest=guest, unit=units["A1"], start=start, end=end, guests_count=2
    )
    assert settlement.stay_phase(reservation) == settlement.UPCOMING
    with time_machine.travel(start + timedelta(hours=1), tick=False):
        assert settlement.stay_phase(reservation) == settlement.ONGOING
    with time_machine.travel(end + timedelta(minutes=5), tick=False):
        assert settlement.stay_phase(reservation) == settlement.SETTLING
        assert settlement.settles_at(reservation) == end + timedelta(hours=6)
    reservation_services.cancel_reservation(reservation=reservation, by=guest)
    reservation.refresh_from_db()
    assert settlement.stay_phase(reservation) == settlement.CLOSED
    assert settlement.settles_at(reservation) is None


# ─── لایهٔ ۲: بستن خودکار پرونده ──────────────────────────────────────────


def test_unattended_stay_is_closed_as_completed_and_opens_feedback(guest, units) -> None:
    """رزرو بی‌پذیرش ⇒ «پایان‌یافته» بی ‎checked_in_at‎؛ دفتر دل‌نوشته باز می‌شود."""
    reservation = _booked(guest, units["B1"], days_ago=8)
    assert feedback_services.eligibility(reservation).eligible is False

    report = settlement.settle_ended_stays()

    assert (report.unattended, report.checked_out, report.no_show) == (1, 0, 0)
    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.COMPLETED
    assert reservation.checked_in_at is None
    assert reservation.checked_out_at is None
    assert feedback_services.eligibility(reservation).eligible is True
    event = reservation.events.get(kind=ReservationEventKind.SETTLED)
    assert event.actor is None
    assert event.metadata["mode"] == settlement.MODE_UNATTENDED
    assert event.metadata["automatic"] is True
    audit = AuditRecord.all_objects.get(action=AuditAction.RESERVATION_SETTLED)
    assert audit.actor_label == settlement.SYSTEM_ACTOR
    assert audit.metadata["code"] == reservation.code
    assert OutboxEvent.all_objects.filter(event_type="reservation.settled").exists()


def test_checked_in_stay_is_checked_out_at_its_end(guest, units, operator) -> None:
    """پذیرش‌شدهٔ بی‌تخلیه ⇒ «پایان‌یافته» با زمان تخلیه = پایان اقامت (نه لحظهٔ کرون)."""
    reservation = _checked_in(guest, units["A2"], operator, days_ago=5)

    report = settlement.settle_ended_stays()

    assert report.checked_out == 1
    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.COMPLETED
    assert reservation.checked_out_at == reservation.ends_at
    event = reservation.events.get(kind=ReservationEventKind.SETTLED)
    assert event.metadata["mode"] == settlement.MODE_CHECKED_OUT


def test_policy_can_close_unattended_stays_as_no_show(guest, units, manager) -> None:
    """سیاست «عدم‌حضور خودکار» روشن ⇒ «عدم‌حضور» با محرومیت سیاستی."""
    set_policy(
        key="reservation.auto_no_show_unattended",
        value=True,
        actor=manager,
        reason="سخت‌گیری فصل پیک",
    )
    reservation = _booked(guest, units["B1"], days_ago=8)

    report = settlement.settle_ended_stays()

    assert report.no_show == 1
    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.NO_SHOW
    assert reservation.no_show_at is not None
    assert reservation.events.filter(kind=ReservationEventKind.NO_SHOW).exists()
    assert AuditRecord.all_objects.filter(action=AuditAction.RESERVATION_NO_SHOW).exists()
    assert standing.guest_standing(guest).suspended_until is not None


def test_desk_keeps_its_window_before_automatic_closing(guest, units) -> None:
    """پیش از مهلت سیاستی، پرونده دست‌نخورده می‌ماند تا پیشخوان خودش تخلیه/عدم‌حضور بزند."""
    reservation = _booked(guest, units["B1"], days_ago=8)
    with time_machine.travel(reservation.ends_at + timedelta(hours=5, minutes=59), tick=False):
        assert settlement.settle_ended_stays().settled == 0
    with time_machine.travel(reservation.ends_at + timedelta(hours=6), tick=False):
        assert settlement.settle_ended_stays().settled == 1


def test_settle_window_follows_policy(guest, units, manager) -> None:
    """مدیر می‌تواند مهلت را بی انتشار نسخه تغییر دهد (صفر = بلافاصله پس از پایان)."""
    set_policy(
        key="reservation.auto_settle_after_hours",
        value=0,
        actor=manager,
        reason="بستن فوری پرونده‌ها",
    )
    reservation = _booked(guest, units["B1"], days_ago=8)
    with time_machine.travel(reservation.ends_at + timedelta(minutes=1), tick=False):
        assert settlement.settle_ended_stays().settled == 1


def test_settlement_is_idempotent_and_skips_closed_files(guest, other_guest, units) -> None:
    """اجرای دوباره چیزی را دوباره نمی‌بندد؛ لغوشده و آینده هرگز دست نمی‌خورند."""
    ended = _booked(guest, units["B1"], days_ago=8)
    cancelled = _booked(other_guest, units["A1"], days_ago=9)
    Reservation.objects.filter(pk=cancelled.pk).update(status=ReservationStatus.CANCELLED_BY_GUEST)
    start, end = lattice_period(start_day=days_ahead(4), index=0, cells=2)
    future = reservation_services.create_reservation(
        guest=make_user(phone="09120000777"), unit=units["B3"], start=start, end=end, guests_count=2
    )

    assert settlement.settle_ended_stays().settled == 1
    assert settlement.settle_ended_stays().settled == 0
    assert ended.events.filter(kind=ReservationEventKind.SETTLED).count() == 1
    cancelled.refresh_from_db()
    future.refresh_from_db()
    assert cancelled.status == ReservationStatus.CANCELLED_BY_GUEST
    assert future.status == ReservationStatus.CONFIRMED


def test_dry_run_reports_without_touching_anything(guest, other_guest, units, operator) -> None:
    """حالت آزمایشی: شمارش درست به‌تفکیک حالت، بی هیچ تغییر و رخداد."""
    unattended = _booked(guest, units["B1"], days_ago=8)
    staying = _checked_in(other_guest, units["A2"], operator, days_ago=5)

    report = settlement.settle_ended_stays(dry_run=True)

    assert (report.unattended, report.checked_out, report.examined) == (1, 1, 2)
    unattended.refresh_from_db()
    staying.refresh_from_db()
    assert unattended.status == ReservationStatus.CONFIRMED
    assert staying.status == ReservationStatus.CHECKED_IN
    assert not AuditRecord.all_objects.filter(action=AuditAction.RESERVATION_SETTLED).exists()


def test_quota_is_consumed_not_refunded_after_the_stay(guest, units) -> None:
    """نیم‌روزهای اقامت بسته‌شده همچنان از سهمیهٔ پنجره و سال کم می‌شوند (فقط لغو بازمی‌گرداند)."""
    reservation = _booked(guest, units["B1"], days_ago=8, cells=4)
    settlement.settle_ended_stays()
    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.COMPLETED
    state = standing.guest_standing(guest)
    assert state.window.used == 4
    assert state.year.used == 4


def test_management_command_prints_a_persian_summary(guest, units) -> None:
    """فرمان کرون: کارنامهٔ فارسی؛ ‎--dry-run‎ هیچ چیز را نمی‌بندد."""
    _booked(guest, units["B1"], days_ago=8)
    out = StringIO()
    call_command("settle_stays", "--dry-run", stdout=out)
    assert "آزمایشی" in out.getvalue()
    assert selectors.active_count(guest) == 0
    assert Reservation.objects.filter(status=ReservationStatus.CONFIRMED).count() == 1
    out = StringIO()
    call_command("settle_stays", "--limit", "10", stdout=out)
    assert "بستن خودکار: ۱ پرونده" in out.getvalue() or "بستن خودکار: 1 پرونده" in out.getvalue()
    assert Reservation.objects.filter(status=ReservationStatus.COMPLETED).count() == 1


# ─── API پرتال و پیشخوان ──────────────────────────────────────────────────


def test_portal_exposes_phase_and_settlement_time(api, guest, units) -> None:
    """پیش از بستن: ‎phase=settling‎ با زمان بستن؛ پس از آن: بسته و پیوند نظرسنجی."""
    reservation = _booked(guest, units["B1"], days_ago=8)
    api.force_authenticate(user=guest)
    with time_machine.travel(reservation.ends_at + timedelta(hours=1), tick=False):
        row = api.get(f"/api/v1/reservations/{reservation.code}").json()
        assert row["phase"] == "settling"
        assert row["phase_label"] == settlement.PHASE_LABELS[settlement.SETTLING]
        assert row["is_active"] is False
        assert row["settles_at"].startswith(
            (reservation.ends_at + timedelta(hours=6)).astimezone().date().isoformat()[:4]
        )
        assert row["feedback_url"] is None

    settlement.settle_ended_stays()
    row = api.get(f"/api/v1/reservations/{reservation.code}").json()
    assert row["phase"] == "closed"
    assert row["status"] == ReservationStatus.COMPLETED
    assert row["settles_at"] is None
    assert row["checked_in_at"] is None
    assert row["feedback_url"].startswith("/feedback/")
    past = api.get("/api/v1/reservations", {"scope": "past"}).json()["results"]
    assert [item["code"] for item in past] == [reservation.code]


def test_portal_marks_real_check_in_and_out_times(api, guest, units, operator) -> None:
    """پذیرش واقعی ⇒ ‎checked_in_at‎ پر؛ بستن خودکار ⇒ ‎checked_out_at‎ = پایان اقامت."""
    reservation = _checked_in(guest, units["A2"], operator, days_ago=5)
    settlement.settle_ended_stays()
    api.force_authenticate(user=guest)
    row = api.get(f"/api/v1/reservations/{reservation.code}").json()
    assert row["checked_in_at"] is not None
    assert row["checked_out_at"] is not None


def test_desk_log_explains_automatic_closing(guest, units, operator) -> None:
    """رخدادنگار پیشخوان: متن گویا با لحن هشدار، به‌تفکیک حالت."""
    unattended = _booked(guest, units["B1"], days_ago=8)
    staying = _checked_in(make_user(phone="09120000778"), units["A2"], operator, days_ago=5)
    settlement.settle_ended_stays()

    events = desk.recent_events(since=unattended.created_at - timedelta(days=30), limit=50)
    settled = [item for item in events if item.kind == "reservation.settled"]
    texts = {item.reservation_code: item.text for item in settled}
    assert "پذیرش حضوری" in texts[unattended.code]
    assert "کلید واحد را بازبینی کنید" in texts[staying.code]
    assert {item.tone for item in settled} == {"warning"}


def test_desk_does_not_call_an_unattended_file_checked_out(guest, units) -> None:
    """پروندهٔ بسته‌شدهٔ بی‌پذیرش در دفتر «تخلیه‌شده» خوانده نمی‌شود."""
    reservation = _booked(guest, units["B1"], days_ago=8)
    settlement.settle_ended_stays()
    reservation.refresh_from_db()
    flags = desk.desk_flags(reservation, now=reservation.ends_at, rules=desk.DeskRules.load())
    assert flags["lane"] == "checked_out"
    assert flags["lane_label"] == "بسته‌شده · بی ثبت پذیرش"


def test_staff_closing_first_wins_over_the_scheduler(guest, units) -> None:
    """اگر پیشخوان پیش از کرون عدم‌حضور بزند، زمان‌بند همان را نگه می‌دارد."""
    reservation = _booked(guest, units["B1"], days_ago=8)
    with time_machine.travel(reservation.ends_at + timedelta(hours=1), tick=False):
        reservation_services.mark_no_show(
            reservation=reservation, by=make_staff(phone="09991230001"), reason="زائر نیامد"
        )
    assert settlement.settle_ended_stays().settled == 0
    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.NO_SHOW
