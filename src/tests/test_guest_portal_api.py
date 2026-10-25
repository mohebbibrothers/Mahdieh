"""پرتال زائر (P-07) — هماهنگی ورود پس از ثبت و سابقهٔ اقامت با وضعیت نظرسنجی.

قرارداد سیم دو قابلیت تازه را قفل می‌کند:
    ۱) ‎PATCH /reservations/{code}‎: ساعت رسیدن، پلاک و یادداشت پذیرش — فقط صاحب رزرو، فقط پیش
       از پذیرش حضوری، بی‌اثر بر سانس؛ رخداد «هماهنگی ورود» در تاریخچه و ممیزی بدون مقدارها.
    ۲) نمای رزرو: ‎feedback_rating‎ (امتیاز ثبت‌شده)، ‎is_late_cancel‎ و ‎details_editable‎ —
       سابقهٔ اقامت بدون N+1 (نظرسنجی هم‌زمان بارگذاری می‌شود).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import time_machine

from apps.audit.choices import AuditAction
from apps.audit.models import AuditRecord
from apps.reservation import feedback as feedback_services, services as reservation_services
from apps.reservation.choices import ReservationEventKind, ReservationStatus
from apps.reservation.models import Reservation
from apps.reservation.policy import set_policy
from tests.factories import days_ahead, lattice_period

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def upcoming(guest, units) -> Reservation:
    """رزرو تأییدشدهٔ چهار روز آینده برای زائر."""
    start, end = lattice_period(start_day=days_ahead(4), index=0, cells=2)
    return reservation_services.create_reservation(
        guest=guest, unit=units["A1"], start=start, end=end, guests_count=2
    )


def _url(reservation: Reservation) -> str:
    return f"/api/v1/reservations/{reservation.code}"


def _stay(guest, unit, operator, *, days_ago: int, index: int = 0) -> Reservation:
    """اقامت واقعی پایان‌یافته (رزرو ⇐ پذیرش ⇐ تخلیه) با سفر در زمان."""
    start, end = lattice_period(start_day=days_ahead(-days_ago), index=index, cells=1)
    with time_machine.travel(start - timedelta(days=4), tick=False):
        reservation = reservation_services.create_reservation(
            guest=guest, unit=unit, start=start, end=end, guests_count=2
        )
    with time_machine.travel(start + timedelta(hours=1), tick=False):
        reservation = reservation_services.check_in(reservation=reservation, by=operator)
    with time_machine.travel(end + timedelta(hours=1), tick=False):
        return reservation_services.check_out(reservation=reservation, by=operator)


# ─── هماهنگی ورود ───────────────────────────────────────────────────────────


def test_guest_updates_arrival_plate_and_note(api, guest, upcoming) -> None:
    """هر سه فیلد ذخیره و در پاسخ بازگردانده می‌شوند؛ تاریخچه رخداد «هماهنگی ورود» دارد."""
    api.force_authenticate(user=guest)
    assert api.get(_url(upcoming)).json()["details_editable"] is True
    response = api.patch(
        _url(upcoming),
        {
            "expected_arrival": "21:30",
            "vehicle_plate": "  ۱۲ ب  ۴۵۶ - ۲۳ ",
            "guest_note": "کالسکهٔ کودک",
        },
        format="json",
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["expected_arrival"] == "21:30:00"
    assert body["vehicle_plate"] == "۱۲ ب ۴۵۶ - ۲۳"
    assert body["guest_note"] == "کالسکهٔ کودک"
    assert body["unit_code"] == "A1"  # نمای کامل، نه فقط فیلدهای تغییرکرده
    assert body["details_editable"] is True

    timeline = api.get(f"{_url(upcoming)}/timeline").json()["results"]
    details = [event for event in timeline if event["kind"] == ReservationEventKind.DETAILS]
    assert len(details) == 1
    assert details[0]["kind_label"] == "هماهنگی ورود"


def test_partial_update_touches_only_sent_fields_and_can_clear(api, guest, upcoming) -> None:
    """فقط فیلد فرستاده‌شده عوض می‌شود؛ ساعت تهی = «نامشخص»."""
    api.force_authenticate(user=guest)
    api.patch(_url(upcoming), {"expected_arrival": "09:15", "guest_note": "ویلچر"}, format="json")
    body = api.patch(_url(upcoming), {"expected_arrival": None}, format="json").json()
    assert body["expected_arrival"] is None
    assert body["guest_note"] == "ویلچر"


def test_unchanged_update_records_no_event(api, guest, upcoming) -> None:
    """دوبار زدن «ثبت» با همان مقدار، تاریخچه و ممیزی را شلوغ نمی‌کند."""
    api.force_authenticate(user=guest)
    payload = {"vehicle_plate": "۱۲ ب ۴۵۶ - ۲۳"}
    assert api.patch(_url(upcoming), payload, format="json").status_code == 200
    assert api.patch(_url(upcoming), payload, format="json").status_code == 200
    assert upcoming.events.filter(kind=ReservationEventKind.DETAILS).count() == 1
    assert AuditRecord.objects.filter(action=AuditAction.RESERVATION_DETAILS_UPDATED).count() == 1


def test_audit_records_field_names_not_personal_values(api, guest, upcoming) -> None:
    """ممیزی زنجیره‌ای پاک‌شدنی نیست ⇒ فقط نام فیلدها، هرگز پلاک یا یادداشت."""
    api.force_authenticate(user=guest)
    api.patch(
        _url(upcoming), {"vehicle_plate": "۷۷ ج ۸۸۸ - ۹۹", "guest_note": "سرّی"}, format="json"
    )
    record = AuditRecord.objects.get(action=AuditAction.RESERVATION_DETAILS_UPDATED)
    assert sorted(record.metadata["fields"]) == ["guest_note", "vehicle_plate"]
    assert "۸۸۸" not in str(record.metadata)
    assert "سرّی" not in str(record.metadata)
    event = upcoming.events.get(kind=ReservationEventKind.DETAILS)
    assert "۸۸۸" not in str(event.metadata)


def test_empty_body_is_rejected(api, guest, upcoming) -> None:
    """بدنهٔ خالی ⇒ ۴۲۲ گویا (نه ۲۰۰ بی‌اثر)."""
    api.force_authenticate(user=guest)
    response = api.patch(_url(upcoming), {}, format="json")
    assert response.status_code == 422
    assert response.headers["Content-Type"].startswith("application/problem+json")


def test_overlong_values_are_rejected(api, guest, upcoming) -> None:
    """سقف ستون‌ها در لبهٔ API اعمال می‌شود (پلاک ۲۰، یادداشت ۳۰۰)."""
    api.force_authenticate(user=guest)
    assert api.patch(_url(upcoming), {"vehicle_plate": "۱" * 21}, format="json").status_code == 422
    assert api.patch(_url(upcoming), {"guest_note": "ن" * 301}, format="json").status_code == 422
    assert (
        api.patch(_url(upcoming), {"expected_arrival": "25:99"}, format="json").status_code == 422
    )


def test_other_guests_reservation_is_invisible(api, other_guest, upcoming) -> None:
    """رزرو دیگران همان ۴۰۴ «یافت نشد» را می‌گیرد (ضد حدس کد)."""
    api.force_authenticate(user=other_guest)
    response = api.patch(_url(upcoming), {"guest_note": "نفوذ"}, format="json")
    assert response.status_code == 404
    upcoming.refresh_from_db()
    assert upcoming.guest_note == ""


def test_anonymous_cannot_update(api, upcoming) -> None:
    """بدون ورود ⇒ ۴۰۱."""
    assert api.patch(_url(upcoming), {"guest_note": "x"}, format="json").status_code == 401


def test_cancelled_reservation_is_locked(api, guest, upcoming) -> None:
    """پس از لغو، هماهنگی ورود بی‌معناست ⇒ ۴۰۹ ‎details_locked‎ و ‎details_editable=false‎."""
    api.force_authenticate(user=guest)
    assert api.post(f"{_url(upcoming)}/cancel", {}, format="json").status_code == 200
    assert api.get(_url(upcoming)).json()["details_editable"] is False
    response = api.patch(_url(upcoming), {"guest_note": "دیر"}, format="json")
    assert response.status_code == 409
    assert response.json()["code"] == "details_locked"


def test_checked_in_reservation_is_locked(api, guest, upcoming, operator) -> None:
    """پس از پذیرش حضوری، داده سند پیشخوان است و زائر آن را تغییر نمی‌دهد."""
    with time_machine.travel(upcoming.starts_at + timedelta(hours=1), tick=False):
        reservation_services.check_in(reservation=upcoming, by=operator)
        api.force_authenticate(user=guest)
        response = api.patch(_url(upcoming), {"vehicle_plate": "۱۱ د ۱۱۱ - ۱۱"}, format="json")
    assert response.status_code == 409
    assert response.json()["code"] == "details_locked"


def test_guest_timeline_still_hides_staff_notes(api, guest, upcoming, operator) -> None:
    """رخداد تازه دیده می‌شود ولی یادداشت داخلی کارکنان همچنان پنهان می‌ماند."""
    reservation_services.update_staff_note(reservation=upcoming, by=operator, note="داخلی")
    api.force_authenticate(user=guest)
    api.patch(_url(upcoming), {"guest_note": "سلام"}, format="json")
    kinds = [event["kind"] for event in api.get(f"{_url(upcoming)}/timeline").json()["results"]]
    assert ReservationEventKind.DETAILS in kinds
    assert ReservationEventKind.NOTE not in kinds


# ─── سابقهٔ اقامت ───────────────────────────────────────────────────────────


def test_past_stays_carry_feedback_rating_and_link(api, guest, units, operator) -> None:
    """اقامت با نظر ثبت‌شده ⇒ امتیاز و بی‌پیوند؛ اقامت بی‌نظر ⇒ پیوند و امتیاز تهی."""
    rated = _stay(guest, units["A1"], operator, days_ago=6)
    open_ = _stay(guest, units["B1"], operator, days_ago=3)
    feedback_services.submit_feedback(token=feedback_services.issue_token(rated), rating=5)

    api.force_authenticate(user=guest)
    rows = {
        row["code"]: row
        for row in api.get("/api/v1/reservations", {"scope": "past"}).json()["results"]
    }
    assert rows[rated.code]["feedback_rating"] == 5
    assert rows[rated.code]["feedback_url"] is None
    assert rows[open_.code]["feedback_rating"] is None
    assert rows[open_.code]["feedback_url"].startswith("/feedback/")
    assert rows[open_.code]["details_editable"] is False


def test_past_list_has_no_n_plus_one(
    api, guest, units, operator, django_assert_max_num_queries
) -> None:
    """تعداد پرس‌وجوی سابقهٔ اقامت با تعداد اقامت‌ها رشد نمی‌کند."""
    for index, code in enumerate(("A1", "B1", "A2", "B2")):
        _stay(guest, units[code], operator, days_ago=3 + index)
    api.force_authenticate(user=guest)
    with django_assert_max_num_queries(12):
        response = api.get("/api/v1/reservations", {"scope": "past"})
    assert response.status_code == 200
    assert len(response.json()["results"]) == 4


def test_late_cancel_flag_is_exposed(api, guest, units) -> None:
    """لغو کمتر از ۲۴ ساعت پیش از ورود ⇒ ‎is_late_cancel=true‎ در سابقه (برای برچسب «دیرهنگام»)."""
    start, end = lattice_period(start_day=days_ahead(2), index=0, cells=1)
    reservation = reservation_services.create_reservation(
        guest=guest, unit=units["A2"], start=start, end=end, guests_count=1
    )
    api.force_authenticate(user=guest)
    with time_machine.travel(start - timedelta(hours=3), tick=False):
        assert api.post(f"{_url(reservation)}/cancel", {}, format="json").status_code == 200
        body = api.get(_url(reservation)).json()
    assert body["status"] == ReservationStatus.CANCELLED_BY_GUEST
    assert body["is_late_cancel"] is True
    assert body["can_cancel"] is False


# ─── ترتیب و صفحه‌بندی ──────────────────────────────────────────────────────


def test_upcoming_is_ordered_by_stay_start_not_creation(api, guest, units, operator) -> None:
    """سفر دورتر که زودتر ثبت شده، پس از سفر نزدیک‌تر می‌آید (کارت «اقامت فعال» = نخستین)."""
    far_start, far_end = lattice_period(start_day=days_ahead(9), index=0, cells=1)
    near_start, near_end = lattice_period(start_day=days_ahead(3), index=0, cells=1)
    far = reservation_services.create_reservation(
        guest=guest, unit=units["B1"], start=far_start, end=far_end, guests_count=1
    )
    # سقف پیش‌فرض «رزرو فعال» ۱ است؛ مدیر برای این سناریو آن را ۲ می‌کند.
    set_policy(
        key="reservation.max_active_per_user", value=2, actor=operator, reason="تست ترتیب سفرها"
    )
    near = reservation_services.create_reservation(
        guest=guest, unit=units["B2"], start=near_start, end=near_end, guests_count=1
    )
    api.force_authenticate(user=guest)
    body = api.get("/api/v1/reservations", {"scope": "upcoming"}).json()
    assert [row["code"] for row in body["results"]] == [near.code, far.code]
    assert body["meta"]["total"] == 2


def test_past_cursor_walks_newest_stay_first_without_gaps(api, guest, units, operator) -> None:
    """مکان‌نما با اندازهٔ صفحهٔ ۱ همهٔ سابقه را یک‌بار و به ترتیب تازه‌ترین سفر می‌پیماید."""
    # عمداً ترتیب ثبت با ترتیب سفر یکی نیست.
    older = _stay(guest, units["A1"], operator, days_ago=8)
    newest = _stay(guest, units["B1"], operator, days_ago=2)
    middle = _stay(guest, units["A2"], operator, days_ago=5)
    api.force_authenticate(user=guest)
    seen: list[str] = []
    url: str | None = "/api/v1/reservations?scope=past&page_size=1"
    while url:
        body = api.get(url).json()
        assert body["meta"]["total"] == 3
        seen.extend(row["code"] for row in body["results"])
        url = body["meta"]["next"]
    assert seen == [newest.code, middle.code, older.code]
