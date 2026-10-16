"""تست‌های تحلیل‌ها — درستی محاسبه، انسجام با موجودی، و نگهبانی دسترسی.

ستون فقرات این فایل یک پرسش است: «آیا عددی که داشبورد نشان می‌دهد با واقعیت
رزروها می‌خواند؟» پس هر تست یک رزرو واقعی می‌سازد و همان چیزی را می‌سنجد که
مدیر روی صفحه می‌بیند.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.utils import timezone
import pytest
import time_machine

from apps.analytics import charts, insights
from apps.reservation import services as reservation_services
from apps.reservation.availability import availability_matrix
from apps.reservation.choices import ReservationStatus
from apps.reservation.policy import set_policy
from tests.factories import days_ahead, lattice_period, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _book(*, guest, unit, start, end, guests: int = 2):
    """رزرو فعال بسازد (میان‌بر خوانا برای تست‌ها)."""
    return reservation_services.create_reservation(
        guest=guest, unit=unit, start=start, end=end, guests_count=guests
    )


def test_occupancy_counts_real_reservations(guest, units) -> None:
    """نیم‌روزهای اشغال از رزرو واقعی می‌آید، نه از وضعیت شبکهٔ موجودی."""
    start, end = lattice_period(start_day=days_ahead(3), index=0, cells=2)
    _book(guest=guest, unit=units["A1"], start=start, end=end)

    series = insights.occupancy_series(start_day=days_ahead(3), days=1)
    assert len(series) == 1
    point = series[0]
    # پنج واحد × دو نیم‌روز = ۱۰ نیم‌روز ظرفیت روزانه
    assert point.total_cells == 10
    assert point.occupied_cells == 2
    assert point.free_cells == 8
    assert point.rate == pytest.approx(0.2)
    assert point.state == "future"


def test_history_is_not_read_as_full(units, guest) -> None:
    """روزهای گذشته «پر» شمرده نمی‌شوند — تفکیک تاریخ از موجودی لحظه‌ای.

    اگر نرخ اشغال از ماتریس موجودی خوانده می‌شد، هر روز گذشته ۱۰۰٪ می‌شد، چون
    سانس‌های گذشته دیگر آزاد نیستند. این تست همان رگرسیون را قفل می‌کند.
    """
    series = insights.occupancy_series(start_day=timezone.localdate() - timedelta(days=6), days=7)
    assert all(point.occupied_cells == 0 for point in series)
    assert all(point.rate == 0.0 for point in series)
    assert series[0].state == "past"


def test_cancelled_reservation_frees_capacity(guest, units) -> None:
    """لغو رزرو، ظرفیت را در تحلیل برمی‌گرداند (حقیقت تاریخی به‌روز می‌ماند)."""
    start, end = lattice_period(start_day=days_ahead(4), index=0, cells=2)
    reservation = _book(guest=guest, unit=units["A2"], start=start, end=end)
    assert insights.summary(start_day=days_ahead(4), days=1)["occupied_cells"] == 2

    reservation_services.cancel_reservation(
        reservation=reservation,
        by=guest,
        reason_kind="guest_request",
        reason="منصرف شدم",
        is_staff=False,
    )
    assert insights.summary(start_day=days_ahead(4), days=1)["occupied_cells"] == 0


def test_completed_stay_stays_in_history(guest, operator, units) -> None:
    """اقامت انجام‌شده در تاریخ می‌ماند — اشغال گذشته فراموش نمی‌شود.

    اقامت با مسیر **واقعی** ساخته می‌شود (سرویس دامنه)، پس ساعت سیستم را به پنج
    روز پیش می‌بریم: قاعدهٔ «رزرو در گذشته ممنوع» درست است و نباید برای تست دور
    زده شود؛ سفر زمانی، هم قاعده را نگه می‌دارد و هم دادهٔ تاریخی واقعی می‌سازد.
    """
    start, end = lattice_period(start_day=days_ahead(-5), index=0, cells=2)
    with time_machine.travel(start - timedelta(days=2), tick=False):
        reservation = _book(guest=guest, unit=units["B1"], start=start, end=end)
        reservation_services.check_in(reservation=reservation, by=operator)
        live = reservation.__class__.objects.get(pk=reservation.pk)
        with time_machine.travel(end + timedelta(hours=1), tick=False):
            reservation_services.check_out(reservation=live, by=operator)

    refreshed = reservation.__class__.objects.get(pk=reservation.pk)
    assert refreshed.status == ReservationStatus.COMPLETED
    assert insights.summary(start_day=days_ahead(-5), days=1)["occupied_cells"] == 2


def test_unit_day_matrix_matches_availability_matrix(units, guest) -> None:
    """نقشهٔ حرارتی با ماتریس موجودی هم‌خوان است (دو مسیر، یک حقیقت)."""
    start, end = lattice_period(start_day=days_ahead(2), index=0, cells=2)
    _book(guest=guest, unit=units["B2"], start=start, end=end)

    matrix = availability_matrix(start_day=days_ahead(2), days=1, staff=True, use_cache=False)
    day = matrix[0]
    unit_rows = {unit.code: unit for unit in day.units}
    occupied_by_unit = {
        code: len([cell for cell in unit.cells if cell.status != "free"])
        for code, unit in unit_rows.items()
    }

    report = insights.unit_day_matrix(start_day=days_ahead(2), days=1)
    rates = {row["code"]: row["rates"][0] for row in report["rows"]}
    for code, occupied in occupied_by_unit.items():
        expected = round(occupied / len(unit_rows[code].cells), 4)
        assert rates[code] == pytest.approx(expected)


def test_weekday_profile_sorted_and_ranked(guest, units) -> None:
    """پروفایل هفته از پرتقاضاترین روز به کم‌تقاضاترین مرتب و رتبه‌گذاری می‌شود."""
    rows = insights.weekday_profile(days=28)
    assert len(rows) == 7
    occupancies = [row["occupancy"] for row in rows]
    assert occupancies == sorted(occupancies, reverse=True)
    assert [row["rank"] for row in rows] == list(range(1, 8))
    assert all(row["samples"] == 4 for row in rows)


def test_upcoming_pressure_flags_empty_and_tight_days(guest, units) -> None:
    """فشار پیش‌رو روزهای خالی را جدا و روزهای پر را جدا گزارش می‌کند."""
    pressure = insights.upcoming_pressure(days=10)
    assert pressure["empty_count"] == 10
    assert pressure["tight_count"] == 0

    # یک روز کامل را پر کنیم: هر پنج واحد، دو نیم‌روز ⇒ ۱۰۰٪
    day = days_ahead(3)
    for index, unit in enumerate(units.values()):
        guest_i = make_user(phone=f"09120001{index:03d}")
        start, end = lattice_period(start_day=day, index=0, cells=2)
        _book(guest=guest_i, unit=unit, start=start, end=end)

    pressure = insights.upcoming_pressure(days=10)
    assert pressure["tight_count"] == 1
    assert pressure["tight_days"][0].rate == pytest.approx(1.0)


def test_dashboard_window_is_trailing_and_consistent(guest, units) -> None:
    """پنجرهٔ پیش‌فرض داشبورد، گذشتهٔ منتهی به امروز است و همهٔ بخش‌ها هم‌پنجره‌اند."""
    data = insights.dashboard(days=30)
    assert data["end_day"] == timezone.localdate()
    assert data["start_day"] == timezone.localdate() - timedelta(days=29)
    assert data["days"] == 30
    assert data["window_days"] == 30
    assert data["planning"]["days"] == 30
    assert data["pressure"]["days"] == 30
    assert data["total_cells"] == data["occupied_cells"] + data["free_cells"]


def test_long_window_is_chunked_without_error(units) -> None:
    """پنجرهٔ بلندتر از سقف ماتریس موجودی هم بی‌خطا محاسبه می‌شود."""
    series = insights.occupancy_series(
        start_day=timezone.localdate() - timedelta(days=120), days=150
    )
    assert len(series) == 150
    assert series[-1].day == timezone.localdate() + timedelta(days=29)


def test_policy_change_affects_future_windows_only(manager, units) -> None:
    """تغییر سیاست، پنجرهٔ آیندهٔ بعدی را عوض می‌کند اما دادهٔ گذشته را نه."""
    before = insights.occupancy_series(start_day=days_ahead(1), days=2)
    set_policy(
        key="lattice.cell_hours",
        value=12,
        actor=manager,
        reason="آزمون پایداری پنجره",
    )
    set_policy(
        key="lattice.grid_start_local",
        value=8,
        actor=manager,
        reason="آزمون پایداری پنجره",
    )
    after = insights.occupancy_series(start_day=days_ahead(1), days=2)
    assert before == after  # نوع/ترتیب‌ها و ظرفیت روزانه تغییر نمی‌کند


def test_charts_are_csp_safe_and_persian() -> None:
    """نمودارها SVG درون‌خطی‌اند، ارقام فارسی دارند و به منبع بیرونی وابسته نیستند."""
    bars = charts.bars([("A1", 0.42), ("B1", 0.0)], label="آزمون")
    assert bars.startswith("<svg")
    # نمودار باید کاملاً خودبسنده باشد: بدون اسکریپت، بدون منبع بیرونی و بدون
    # ارجاع خارجی — چون CSP سایت هیچ منبع بیرونی را اجازه نمی‌دهد.
    assert "<script" not in bars
    assert "xlink:href" not in bars and "url(" not in bars
    assert "cdn." not in bars
    assert "۴۲٪" in bars  # درصد با ارقام و نشانهٔ فارسی چاپ می‌شود
    assert "42%" not in bars

    gauge = charts.gauge(0.5)
    assert "۵۰٪" in gauge and "50%" not in gauge
    heat = charts.heatmap(rows=[("A1", [0.1, 0.9])], columns=2, column_labels=["۱", "۲"])
    assert heat.startswith("<svg")
    assert "۹۰٪" in heat


def test_insights_api_requires_staff(api, guest, operator, units) -> None:
    """API تحلیل‌ها برای زائر بسته و برای کارکنان باز است."""
    api.force_authenticate(user=guest)
    assert api.get("/api/v1/staff/insights").status_code == 403

    api.force_authenticate(user=operator)
    response = api.get("/api/v1/staff/insights?days=30")
    assert response.status_code == 200
    payload = response.json()
    assert payload["window_days"] == 30
    assert payload["start"] <= payload["end"]
    assert "planning" in payload and "rows" in payload["planning"]


def test_insights_page_renders_for_staff(client, operator, units) -> None:
    """صفحهٔ داشبورد برای کارکنان با نمودار و اعداد فارسی رندر می‌شود."""
    client.force_login(operator)
    response = client.get("/board/insights/?days=7")
    assert response.status_code == 200
    body = response.content.decode()
    assert "<svg" in body
    assert "نقشهٔ حرارتی" in body
    assert "روند اشغال" in body


def test_insights_page_denies_guest(client, guest) -> None:
    """زائر به داشبورد مدیریتی دسترسی ندارد."""
    client.force_login(guest)
    response = client.get("/board/insights/")
    assert response.status_code in {302, 403}


def test_window_clamp_and_validation(units) -> None:
    """پنجرهٔ بیش از حد بزرگ به سقف مجاز کوتاه می‌شود و بازهٔ نامعتبر رد می‌شود."""
    assert insights.occupancy_series(start_day=date.today(), days=1000)
    assert (
        len(insights.occupancy_series(start_day=date.today(), days=1000))
        == insights.MAX_WINDOW_DAYS
    )
    with pytest.raises(ValueError, match="دست‌کم"):
        insights.occupancy_series(start_day=date.today(), days=0)
