"""قرارداد API فرانت مستقل (فاز ۱۴) — هر نقطه‌ای که Next.js به آن تکیه می‌کند.

پوشش: پیکربندی و تقویم شرطی (ETag/304)، راهنما، توان رزرو، پیش‌نمایش لغو،
تاریخچه، ICS، گواهی و تأیید عمومی، نظرسنجی توکن‌محور، پیوند پیامکی صف انتظار،
پالایه‌های تابلوی پذیرش، دفتر زائران و نمایش کنترل‌شدهٔ شماره، اکسل با شمارهٔ
کامل، و قرارداد یکسان ۴۰۴ برای «مال شما نیست».
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.test import override_settings
from django.utils import timezone
import pytest
import time_machine

from apps.audit.models import AuditRecord
from apps.reservation import feedback as feedback_services, services as reservation_services
from apps.reservation.certificate import build_certificate
from apps.reservation.choices import ReservationStatus
from apps.reservation.selectors import phone_fragment
from apps.waitlist import selectors as waitlist_selectors, services as waitlist_services
from apps.waitlist.tokens import make_token
from tests.factories import days_ahead, lattice_period, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _book(guest: Any, unit: Any, *, day: int = 5, index: int = 0, cells: int = 2) -> Any:
    """رزرو واقعی از مسیر سرویس دامنه."""
    start, end = lattice_period(start_day=days_ahead(day), index=index, cells=cells)
    return reservation_services.create_reservation(
        guest=guest, unit=unit, start=start, end=end, guests_count=2
    )


def _stayed(guest: Any, unit: Any, operator: Any) -> Any:
    """اقامت پایان‌یافتهٔ واقعی (با سفر در زمان)."""
    start, end = lattice_period(start_day=days_ahead(-3), index=0, cells=2)
    with time_machine.travel(start - timedelta(days=4), tick=False):
        reservation = reservation_services.create_reservation(
            guest=guest, unit=unit, start=start, end=end, guests_count=2
        )
    with time_machine.travel(start + timedelta(hours=1), tick=False):
        reservation = reservation_services.check_in(reservation=reservation, by=operator)
    with time_machine.travel(end + timedelta(hours=1), tick=False):
        return reservation_services.check_out(reservation=reservation, by=operator)


# ── پیکربندی، راهنما، تقویم ─────────────────────────────────────────────────


def test_config_is_public_complete_and_conditional(api, units) -> None:
    """``/config``: همه‌چیز در یک پاسخ + ETag ⇒ ۳۰۴ در درخواست دوم."""
    first = api.get("/api/v1/config")
    assert first.status_code == 200
    body = first.json()
    assert body["lattice"]["cell_hours"] == 12
    assert body["auth"]["otp_length"] == 5
    assert {unit["code"] for unit in body["units"]} >= {"A1", "B1", "B3"}
    assert any(country["code"] == "AF" and country["priority"] for country in body["countries"])
    assert len(body["provinces"]) == 31
    statuses = {item["value"] for item in body["labels"]["reservation_status"]}
    assert "checked_in" in statuses
    assert "confirmed" in body["status_groups"]["cancellable"]
    policy_keys = {item["key"] for item in body["policies"]}
    assert "reservation.checkin_early_hours" in policy_keys
    assert first["ETag"].startswith('W/"')

    again = api.get("/api/v1/config", HTTP_IF_NONE_MATCH=first["ETag"])
    assert again.status_code == 304
    assert again["ETag"] == first["ETag"]


def test_calendar_etag_changes_when_a_booking_lands(api, guest, units) -> None:
    """ETag تقویم با هر تغییر موجودی عوض می‌شود؛ بدون تغییر ⇒ ۳۰۴."""
    url = f"/api/v1/calendar?from={days_ahead(1).isoformat()}&days=7"
    first = api.get(url)
    assert first.status_code == 200
    assert api.get(url, HTTP_IF_NONE_MATCH=first["ETag"]).status_code == 304

    _book(guest, units["A1"], day=3)
    changed = api.get(url, HTTP_IF_NONE_MATCH=first["ETag"])
    assert changed.status_code == 200
    assert changed["ETag"] != first["ETag"]


def test_help_api_renders_live_policy_numbers(api) -> None:
    """راهنما: دسته‌ها، جست‌وجو و اقدام‌های معنایی (نه نشانی SSR)."""
    response = api.get("/api/v1/help?q=لغو")
    assert response.status_code == 200
    body = response.json()
    assert body["categories"]
    assert body["matches"]
    assert all("{" not in item["answer"] for c in body["categories"] for item in c["items"])
    targets = {step["target"] for step in body["quick_steps"]}
    assert "calendar" in targets


# ── تجربهٔ زائر ─────────────────────────────────────────────────────────────


def test_standing_reports_quotas_and_blockers(api, guest, units) -> None:
    """توان رزرو: سهمیهٔ فعال پر ⇒ ``can_book=False`` با کد ``active_limit``."""
    api.force_authenticate(user=guest)
    fresh = api.get("/api/v1/me/standing").json()
    assert fresh["can_book"] is True
    assert fresh["active"]["used"] == 0

    limit = fresh["active"]["limit"]
    for offset in range(limit):
        _book(guest, units["B3"], day=3 + offset * 3)
    full = api.get("/api/v1/me/standing").json()
    assert full["can_book"] is False
    assert full["active"]["remaining"] == 0
    assert "active_limit" in {blocker["code"] for blocker in full["blockers"]}


def test_incomplete_profile_is_a_blocker(api) -> None:
    """پروفایل ناقص ⇒ مانع «profile_incomplete»."""
    newcomer = make_user(phone="09120004401", complete_profile=False)
    api.force_authenticate(user=newcomer)
    body = api.get("/api/v1/me/standing").json()
    assert body["profile_complete"] is False
    assert body["blockers"][0]["code"] == "profile_incomplete"


def test_cancel_preview_free_then_late(api, guest, units) -> None:
    """پیش‌نمایش لغو: اکنون رایگان؛ یک ساعت پیش از ورود ⇒ دیرهنگام با خنک‌شدن."""
    reservation = _book(guest, units["A1"])
    api.force_authenticate(user=guest)
    url = f"/api/v1/reservations/{reservation.code}/cancel-preview"

    free = api.get(url).json()
    assert free["cancellable"] is True
    assert free["late"] is False
    assert free["cooldown_until"] is None

    detail = api.get(f"/api/v1/reservations/{reservation.code}").json()
    assert detail["can_cancel"] is True
    assert detail["free_cancel_until"] == free["free_until"]
    assert detail["start"].endswith("+03:30")

    with time_machine.travel(reservation.starts_at - timedelta(hours=1), tick=False):
        late = api.get(url).json()
    assert late["late"] is True
    assert late["cooldown_until"] is not None
    assert late["cooldown_hours"] > 0


def test_other_guests_reservations_are_404_everywhere(api, guest, other_guest, units) -> None:
    """رزرو دیگران در همهٔ مسیرهای زائر ۴۰۴ است (نه ۴۰۳)."""
    reservation = _book(guest, units["A1"])
    api.force_authenticate(user=other_guest)
    for suffix in ("", "/cancel-preview", "/timeline", "/calendar.ics", "/certificate"):
        assert api.get(f"/api/v1/reservations/{reservation.code}{suffix}").status_code == 404
    assert api.post(f"/api/v1/reservations/{reservation.code}/cancel").status_code == 404


def test_timeline_hides_internal_notes(api, guest, units, operator) -> None:
    """تاریخچهٔ زائر یادداشت داخلی و نام کارمند را نشان نمی‌دهد؛ تاریخچهٔ کارکنان نشان می‌دهد."""
    reservation = _book(guest, units["B1"])
    reservation_services.update_staff_note(
        reservation=reservation, by=operator, note="مهمان ویلچر دارد"
    )
    api.force_authenticate(user=guest)
    events = api.get(f"/api/v1/reservations/{reservation.code}/timeline").json()["results"]
    kinds = [event["kind"] for event in events]
    assert "created" in kinds
    assert "note" not in kinds
    assert all("actor_label" not in event for event in events)

    api.force_authenticate(user=operator)
    staff_events = api.get(f"/api/v1/staff/reservations/{reservation.code}/events").json()
    note = next(event for event in staff_events["results"] if event["kind"] == "note")
    assert note["actor_label"]


def test_ics_downloads_are_audited(api, guest, units) -> None:
    """ICS تک‌رزرو و «همهٔ اقامت‌های من» — نوع محتوا، پیوست و حسابرسی."""
    reservation = _book(guest, units["A1"])
    api.force_authenticate(user=guest)
    single = api.get(f"/api/v1/reservations/{reservation.code}/calendar.ics")
    assert single.status_code == 200
    assert single["Content-Type"].startswith("text/calendar")
    assert b"BEGIN:VEVENT" in single.content
    mine = api.get("/api/v1/me/calendar.ics")
    assert mine.status_code == 200
    assert mine.content.count(b"BEGIN:VEVENT") == 1
    assert AuditRecord.objects.filter(action="reservation.calendar_exported").count() == 2


def test_certificate_and_public_verification(api, guest, units, operator) -> None:
    """گواهی برای رزرو معتبر؛ تأیید عمومی معتبر/دست‌کاری‌شده؛ بازرسی آفلاین."""
    reservation = _book(guest, units["A2"])
    api.force_authenticate(user=guest)
    certificate = api.get(f"/api/v1/reservations/{reservation.code}/certificate")
    assert certificate.status_code == 200
    body = certificate.json()
    assert body["qr_svg"].startswith("<svg")
    assert len(body["short_code"]) == 8
    token = build_certificate(reservation).token

    api.force_authenticate(user=None)
    valid = api.get(f"/api/v1/certificates/verify/{token}").json()
    assert valid["valid"] is True
    assert valid["payload"]["code"] == reservation.code
    forged_token = token[:-1] + ("A" if token[-1] != "A" else "B")
    forged = api.get(f"/api/v1/certificates/verify/{forged_token}").json()
    assert forged["valid"] is False
    assert forged["payload"] == {}

    printed = build_certificate(reservation)
    offline = api.post(
        "/api/v1/certificates/verify-offline",
        {
            "code": printed.code,
            "holder": printed.holder,
            "unit": printed.unit_code,
            "floor": printed.floor_number,
            "guests": printed.guests_count,
            "starts": printed.starts_at_local,
            "ends": printed.ends_at_local,
            "short_code": printed.short_code,
        },
        format="json",
    )
    assert offline.json() == {"valid": True}

    reservation_services.cancel_reservation(
        reservation=reservation, by=operator, reason="تعمیر لوله", is_staff=True
    )
    api.force_authenticate(user=guest)
    refused = api.get(f"/api/v1/reservations/{reservation.code}/certificate")
    assert refused.status_code == 409
    assert refused.json()["code"] == "certificate_unavailable"


def test_feedback_by_token_open_submit_and_once(api, guest, units, operator) -> None:
    """نظرسنجی توکن‌محور: باز ⇒ ثبت ۲۰۱ ⇒ دوباره ۴۰۹؛ توکن خراب ⇒ ۴۱۰."""
    stayed = _stayed(guest, units["A1"], operator)
    token = feedback_services.issue_token(stayed)

    state = api.get(f"/api/v1/feedback/{token}").json()
    assert state["state"] == "open"
    assert state["reservation"]["code"] == stayed.code
    assert [item["field"] for item in state["subscores"]] == list(feedback_services.SUBSCORE_FIELDS)

    invalid = api.post(f"/api/v1/feedback/{token}", {"rating": 9}, format="json")
    assert invalid.status_code == 422
    assert "rating" in invalid.json()["errors"]

    created = api.post(
        f"/api/v1/feedback/{token}",
        {"rating": 5, "comment": "عالی", "would_recommend": True, "scores": {"cleanliness": 5}},
        format="json",
    )
    assert created.status_code == 201, created.content
    assert created.json()["state"] == "submitted"
    again = api.post(f"/api/v1/feedback/{token}", {"rating": 4}, format="json")
    assert again.status_code == 409

    gone = api.get(f"/api/v1/feedback/{token[:-4]}zzzz")
    assert gone.status_code == 410
    assert gone.json()["code"] == "feedback_link_invalid"


def test_waitlist_offer_link_resolves_for_its_owner_only(api, guest, other_guest, units) -> None:
    """پیوند پیامکی پیشنهاد: صاحب ⇒ جزئیات؛ دیگری ⇒ ۴۰۴؛ کنش ناجور ⇒ ۴۱۰."""
    start, end = lattice_period(start_day=days_ahead(5), index=0, cells=2)
    taken = reservation_services.create_reservation(
        guest=other_guest, unit=units["B1"], start=start, end=end, guests_count=2
    )
    entry = waitlist_services.join_waitlist(
        guest=guest, unit=units["B1"], start=start, end=end, guests_count=2
    )
    reservation_services.cancel_reservation(reservation=taken, by=other_guest, reason="منصرف شدم")
    offer = waitlist_selectors.open_offer_for_entry(entry)
    assert offer is not None
    token = make_token(offer_id=offer.id, action="accept")

    api.force_authenticate(user=guest)
    ok = api.get(f"/api/v1/waitlist/offers/by-token/{token}?action=accept")
    assert ok.status_code == 200
    assert ok.json()["id"] == str(offer.id)
    assert ok.json()["is_open"] is True
    wrong_action = api.get(f"/api/v1/waitlist/offers/by-token/{token}?action=decline")
    assert wrong_action.status_code == 410

    api.force_authenticate(user=other_guest)
    assert api.get(f"/api/v1/waitlist/offers/by-token/{token}?action=accept").status_code == 404


# ── پروفایل زائر خارجی ──────────────────────────────────────────────────────


@override_settings(INTERNATIONAL_PHONES_ENABLED=True)
def test_foreign_pilgrim_completes_profile_with_passport(api) -> None:
    """زائر افغانستانی با گذرنامه — بدون استان/کد ملی؛ خطاها به‌ازای هر فیلد."""
    pilgrim = make_user(phone="09120004402", complete_profile=False)
    api.force_authenticate(user=pilgrim)

    broken = api.patch(
        "/api/v1/me/profile",
        {"first_name": "ا", "last_name": "", "nationality": "AF", "city": "هرات"},
        format="json",
    )
    assert broken.status_code == 422
    assert {"first_name", "passport_number"} <= set(broken.json()["errors"])

    done = api.patch(
        "/api/v1/me/profile",
        {
            "first_name": "محمد",
            "last_name": "رحیمی",
            "nationality": "AF",
            "city": "هرات",
            "passport_number": "P01234567",
        },
        format="json",
    )
    assert done.status_code == 200, done.content
    body = done.json()
    assert body["nationality"] == "AF"
    assert body["nationality_title"] == "افغانستان"
    assert body["id_document_type"] == "passport"
    assert body["is_profile_complete"] is True

    twin = make_user(phone="09120004403", complete_profile=False)
    api.force_authenticate(user=twin)
    duplicate = api.patch(
        "/api/v1/me/profile",
        {
            "first_name": "علی",
            "last_name": "رحیمی",
            "nationality": "AF",
            "city": "هرات",
            "passport_number": "p01234567",
        },
        format="json",
    )
    assert duplicate.status_code in {409, 422}
    assert "passport_number" in duplicate.json()["errors"]


# ── کارکنان ─────────────────────────────────────────────────────────────────


def test_staff_board_filters(api, guest, other_guest, units, operator) -> None:
    """تابلو: جست‌وجوی کد/شماره/نام و پالایهٔ وضعیت/واحد/منبع؛ مقدار ناشناخته ⇒ ۴۲۲."""
    mine = _book(guest, units["A1"], day=2)
    theirs = _book(other_guest, units["B3"], day=3)
    reservation_services.cancel_reservation(reservation=theirs, by=other_guest, reason="انصراف")
    api.force_authenticate(user=operator)
    base = f"/api/v1/staff/reservations?from={days_ahead(1).isoformat()}&days=7"

    def codes(query: str) -> set[str]:
        response = api.get(base + query)
        assert response.status_code == 200, response.content
        return {row["code"] for row in response.json()["results"]}

    assert codes("") == {mine.code, theirs.code}
    assert codes(f"&q={mine.code}") == {mine.code}
    assert codes("&q=" + guest.phone[-6:]) == {mine.code}
    assert codes("&status=confirmed") == {mine.code}
    assert codes("&status=cancelled_by_guest,confirmed") == {mine.code, theirs.code}
    assert codes("&unit=b3") == {theirs.code}
    assert codes("&source=phone") == set()
    rejected = api.get(base + "&status=bogus")
    assert rejected.status_code == 422
    assert "status" in rejected.json()["errors"]


def test_guest_directory_and_audited_phone_reveal(api, guest, units, operator) -> None:
    """دفتر زائران: شمارهٔ پوشانده؛ نمایش کامل فقط با هدف + حسابرسی."""
    _book(guest, units["A1"])
    api.force_authenticate(user=operator)
    listed = api.get("/api/v1/staff/guests?q=" + guest.phone[-4:]).json()["results"]
    row = next(item for item in listed if item["id"] == str(guest.pk))
    assert row["reservations_count"] == 1
    assert "***" in row["phone_masked"]
    assert all(
        item["id"] != str(operator.pk) for item in api.get("/api/v1/staff/guests").json()["results"]
    )

    card = api.get(f"/api/v1/staff/guests/{guest.pk}").json()
    assert card["standing"]["active"]["used"] == 1
    assert len(card["recent_reservations"]) == 1
    assert api.get(f"/api/v1/staff/guests/{operator.pk}").status_code == 404

    missing_note = api.post(
        f"/api/v1/staff/guests/{guest.pk}/reveal-phone", {"purpose": "other"}, format="json"
    )
    assert missing_note.status_code == 422
    revealed = api.post(
        f"/api/v1/staff/guests/{guest.pk}/reveal-phone", {"purpose": "call_guest"}, format="json"
    )
    assert revealed.status_code == 200
    assert revealed.json()["phone"] == guest.phone
    assert "no-store" in revealed["Cache-Control"]
    record = AuditRecord.objects.filter(action="data.sensitive_read", target_id=str(guest.pk)).get()
    assert record.metadata["purpose"] == "call_guest"

    api.force_authenticate(user=guest)
    assert api.get("/api/v1/staff/guests").status_code == 403


def test_excel_full_phones_are_manager_only(api, guest, units, operator, manager) -> None:
    """اکسل: پیش‌فرض پوشانده؛ ``phones=full`` برای اپراتور ۴۰۳ و برای مدیر مجاز + حسابرسی."""
    _book(guest, units["A1"], day=2)
    span = f"from={days_ahead(0).isoformat()}&to={days_ahead(6).isoformat()}"

    api.force_authenticate(user=operator)
    masked = api.get(f"/api/v1/staff/reports/reservations.xlsx?{span}")
    assert masked.status_code == 200
    assert masked["X-Report-Phones"] == "masked"
    denied = api.get(f"/api/v1/staff/reports/reservations.xlsx?{span}&phones=full")
    assert denied.status_code == 403
    assert denied.json()["code"] == "manager_only"

    api.force_authenticate(user=manager)
    full = api.get(f"/api/v1/staff/reports/reservations.xlsx?{span}&phones=full")
    assert full.status_code == 200
    assert full["X-Report-Phones"] == "full"
    exports = AuditRecord.objects.filter(action="export.requested").order_by("created_at")
    assert exports.last().metadata["phones"] == "full"

    bad = api.get(f"/api/v1/staff/reports/reservations.xlsx?{span}&phones=everything")
    assert bad.status_code == 422


def test_staff_transition_response_is_the_staff_view(api, guest, units, operator) -> None:
    """پاسخ انتقال‌ها و ثبت تلفنی، نمای کارکنان است (با شمارهٔ پوشانده و نسخه)."""
    reservation = _book(guest, units["B1"], day=1)
    api.force_authenticate(user=operator)
    with time_machine.travel(reservation.starts_at - timedelta(hours=1), tick=False):
        response = api.post(f"/api/v1/staff/reservations/{reservation.code}/check-in")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] == ReservationStatus.CHECKED_IN
    assert "guest_phone_masked" in body
    assert "version" in body
    assert body["source_label"]
    assert timezone.localtime(reservation.starts_at).isoformat() == body["start"]


@pytest.mark.parametrize(
    ("digits", "expected"),
    [
        ("09121234567", "9121234567"),
        ("989121234567", "9121234567"),
        ("00989121234567", "9121234567"),
        ("98912", "912"),
        ("000001", "000001"),
        ("4567", "4567"),
        ("0912", "912"),
    ],
)
def test_phone_fragment_strips_only_whole_prefixes(digits: str, expected: str) -> None:
    """تکهٔ «۰۰۰۰۰۱» نباید به «۱» بدل شود و با همهٔ شماره‌ها جور شود."""
    assert phone_fragment(digits) == expected


def test_check_in_too_early_is_409_with_opening_moment(api, guest, units, operator) -> None:
    """پذیرش بیش از ``checkin_early_hours`` پیش از شروع ⇒ ۴۰۹ با ``opens_at``."""
    reservation = _book(guest, units["A2"], day=4)
    api.force_authenticate(user=operator)
    with time_machine.travel(reservation.starts_at - timedelta(days=2), tick=False):
        response = api.post(f"/api/v1/staff/reservations/{reservation.code}/check-in")
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "too_early_to_check_in"
    assert body["opens_at"]
