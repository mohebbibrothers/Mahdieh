"""قرارداد صفحه‌بندی — «فهرست» برای فرانت یک شکل دارد، نه چند شکل.

چرا این آزمون جدا از قرارداد OpenAPI است؟ سند می‌گوید هر پاسخ چه شکلی دارد؛ این
آزمون می‌سنجد که **واقعاً همان‌طور رفتار می‌کند**: پوشش ``results``/``meta``،
احترام به ``page_size`` با سقف سخت، پیمایش مکان‌نما بدون تکرار/جاافتادگی، و
خطای خوانا برای مکان‌نمای خراب (نه ۵۰۰).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from apps.reservation import services as reservation_services
from tests.factories import days_ahead, future_period, lattice_period

pytestmark = pytest.mark.django_db


def _payload(unit_code: str, start: Any, end: Any) -> dict[str, Any]:
    """بدنهٔ ثبت رزرو — همان قرارداد API."""
    return {
        "unit_code": unit_code,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "guests_count": 2,
    }


def _book(api: Any, guest: Any, *, unit: str, days: int) -> str:
    """ثبت یک رزرو و بازگرداندن کد آن."""
    api.force_authenticate(user=guest)
    start, end = future_period(days=days, cells=2)
    response = api.post("/api/v1/reservations", _payload(unit, start, end), format="json")
    assert response.status_code == 201, response.content
    return str(response.json()["code"])


def test_owner_list_envelope_carries_total(api: Any, guest: Any, units: Any) -> None:
    """فهرست «مال من»: ``results`` + ``meta`` با شمارش کل."""
    _book(api, guest, unit="A1", days=2)
    body = api.get("/api/v1/reservations").json()
    assert set(body) == {"results", "meta"}
    assert body["meta"]["total"] == 1
    assert body["meta"]["page_size"] == 20
    assert body["meta"]["next"] is None


def test_page_size_is_honoured_and_capped(api: Any, guest: Any, units: Any) -> None:
    """``page_size`` از کلاینت پذیرفته می‌شود، ولی سقف سخت دارد (۱۰۰)."""
    _book(api, guest, unit="A1", days=2)
    small = api.get("/api/v1/reservations", {"page_size": 1}).json()
    assert small["meta"]["page_size"] == 1
    huge = api.get("/api/v1/reservations", {"page_size": 5000}).json()
    assert huge["meta"]["page_size"] == 100


def test_cursor_walk_has_no_gaps_or_repeats(api: Any, guest: Any, units: Any) -> None:
    """پیمایش مکان‌نما: همهٔ ردیف‌ها یک‌بار، بدون تکرار و بدون جاافتادگی.

    سه رزرو با **سرویس دامنه** ساخته می‌شود نه API: سیاست «یک رزرو فعال برای هر
    زائر» از مسیر API اجازهٔ سه رزرو هم‌زمان نمی‌دهد — و آن قید، موضوع این آزمون نیست.
    """
    expected = set()
    for offset in (4, 8, 12):
        start, end = lattice_period(start_day=days_ahead(offset), index=0, cells=2)
        reservation = reservation_services.create_reservation(
            guest=guest, unit=units["A1"], start=start, end=end, guests_count=2
        )
        # لغو آزاد (بیش از ۲۴ ساعت پیش از ورود، پس بی‌خنک‌شدن) تا هر سه ردیف در
        # دامنهٔ «همه» بمانند و سیاست «یک رزرو فعال» مسیر را نبندد.
        reservation_services.cancel_reservation(
            reservation=reservation,
            by=guest,
            reason_kind="guest_request",
            reason="آزمون صفحه‌بندی",
        )
        expected.add(str(reservation.code))
    api.force_authenticate(user=guest)
    seen: list[str] = []
    query = {"page_size": 2, "scope": "all"}
    for _ in range(5):  # گارد: بیش از صفحه‌های ممکن نمی‌گردیم
        response = api.get("/api/v1/reservations", query)
        assert response.status_code == 200, response.content
        body = response.json()
        seen.extend(item["code"] for item in body["results"])
        next_link = body["meta"]["next"]
        if not next_link:
            break
        # قرارداد پیمایش: نشانیِ ``meta.next`` را همان‌طور که هست دنبال کنید.
        # در آزمون، مسیرِ نسبی را از همان نشانی بیرون می‌کشیم تا کلاینت آزمون
        # درگیر میزبان مطلق نشود؛ پارامترها اما دست‌نخورده پیمایش می‌شوند.
        query = {key: value[0] for key, value in parse_qs(urlparse(next_link).query).items()}
    assert sorted(seen) == sorted(expected), seen
    assert len(seen) == len(set(seen))  # بدون تکرار


def test_broken_cursor_is_a_readable_error(api: Any, guest: Any, units: Any) -> None:
    """مکان‌نمای خراب ⇒ ۴۰۴ با بدنهٔ problem+json، نه ۵۰۰."""
    api.force_authenticate(user=guest)
    response = api.get("/api/v1/reservations", {"cursor": "not-a-cursor"})
    assert response.status_code == 404
    assert response["Content-Type"].startswith("application/problem+json")


def test_staff_board_omits_total_on_purpose(
    api: Any, guest: Any, operator: Any, units: Any
) -> None:
    """میز کارکنان شمارش کل نمی‌فرستد — کوئری گران روی میز بزرگ، ارزش کم."""
    _book(api, guest, unit="A1", days=2)
    api.force_authenticate(user=operator)
    body = api.get("/api/v1/staff/reservations", {"days": 7}).json()
    assert "total" not in body["meta"]
    assert body["meta"]["page_size"] == 50  # صفحهٔ بزرگ‌تر برای پذیرش


def test_waitlist_list_envelope(api: Any, guest: Any, other_guest: Any, units: Any) -> None:
    """فهرست صف انتظار همان قرارداد پوشش را دارد (نتایج + فراداده).

    نکته: صف انتظار فقط برای سانسِ **پُر** معنا دارد؛ پس نخست با زائر دیگری سانس
    را پر می‌کنیم — وگرنه API درست کار می‌کند و ۴۰۹ «سانس آزاد است» می‌دهد.
    """
    start, end = lattice_period(start_day=days_ahead(3), index=0, cells=2)
    reservation_services.create_reservation(
        guest=other_guest, unit=units["A1"], start=start, end=end, guests_count=2
    )
    api.force_authenticate(user=guest)
    api.post(
        "/api/v1/waitlist",
        {
            "unit_code": "A1",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "guests_count": 2,
        },
        format="json",
    )
    body = api.get("/api/v1/waitlist").json()
    assert set(body) == {"results", "meta"}
    assert body["meta"]["total"] >= 1
