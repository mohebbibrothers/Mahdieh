"""تست لایهٔ نمایش — صفحه‌های SSR، قطعه‌های HTMX و جریان کامل زائر و کارکنان.

این تست‌ها قرارداد کاربر-محور سامانه را قفل می‌کنند: کاربری که «به‌سختی» رزرو
می‌کند، هر بار رسیدن به هدف، یک رگرسیون واقعی است.
"""

from __future__ import annotations

from datetime import timedelta
import re

from django.urls import reverse
import pytest
import time_machine

from apps.notifications.providers import MockSmsProvider
from apps.reservation import selectors as reservation_selectors
from apps.reservation.choices import ReservationSource, ReservationStatus
from apps.reservation.models import Reservation
from tests.factories import days_ahead, future_period

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

OTP_PATTERN = re.compile(r"\b(\d{5})\b")


def _code_from_sms(phone: str) -> str:
    """خواندن کد یک‌بارمصرف از صندوق پیامک ماک (هرگز از بدنهٔ HTTP)."""
    message = MockSmsProvider.last_for(phone)
    assert message is not None, "پیامک کد ورود ارسال نشد"
    match = OTP_PATTERN.search(message.body)
    assert match is not None, f"کد در متن پیامک پیدا نشد: {message.body!r}"
    return match.group(1)


# ── تقویم و صفحات عمومی ───────────────────────────────────────────────────────


def test_calendar_page_renders_full_grid(client, units) -> None:  # type: ignore[no-untyped-def]
    """صفحهٔ تقویم باید جدول واحد×نیم‌روز و راهنما را کامل رندر کند."""
    response = client.get(reverse("web:calendar"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "تقویم نیم‌روز" in content
    for code in units:
        assert code in content
    assert "بافر نظافت و تحویل" in content


def test_calendar_grid_partial_returns_fragment_only(client, units) -> None:  # type: ignore[no-untyped-def]
    """قطعهٔ HTMX نباید پوستهٔ کامل صفحه را برگرداند (بهینه‌سازی شبکه)."""
    response = client.get(reverse("web:calendar-grid"), {"days": "7"})
    assert response.status_code == 200
    content = response.content.decode()
    assert "<html" not in content.lower()
    assert 'id="calendar-grid"' in content


def test_calendar_day_filter_narrows_rows(client, units) -> None:  # type: ignore[no-untyped-def]
    """فیلتر واحد باید فقط همان واحد را نشان دهد."""
    response = client.get(reverse("web:calendar-grid"), {"unit": "A1", "days": "7"})
    content = response.content.decode()
    assert "A1" in content
    assert "B3" not in content


def test_unit_detail_unknown_code_returns_404(client, units) -> None:  # type: ignore[no-untyped-def]
    """کد ناشناخته باید ۴۰۴ بدهد، نه خطای سرور."""
    response = client.get(reverse("web:unit-detail", args=["ZZ9"]))
    assert response.status_code == 404


# ── ورود با تلفن و پروفایل ────────────────────────────────────────────────────


def test_login_flow_creates_account_and_opens_session(client, units) -> None:  # type: ignore[no-untyped-def]
    """ثبت‌نام با شمارهٔ تلفن: کد پیامک، تأیید و باز شدن نشست."""
    phone = "09121112233"
    step_one = client.post(reverse("web:login"), {"phone": phone})
    assert step_one.status_code == 200
    assert "کد پیامک‌شده" in step_one.content.decode()
    code = _code_from_sms("+989121112233")

    step_two = client.post(reverse("web:login-verify"), {"phone": phone, "code": code})
    assert step_two.status_code == 302
    # کاربر تازه پروفایل کامل ندارد ⇒ مسیر تکمیل پروفایل
    assert step_two["Location"] == reverse("web:profile")
    me = client.get(reverse("web:profile"))
    assert me.status_code == 200
    assert "پروفایل زائر" in me.content.decode()


def test_login_with_wrong_code_shows_error(client, units) -> None:  # type: ignore[no-untyped-def]
    """کد نادرست باید پیام روشن بدهد و نشست باز نشود."""
    phone = "09121112244"
    client.post(reverse("web:login"), {"phone": phone})
    response = client.post(reverse("web:login-verify"), {"phone": phone, "code": "00000"})
    assert response.status_code == 200
    content = response.content.decode()
    assert "درست نیست" in content or "معتبر نیست" in content or "منقضی" in content
    assert client.get(reverse("web:my_reservations")).status_code == 302


def test_booking_requires_complete_profile(client, units) -> None:  # type: ignore[no-untyped-def]
    """کاربر بدون پروفایل، پیش از هر چیز به فرم پروفایل هدایت می‌شود (ثابت I5)."""
    phone = "09121112255"
    client.post(reverse("web:login"), {"phone": phone})
    client.post(
        reverse("web:login-verify"), {"phone": phone, "code": _code_from_sms("+989121112255")}
    )
    response = client.get(reverse("web:book", args=["A1"]))
    assert response.status_code == 302
    assert reverse("web:profile") in response["Location"]


def test_profile_completion_then_booking_page_opens(client, units) -> None:  # type: ignore[no-untyped-def]
    """پس از تکمیل پروفایل، فرم رزرو باز می‌شود."""
    phone = "09121112266"
    client.post(reverse("web:login"), {"phone": phone})
    client.post(
        reverse("web:login-verify"), {"phone": phone, "code": _code_from_sms("+989121112266")}
    )
    saved = client.post(
        reverse("web:profile"),
        {
            "first_name": "مریم",
            "last_name": "حسینی",
            "national_id": "0499370899",
            "province": "khorasan_razavi",
            "city": "مشهد",
            "next": reverse("web:book", args=["A1"]),
        },
    )
    assert saved.status_code == 302
    assert saved["Location"] == reverse("web:book", args=["A1"])
    page = client.get(reverse("web:book", args=["A1"]))
    assert page.status_code == 200
    assert "ثبت نهایی رزرو" in page.content.decode()


# ── رزرو، برآورد و لغو ────────────────────────────────────────────────────────


def test_booking_flow_creates_reservation_and_is_idempotent(client, guest, units) -> None:  # type: ignore[no-untyped-def]
    """فرم رزرو: یک رزرو می‌سازد و ارسال دوبارهٔ همان کلید، رزرو دوم نمی‌سازد."""
    client.force_login(guest)
    payload = {
        "unit_code": "A1",
        "day": days_ahead(3).isoformat(),
        "period": "day",
        "cells": "2",
        "guests_count": "3",
        "idempotency_key": "web-test-key-1",
    }
    first = client.post(reverse("web:book", args=["A1"]), payload)
    assert first.status_code == 302
    second = client.post(reverse("web:book", args=["A1"]), payload)
    assert second.status_code == 302
    assert second["Location"] == first["Location"]
    assert Reservation.objects.filter(guest=guest).count() == 1
    reservation = Reservation.objects.get(guest=guest)
    assert reservation.source == ReservationSource.WEB
    assert reservation.status == ReservationStatus.CONFIRMED

    detail = client.get(first["Location"])
    assert detail.status_code == 200
    assert reservation.code in detail.content.decode()


def test_quote_partial_explains_conflict(client, guest, other_guest, units) -> None:  # type: ignore[no-untyped-def]
    """برآورد زنده باید تصادم را با پیام روشن و جایگزین نشان دهد، نه با خطای مبهم."""
    from apps.reservation import services

    start, end = future_period(days=4, cells=2)
    services.create_reservation(
        guest=other_guest, unit=units["A1"], start=start, end=end, guests_count=2
    )

    client.force_login(guest)
    response = client.post(
        reverse("web:book-quote", args=["A1"]),
        {
            "unit_code": "A1",
            "day": start.date().isoformat(),
            "period": "day",
            "cells": "2",
            "guests_count": "2",
        },
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert "قابل ثبت نیست" in content
    assert "اشغال" in content


def test_guest_cancel_frees_the_slot(client, guest, units) -> None:  # type: ignore[no-untyped-def]
    """لغو توسط زائر باید فوراً سانس را آزاد کند (سناریوی اصلی محصول)."""
    from apps.reservation import services

    client.force_login(guest)
    start, end = future_period(days=6, cells=2)
    reservation = services.create_reservation(
        guest=guest, unit=units["B1"], start=start, end=end, guests_count=2
    )
    response = client.post(
        reverse("web:reservation_cancel", args=[reservation.code]),
        {
            "expected_version": reservation.version,
            "reason_kind": "guest_request",
            "reason": "تغییر برنامه",
        },
    )
    assert response.status_code == 302
    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.CANCELLED_BY_GUEST

    again = client.post(
        reverse("web:book-quote", args=["B1"]),
        {
            "unit_code": "B1",
            "day": start.date().isoformat(),
            "period": "day",
            "cells": "2",
            "guests_count": "2",
        },
    )
    assert "قابل ثبت" in again.content.decode()


def test_guest_cannot_open_another_guests_reservation(client, guest, other_guest, units) -> None:  # type: ignore[no-untyped-def]
    """رزرو دیگران برای زائر نامرئی است (۴۰۴، بدون افشای وجود)."""
    from apps.reservation import services

    start, end = future_period(days=7, cells=2)
    reservation = services.create_reservation(
        guest=other_guest, unit=units["A2"], start=start, end=end, guests_count=2
    )
    client.force_login(guest)
    response = client.get(reverse("web:reservation_detail", args=[reservation.code]))
    assert response.status_code == 404


def test_certificate_page_is_printable(client, guest, units) -> None:  # type: ignore[no-untyped-def]
    """گواهی اقامت باید کد پیگیری و دکمهٔ چاپ داشته باشد."""
    from apps.reservation import services

    client.force_login(guest)
    start, end = future_period(days=8, cells=2)
    reservation = services.create_reservation(
        guest=guest, unit=units["B2"], start=start, end=end, guests_count=4
    )
    response = client.get(reverse("web:reservation_certificate", args=[reservation.code]))
    assert response.status_code == 200
    content = response.content.decode()
    assert "گواهی اقامت" in content
    assert reservation.code in content
    assert "چاپ گواهی" in content


# ── تابلوی کارکنان ────────────────────────────────────────────────────────────


def test_board_is_private(client, guest, units) -> None:  # type: ignore[no-untyped-def]
    """تابلوی پذیرش: مهمان ۴۰۳ می‌گیرد و کاربر ناشناس به ورود هدایت می‌شود."""
    anonymous = client.get(reverse("web:board"))
    assert anonymous.status_code == 302
    assert reverse("web:login") in anonymous["Location"]

    client.force_login(guest)
    assert client.get(reverse("web:board")).status_code == 403


def test_board_shows_arrivals_and_actions(client, operator, guest, units) -> None:  # type: ignore[no-untyped-def]
    """تابلو باید ورودی امروز/آیندهٔ نزدیک را با کنش‌های عملیاتی نشان دهد."""
    from apps.reservation import services

    start, end = future_period(days=1, cells=2)
    reservation = services.create_reservation(
        guest=guest, unit=units["A1"], start=start, end=end, guests_count=2
    )
    client.force_login(operator)
    response = client.get(reverse("web:board"), {"day": start.date().isoformat()})
    content = response.content.decode()
    assert response.status_code == 200
    assert reservation.code in content
    assert "تابلوی پذیرش" in content
    assert "خروجی اکسل" in content


def test_board_check_in_and_check_out(client, operator, guest, units) -> None:  # type: ignore[no-untyped-def]
    """پذیرش و تخلیه از تابلو — دو کنش کلیدی کارکنان (با سفر زمانی به پنجرهٔ اقامت)."""
    from apps.reservation import services
    from tests.factories import lattice_period

    client.force_login(operator)
    target_day = days_ahead(1)
    start, end = lattice_period(start_day=target_day, index=0, cells=2)
    reservation = services.create_reservation(
        guest=guest, unit=units["A1"], start=start, end=end, guests_count=2
    )

    with time_machine.travel(reservation.starts_at + timedelta(hours=1), tick=False):
        checked = client.post(
            reverse("web:board-action", args=[reservation.code, "check_in"]),
            {"board_day": target_day.isoformat()},
        )
        assert checked.status_code == 302
        reservation.refresh_from_db()
        assert reservation.status == ReservationStatus.CHECKED_IN

        out = client.post(
            reverse("web:board-action", args=[reservation.code, "check_out"]),
            {"board_day": target_day.isoformat()},
        )
        assert out.status_code == 302
    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.COMPLETED


def test_board_block_create_and_remove(client, operator, units) -> None:  # type: ignore[no-untyped-def]
    """بلوک نگهداری: ساخت با دلیل، سپس برداشتن با دلیل."""
    from apps.inventory.models import Block

    client.force_login(operator)
    tomorrow = days_ahead(1)
    created = client.post(
        reverse("web:board-block-create"),
        {
            "unit_code": "A1",
            "start_local": f"{tomorrow.isoformat()}T08:00",
            "end_local": f"{tomorrow.isoformat()}T20:00",
            "kind": "maintenance",
            "reason": "تعمیر کولر",
            "board_day": tomorrow.isoformat(),
        },
    )
    assert created.status_code == 302
    block = Block.objects.get()
    assert block.unit is not None and block.unit.code == "A1"

    removed = client.post(
        reverse("web:board-block-remove", args=[block.id]),
        {"reason": "تعمیر تمام شد", "board_day": tomorrow.isoformat()},
    )
    assert removed.status_code == 302
    assert Block.objects.count() == 0


def test_board_move_reservation(client, operator, guest, units) -> None:  # type: ignore[no-untyped-def]
    """جابه‌جایی رزرو توسط کارکنان: رزرو قدیم جانشین‌شده و رزرو تازه ساخته می‌شود."""
    from apps.reservation import services

    client.force_login(operator)
    start, end = future_period(days=10, cells=2)
    reservation = services.create_reservation(
        guest=guest, unit=units["A1"], start=start, end=end, guests_count=2
    )
    target_day = days_ahead(12)
    response = client.post(
        reverse("web:board-action", args=[reservation.code, "move"]),
        {
            "unit_code": "B3",
            "day": target_day.isoformat(),
            "period": "day",
            "cells": "2",
            "expected_version": reservation.version,
            "reason": "درخواست زائر برای طبقهٔ سوم",
            "board_day": start.date().isoformat(),
        },
    )
    assert response.status_code == 302
    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.SUPERSEDED
    replacement = Reservation.objects.exclude(id=reservation.id).get(guest=guest)
    assert replacement.unit.code == "B3"
    assert reservation.superseded_by_id == replacement.id


def test_my_reservations_lists_upcoming(client, guest, units) -> None:  # type: ignore[no-untyped-def]
    """«رزروهای من» باید اقامت جاری/آینده را نمایش دهد."""
    from apps.reservation import services

    client.force_login(guest)
    start, end = future_period(days=12, cells=2)
    reservation = services.create_reservation(
        guest=guest, unit=units["B1"], start=start, end=end, guests_count=2
    )
    response = client.get(reverse("web:my_reservations"))
    assert response.status_code == 200
    assert reservation.code in response.content.decode()
    assert reservation_selectors.reservations_for_user(guest).count() == 1


def test_demo_login_is_absent_when_demo_mode_is_off(client) -> None:  # type: ignore[no-untyped-def]
    """مسیر ورود نمایشی در محیط غیرنمایشی وجود ندارد — یک درِ بسته، نه پنهان."""
    response = client.get("/demo/guest/")
    assert response.status_code == 404


def test_units_pages_render_the_calendar_fragment(client, units) -> None:  # type: ignore[no-untyped-def]
    """صفحهٔ واحدها و صفحهٔ یک واحد باید نیم‌روزهای آزاد را نشان دهند.

    رگرسیون: این دو صفحه قطعهٔ تقویم را وارد می‌کنند و روزی بدون کلیدهای زمینه
    (``start_day``/``days``/``rows``) ۵۰۰ می‌دادند؛ بازرسی چشمی آن را گرفت.
    """
    for path in ("/units/", "/units/A2/"):
        response = client.get(path)
        assert response.status_code == 200, path
        body = response.content.decode()
        assert "آزاد" in body, f"ستون آزاد در {path} دیده نشد"
        assert "تقویم نیم‌روز" in body, f"قطعهٔ تقویم در {path} رندر نشد"
