"""قرارداد HTTP رزرو — تقویم، سبد، ثبت هم‌توان، لغو، تابلوی کارکنان.

این فایل «قرارداد سیم» را قفل می‌کند: هر تغییر ناخواسته در شکل پاسخ یا کد
وضعیت‌ها اینجا شکست می‌خورد — پیش از آنکه به دست کاربر برسد.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone
import pytest
import time_machine

from apps.core.idempotency import IDEMPOTENCY_HEADER
from apps.reservation.choices import ReservationStatus
from apps.reservation.models import Reservation
from tests.factories import days_ahead, future_period, lattice_period, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _payload(unit_code: str, start, end, guests: int = 2) -> dict[str, object]:
    """بدنهٔ استاندارد درخواست رزرو."""
    return {
        "unit_code": unit_code,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "guests_count": guests,
    }


# ─── تقویم (عمومی) ───────────────────────────────────────────────────────────


def test_calendar_is_public_and_carries_full_matrix(api, units) -> None:
    """تقویم بدون ورود قابل خواندن است و ماتریس کامل «واحد × نیم‌روز» را دارد."""
    response = api.get("/api/v1/calendar", {"days": 3})
    assert response.status_code == 200
    body = response.json()
    assert len(body["days"]) == 3
    assert body["min_cells"] == 1
    assert body["max_cells"] == 14
    assert body["lattice"] == {"cell_hours": 12, "grid_start_hour": 8}
    first_day = body["days"][0]
    assert first_day["total_cells"] == 10  # ۵ واحد × ۲ نیم‌روز
    assert len(first_day["units"]) == 5
    codes = [unit["code"] for unit in first_day["units"]]
    assert codes == ["A1", "B1", "A2", "B2", "B3"]
    cells = first_day["units"][0]["cells"]
    assert [cell["kind"] for cell in cells] == ["day", "night"]
    assert {cell["status"] for cell in cells} <= {"free", "occupied", "buffer", "blocked", "past"}
    assert cells[0]["label"]


def test_calendar_marks_booked_cells_as_occupied(api, guest, units) -> None:
    """سانس رزروشده در تقویم «اشغال» دیده می‌شود (شفافیت برای کاربر)."""
    start, end = future_period(days=2, cells=2)
    api.force_authenticate(user=guest)
    assert (
        api.post("/api/v1/reservations", _payload("A1", start, end), format="json").status_code
        == 201
    )
    api.force_authenticate(user=None)

    day = timezone.localtime(start).date()
    body = api.get("/api/v1/calendar", {"from": day.isoformat(), "days": 3}).json()
    day_row = next(row for row in body["days"] if row["day"] == day.isoformat())
    a1 = next(unit for unit in day_row["units"] if unit["code"] == "A1")
    assert {cell["status"] for cell in a1["cells"]} == {"occupied"}
    assert day_row["free_cells"] == 8


def test_calendar_rejects_invalid_window(api, units) -> None:
    """پنجرهٔ نامعتبر ⇒ ۴۲۲ با قالب مسئله (RFC 9457)."""
    assert api.get("/api/v1/calendar", {"from": "1405/07/01"}).status_code == 422
    bad_days = api.get("/api/v1/calendar", {"days": 400})
    assert bad_days.status_code == 422
    assert bad_days.headers["Content-Type"].startswith("application/problem+json")


# ─── سبد (quote) ─────────────────────────────────────────────────────────────


def test_quote_requires_authentication(api, units) -> None:
    """سبد رزرو نیازمند ورود است (۴۰۱ استاندارد پروژه)."""
    start, end = future_period(days=4, cells=2)
    response = api.post("/api/v1/reservations/quote", _payload("A1", start, end), format="json")
    assert response.status_code == 401


def test_quote_returns_full_picture(api, guest, units) -> None:
    """سبد: مجازبودن، تعداد نیم‌روز، برچسب مدت، ظرفیت و نبود تخلف."""
    start, end = future_period(days=4, cells=2)
    api.force_authenticate(user=guest)
    body = api.post("/api/v1/reservations/quote", _payload("A1", start, end), format="json").json()
    assert body["allowed"] is True
    assert body["is_available"] is True
    assert body["cells"] == 2
    assert body["unit"]["code"] == "A1"
    assert body["duration_label"]
    assert body["violations"] == []


def test_quote_lists_violations_and_alternatives(api, guest, other_guest, units) -> None:
    """سبد روی بازهٔ اشغال‌شده: تخلف «تصادم» + بازه‌های جایگزین."""
    start, end = future_period(days=6, cells=2)
    api.force_authenticate(user=other_guest)
    api.post("/api/v1/reservations", _payload("A1", start, end), format="json")

    api.force_authenticate(user=guest)
    body = api.post("/api/v1/reservations/quote", _payload("A1", start, end), format="json").json()
    assert body["allowed"] is False
    assert body["is_available"] is False
    assert "not_available" in [item["code"] for item in body["violations"]]
    assert body["alternatives"], "قرارداد: تصادم همیشه با پیشنهاد جایگزین همراه است"


# ─── ثبت رزرو ────────────────────────────────────────────────────────────────


def test_create_reservation_returns_201_and_hides_guest_identity(api, guest, units) -> None:
    """ثبت رزرو: ۲۰۱، کد خوانا، و نبستن هویت زائر در پاسخ عمومی."""
    start, end = future_period(days=3, cells=2)
    api.force_authenticate(user=guest)
    response = api.post("/api/v1/reservations", _payload("A1", start, end), format="json")
    assert response.status_code == 201
    body = response.json()
    assert body["code"].startswith("M-")
    assert body["status"] == ReservationStatus.CONFIRMED
    assert body["unit_code"] == "A1"
    assert body["floor_number"] == 1
    assert body["cells_count"] == 2
    assert body["is_active"] is True
    assert body["duration_label"]
    for forbidden in ("guest", "guest_phone", "guest_name", "national_id"):
        assert forbidden not in body


def test_duplicate_idempotency_key_replays_same_reservation(api, guest, units) -> None:
    """دو ضربهٔ پشت‌سرهم کاربر ⇒ یک رزرو و همان پاسخ (بدون رزرو دوم)."""
    start, end = future_period(days=8, cells=2)
    api.force_authenticate(user=guest)
    headers = {IDEMPOTENCY_HEADER: "web-8f3a1c"}
    payload = _payload("B1", start, end)

    first = api.post("/api/v1/reservations", payload, format="json", headers=headers)
    second = api.post("/api/v1/reservations", payload, format="json", headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()["code"] == second.json()["code"]
    assert Reservation.objects.filter(guest=guest).count() == 1


def test_same_key_with_different_body_is_rejected(api, guest, units) -> None:
    """همان کلید با بدنهٔ متفاوت ⇒ ۴۰۹ (سوءاستفاده یا باگ کلاینت)."""
    start, end = future_period(days=9, cells=2)
    api.force_authenticate(user=guest)
    headers = {IDEMPOTENCY_HEADER: "web-dup-1"}
    assert (
        api.post(
            "/api/v1/reservations", _payload("A1", start, end), format="json", headers=headers
        ).status_code
        == 201
    )
    response = api.post(
        "/api/v1/reservations",
        _payload("A2", start, end),
        format="json",
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_key_reused"


def test_conflicting_booking_returns_409_with_alternatives(api, guest, other_guest, units) -> None:
    """بازهٔ اشغال‌شده ⇒ ۴۰۹ + بازه‌های جایگزین (تجربهٔ کاربری، نه خطای خام)."""
    start, end = future_period(days=10, cells=2)
    api.force_authenticate(user=other_guest)
    api.post("/api/v1/reservations", _payload("A1", start, end), format="json")

    api.force_authenticate(user=guest)
    response = api.post("/api/v1/reservations", _payload("A1", start, end), format="json")
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "reservation_conflict"
    assert body["alternatives"]
    assert body["alternatives"][0]["is_same_unit"] is True


def test_create_requires_complete_profile_and_valid_payload(api, units) -> None:
    """نگهبان‌ها: پروفایل ناقص ⇒ ۴۰۳، بدنهٔ بی‌اعتبار ⇒ ۴۲۲."""
    incomplete = make_user(phone="09120000061", complete_profile=False)
    start, end = future_period(days=4, cells=2)
    api.force_authenticate(user=incomplete)
    blocked = api.post("/api/v1/reservations", _payload("A1", start, end), format="json")
    assert blocked.status_code == 403

    api.force_authenticate(user=make_user(phone="09120000062"))
    # قرارداد: خطای «شکل ورودی» = ۴۰۰ (سریالایزر)، تخلف «قاعدهٔ دامنه» = ۴۲۲.
    invalid = api.post(
        "/api/v1/reservations", {"unit_code": "A1", "guests_count": 0}, format="json"
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "validation_error"

    policy_breach = api.post(
        "/api/v1/reservations", _payload("A1", start, end, guests=20), format="json"
    )
    assert policy_breach.status_code == 422
    assert policy_breach.json()["code"]
    assert policy_breach.json()["violations"]


# ─── فهرست، جزئیات و لغو ─────────────────────────────────────────────────────


def test_list_scopes_and_ownership(api, guest, other_guest, units) -> None:
    """فهرست شخصی، دامنهٔ گذشته، و نبود دسترسی به رزرو دیگران (۴۰۳)."""
    start, end = future_period(days=2, cells=2)
    api.force_authenticate(user=guest)
    created = api.post("/api/v1/reservations", _payload("A1", start, end), format="json").json()

    listed = api.get("/api/v1/reservations").json()
    assert [item["code"] for item in listed["results"]] == [created["code"]]
    assert listed["meta"]["total"] == 1  # فهرست کاربرمحور: شمارش کل می‌آید
    assert api.get("/api/v1/reservations", {"scope": "past"}).json()["results"] == []

    detail = api.get(f"/api/v1/reservations/{created['code']}")
    assert detail.status_code == 200
    assert datetime.fromisoformat(detail.json()["start"]) == start
    assert datetime.fromisoformat(detail.json()["end"]) == end

    api.force_authenticate(user=other_guest)
    assert api.get(f"/api/v1/reservations/{created['code']}").status_code == 403
    assert api.get("/api/v1/reservations/UNKNOWN-CODE").status_code == 404


def test_cancel_frees_slot_and_blocks_double_cancel(api, guest, other_guest, units) -> None:
    """لغو از مسیر API: آزادسازی فوری سانس + رد لغو دوباره (۴۰۹)."""
    start, end = future_period(days=5, cells=2)
    api.force_authenticate(user=guest)
    code = api.post("/api/v1/reservations", _payload("A1", start, end), format="json").json()[
        "code"
    ]

    response = api.post(
        f"/api/v1/reservations/{code}/cancel", {"reason": "برنامه عوض شد"}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["status"] == ReservationStatus.CANCELLED_BY_GUEST

    again = api.post(f"/api/v1/reservations/{code}/cancel", {}, format="json")
    assert again.status_code == 409

    api.force_authenticate(user=other_guest)
    fresh = api.post("/api/v1/reservations", _payload("A1", start, end), format="json")
    assert fresh.status_code == 201, "سانس آزادشده باید بی‌درنگ قابل رزرو باشد"


# ─── مسیر کارکنان ────────────────────────────────────────────────────────────


def test_staff_endpoints_are_closed_to_guests(api, guest, units) -> None:
    """کارکنان فقط: زائر ۴۰۳ می‌گیرد، ناشناس ۴۰۱."""
    api.force_authenticate(user=None)
    assert api.get("/api/v1/staff/reservations").status_code == 401
    api.force_authenticate(user=guest)
    assert api.get("/api/v1/staff/reservations").status_code == 403
    assert (
        api.post(
            "/api/v1/staff/reservations/create", {"phone": "09120000063"}, format="json"
        ).status_code
        == 403
    )


def test_staff_board_shows_masked_guest_identity(api, guest, operator, units) -> None:
    """تابلوی پذیرش: هویت زائر برای کارمند (پوشانده) دیده می‌شود."""
    start, end = future_period(days=1, cells=2)
    api.force_authenticate(user=guest)
    api.post("/api/v1/reservations", _payload("A1", start, end), format="json")

    api.force_authenticate(user=operator)
    board = api.get("/api/v1/staff/reservations", {"days": 7}).json()
    assert len(board["results"]) == 1
    assert "total" not in board["meta"]  # میز کارکنان: شمارش کل گران است، نمی‌فرستیم
    row = board["results"][0]
    assert row["guest_name"]
    assert "***" in row["guest_phone_masked"]
    assert "national_id" not in row


def test_staff_phone_booking_and_full_transition_cycle(api, operator, units) -> None:
    """چرخهٔ کامل پذیرش: ثبت تلفنی ← جابه‌جایی ← پذیرش ← تخلیه."""
    start, end = future_period(days=1, cells=2)
    api.force_authenticate(user=operator)
    created = api.post(
        "/api/v1/staff/reservations/create",
        {
            "phone": "09120000064",
            "unit_code": "B1",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "guests_count": 3,
            "first_name": "زهرا",
            "last_name": "موسوی",
            "province": "khorasan-razavi",
            "city": "مشهد",
            "staff_note": "ورود با خودروی شخصی",
        },
        format="json",
    )
    assert created.status_code == 201
    code = created.json()["code"]

    moved = api.post(
        f"/api/v1/staff/reservations/{code}/move",
        {
            "unit_code": "A2",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "reason": "درخواست زائر برای طبقهٔ دوم",
        },
        format="json",
    )
    assert moved.status_code == 200
    assert moved.json()["unit_code"] == "A2"
    # زنجیرهٔ جانشینی (S4): رزرو پیشین بایگانی می‌شود و کد تازه جای آن را می‌گیرد.
    new_code = moved.json()["code"]
    assert new_code != code
    old = api.get(f"/api/v1/staff/reservations/{code}", {"days": 7})
    assert old.json()["status"] == "superseded"
    assert old.json()["superseded_by_code"] == new_code
    code = new_code

    # پذیرش تنها در بازهٔ اقامت معنا دارد ⇒ ساعت را به داخل بازه می‌بریم.
    with time_machine.travel(start + timedelta(hours=1), tick=False):
        checked_in = api.post(f"/api/v1/staff/reservations/{code}/check-in", {}, format="json")
        assert checked_in.status_code == 200
        assert checked_in.json()["status"] == ReservationStatus.CHECKED_IN

        checked_out = api.post(f"/api/v1/staff/reservations/{code}/check-out", {}, format="json")
        assert checked_out.status_code == 200
        assert checked_out.json()["status"] == ReservationStatus.COMPLETED


def test_staff_note_and_unknown_action(api, guest, operator, units) -> None:
    """یادداشت داخلی ثبت می‌شود و عملیات ناشناخته ۴۰۴ می‌دهد."""
    start, end = future_period(days=2, cells=2)
    api.force_authenticate(user=guest)
    code = api.post("/api/v1/reservations", _payload("A1", start, end), format="json").json()[
        "code"
    ]

    api.force_authenticate(user=operator)
    noted = api.post(
        f"/api/v1/staff/reservations/{code}/note",
        {"staff_note": "زائر با اتوبوس می‌رسد"},
        format="json",
    )
    assert noted.status_code == 200
    assert noted.json()["staff_note"] == "زائر با اتوبوس می‌رسد"

    unknown = api.post(f"/api/v1/staff/reservations/{code}/teleport", {}, format="json")
    assert unknown.status_code == 404


def test_staff_can_book_hold_and_hold_expiry_through_api(api, guest, operator, units) -> None:
    """نگه‌داشت موقت از مسیر API و آزادسازی خودکار پس از انقضا."""
    from apps.reservation.policy import set_policy
    from apps.reservation.services import expire_holds

    set_policy(
        key="reservation.holds_enabled",
        value=True,
        actor=operator,
        reason="فعال‌سازی نگه‌داشت برای تست مسیر HTTP",
    )
    start, end = lattice_period(start_day=days_ahead(20), cells=2)
    api.force_authenticate(user=guest)
    created = api.post("/api/v1/reservations", _payload("B3", start, end), format="json")
    assert created.status_code == 201
    assert created.json()["status"] == ReservationStatus.HOLD

    hold = Reservation.objects.get(code=created.json()["code"])
    assert hold.hold_expires_at is not None
    assert expire_holds(now=hold.hold_expires_at + timedelta(minutes=1)) == 1
    hold.refresh_from_db()
    assert hold.status == ReservationStatus.EXPIRED
