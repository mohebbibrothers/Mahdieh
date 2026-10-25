"""قرارداد «کشف بازهٔ آزاد» (‎/api/v1/calendar/windows‎) — صفحهٔ تقویم P-04.

پیشنهاد کشف باید **همان** چیزی باشد که ثبت رزرو می‌پذیرد: آغاز پس از مهلت پیش از
ورود، درون افق، روی نیم‌روزهای آزاد پیوسته و متناسب با ظرفیت.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone
import pytest
import time_machine

from apps.inventory.lattice import is_grid_boundary
from apps.reservation.windows import first_free_windows
from tests.factories import days_ahead, lattice_period

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

URL = "/api/v1/calendar/windows"


def _frozen_morning() -> datetime:
    """ساعت ۰۹:۰۰ محلی پنج روز بعد ⇒ زودترین آغاز (۱۱:۰۰) به سانس شب همان روز می‌افتد."""
    day = days_ahead(5)
    return timezone.make_aware(datetime(day.year, day.month, day.day, 9, 0))


def _book(api, user, unit_code: str, start: datetime, end: datetime) -> None:
    """ثبت رزرو از مسیر واقعی API (همهٔ قیدهای موتور اعمال می‌شود)."""
    api.force_authenticate(user=user)
    body = {
        "unit_code": unit_code,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "guests_count": 2,
    }
    assert api.post("/api/v1/reservations", body, format="json").status_code == 201
    api.force_authenticate(user=None)


def test_windows_are_public_and_follow_engine_rules(api, units) -> None:
    """بی‌ورود خواندنی؛ یک ردیف برای هر واحد به ترتیب ماتریس؛ آغازها روی شبکه و پس از مهلت."""
    response = api.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert [row["code"] for row in body["units"]] == ["A1", "B1", "A2", "B2", "B3"]
    assert body["guests"] == 1
    assert body["min_cells"] == 1
    assert body["max_cells"] == 14
    not_before = datetime.fromisoformat(body["not_before"])
    horizon_end = datetime.fromisoformat(body["horizon_end"])
    for row in body["units"]:
        assert row["fits_guests"] is True
        window = row["window"]
        assert window is not None
        start = datetime.fromisoformat(window["start"])
        assert not_before <= start <= horizon_end
        assert is_grid_boundary(start)
        assert window["period"] in {"day", "night"}
        assert window["bookable_cells"] == min(window["cells"], 14)
        assert window["stay_label"]
        assert window["date_jalali"]


def test_booking_splits_run_and_min_cells_skips_short_gap(api, guest, units) -> None:
    """رزرو ران را می‌شکند؛ ‎min_cells‎ شکاف کوتاه‌تر را رد می‌کند و بازهٔ بعدی را می‌دهد."""
    moment = _frozen_morning()
    with time_machine.travel(moment, tick=False):
        day = timezone.localdate()
        # A1: شب امروز آزاد، روز فردا آزاد، از شب فردا تا صبح پس‌فردا رزرو ⇒ ران دو نیم‌روزه.
        booked_start, booked_end = lattice_period(
            start_day=day + timedelta(days=1), index=1, cells=2
        )
        _book(api, guest, "A1", booked_start, booked_end)

        first = api.get(URL).json()
        a1 = first["units"][0]["window"]
        tonight = lattice_period(start_day=day, index=1, cells=1)[0]
        assert datetime.fromisoformat(a1["start"]) == tonight
        assert a1["period"] == "night"
        assert a1["cells"] == 2
        assert datetime.fromisoformat(a1["end"]) == booked_start
        assert a1["open_ended"] is False

        longer = api.get(URL, {"min_cells": 3}).json()["units"][0]["window"]
        assert datetime.fromisoformat(longer["start"]) >= booked_end
        assert longer["cells"] >= 3
        # واحدهای بی‌رزرو همچنان از امشب آزادند.
        b1 = api.get(URL, {"min_cells": 3}).json()["units"][1]["window"]
        assert datetime.fromisoformat(b1["start"]) == tonight


def test_capacity_filter_marks_units_without_room(api, units) -> None:
    """همسفران بیش از ظرفیت ⇒ ‎fits_guests=false‎ و بی‌پیشنهاد (نه پیشنهاد گمراه‌کننده)."""
    body = api.get(URL, {"guests": 7}).json()
    assert all(row["fits_guests"] is False and row["window"] is None for row in body["units"])
    assert body["guests"] == 7


def test_conditional_get_and_epoch_invalidation(api, guest, units) -> None:
    """ETag همان ⇒ ۳۰۴؛ هر رزرو تازه نسخهٔ موجودی را عوض می‌کند ⇒ تگ تازه."""
    first = api.get(URL)
    etag = first["ETag"]
    assert etag.startswith('W/"')
    assert first["Cache-Control"] == "public, max-age=0, must-revalidate"
    assert api.get(URL, HTTP_IF_NONE_MATCH=etag).status_code == 304

    start, end = lattice_period(start_day=days_ahead(4), index=0, cells=2)
    _book(api, guest, "B2", start, end)
    after = api.get(URL, HTTP_IF_NONE_MATCH=etag)
    assert after.status_code == 200
    assert after["ETag"] != etag


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"guests": "0"}, "invalid_guests"),
        ({"guests": "61"}, "invalid_guests"),
        ({"guests": "x"}, "invalid_guests"),
        ({"min_cells": "15"}, "invalid_min_cells"),
        ({"min_cells": "0"}, "invalid_min_cells"),
    ],
)
def test_invalid_parameters_are_problem_422(api, units, params, code) -> None:
    """پارامتر بیرون از مرز ⇒ ۴۲۲ با کد ماشین‌خوان و پیام فارسی."""
    response = api.get(URL, params)
    assert response.status_code == 422
    assert response.json()["code"] == code


def test_service_respects_lead_time_and_horizon(units) -> None:
    """سرویس: سانسِ درون مهلت پیش از ورود پیشنهاد نمی‌شود و آغاز هرگز از افق نمی‌گذرد."""
    moment = _frozen_morning()
    with time_machine.travel(moment, tick=False):
        result = first_free_windows()
        now = timezone.now()
        assert result.not_before == now + timedelta(hours=2)
        assert result.horizon_end == now + timedelta(days=90)
        for row in result.units:
            assert row.window is not None
            assert row.window.start >= result.not_before
            # ساختمان خالی ⇒ ران تا پایان بازهٔ پویش ادامه دارد و سقف اقامت رعایت می‌شود.
            assert row.window.open_ended is True
            assert row.window.bookable_cells == 14
