"""تست‌های پروندهٔ تقویم (`.ics`) — استاندارد، نه «شبیه تقویم».

پروندهٔ تقویم اگر استاندارد نباشد، بی‌صدا خراب است: تقویم گوشی آن را باز
نمی‌کند یا زمان را اشتباه نشان می‌دهد. پس این‌جا دقیقاً همان چیزهایی آزموده
می‌شود که RFC 5545 می‌خواهد: CRLF، تاکردن ۷۵ بایتی، زمان UTC، فرار متن و
شناسهٔ پایدار رخداد.
"""

from __future__ import annotations

from datetime import UTC, datetime
import re

from django.test import Client
from django.utils import timezone
import pytest

from apps.audit.choices import AuditAction
from apps.audit.models import AuditRecord
from apps.reservation import ics, services as reservation_services
from tests.factories import days_ahead, lattice_period

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

LINE_LIMIT = 75


@pytest.fixture
def reservation(guest, units):
    """رزرو تأییدشدهٔ آمادهٔ تبدیل به پروندهٔ تقویم."""
    start, end = lattice_period(start_day=days_ahead(6), index=0, cells=2)
    return reservation_services.create_reservation(
        guest=guest, unit=units["A1"], start=start, end=end, guests_count=2
    )


def test_calendar_has_required_envelope(reservation) -> None:
    """پوشش استاندارد تقویم با نسخه و شناسهٔ سازنده."""
    body = ics.reservation_ics(reservation)
    assert body.startswith("BEGIN:VCALENDAR\r\n")
    assert body.endswith("END:VCALENDAR\r\n")
    assert "VERSION:2.0" in body
    assert "PRODID:" in body
    assert "METHOD:PUBLISH" in body


def test_event_times_are_utc_and_match_reservation(reservation) -> None:
    """زمان‌ها به UTC و برابر با بازهٔ رزرو هستند (بدون جابه‌جایی ساعت)."""
    body = ics.reservation_ics(reservation)
    expected_start = reservation.starts_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    expected_end = reservation.ends_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    assert f"DTSTART:{expected_start}" in body
    assert f"DTEND:{expected_end}" in body


def test_lines_end_with_crlf_and_respect_limit(reservation) -> None:
    """هر خط با CRLF تمام می‌شود و از ۷۵ بایت فراتر نمی‌رود."""
    body = ics.reservation_ics(reservation)
    assert body.count("\r\n") > 10
    assert "\n" not in body.replace("\r\n", "")
    assert all(len(line.encode("utf-8")) <= LINE_LIMIT for line in body.split("\r\n"))


def test_folding_never_splits_farsi_characters(reservation) -> None:
    """تاکردن خطوط، کاراکترهای فارسی را نصف نمی‌کند (بایت‌شماری درست)."""
    long_note = "نظافت کامل و تحویل کلید به زائر " * 20
    reservation.guest_note = long_note[:280]
    body = ics.reservation_ics(reservation)
    unfolded = re.sub(r"\r\n ", "", body)
    assert reservation.code in unfolded
    assert "\ufffd" not in body  # نشانهٔ خرابی رمزگذاری


def test_escape_text_covers_special_characters() -> None:
    """فرار متن: بک‌اسلش، نقطه‌ویرگول، کاما و شکست خط."""
    escaped = ics.escape_text("a,b;c\\d\ne")
    assert escaped == "a\\,b\\;c\\\\d\\ne"


def test_event_carries_identity_and_alarm(reservation) -> None:
    """رخداد شناسهٔ پایدار، محل، وضعیت و یادآور ۱۲ ساعته دارد."""
    body = ics.reservation_ics(reservation)
    assert f"UID:{reservation.code}@" in body
    assert "STATUS:CONFIRMED" in body
    assert "TRANSP:OPAQUE" in body
    assert "SUMMARY:" in body and reservation.unit.code in body
    assert "BEGIN:VALARM" in body
    assert "TRIGGER:-PT12H" in body
    assert body.count("BEGIN:VEVENT") == 1


def test_multi_event_calendar_contains_all_reservations(guest, other_guest, units) -> None:
    """تقویم چندرزروی، برای هر رزرو یک VEVENT دارد.

    دو زائر متفاوت لازم است: سیاست سامانه به هر زائر تنها یک رزرو فعال می‌دهد.
    """
    first_start, first_end = lattice_period(start_day=days_ahead(10), index=0, cells=2)
    second_start, second_end = lattice_period(start_day=days_ahead(14), index=0, cells=2)
    first = reservation_services.create_reservation(
        guest=guest, unit=units["A1"], start=first_start, end=first_end, guests_count=2
    )
    second = reservation_services.create_reservation(
        guest=other_guest, unit=units["B3"], start=second_start, end=second_end, guests_count=2
    )
    body = ics.ics_for([first, second])
    assert body.count("BEGIN:VEVENT") == 2
    assert first.code in body and second.code in body


def test_calendar_file_name_is_safe() -> None:
    """نام پرونده فقط از کد رزرو ساخته می‌شود و نویسهٔ خطرناک ندارد."""
    assert ics.calendar_file_name("M-1405-0007") == "mahdieh-M-1405-0007.ics"
    assert "/" not in ics.calendar_file_name("../../etc/passwd")


def test_google_calendar_url_encodes_event(reservation) -> None:
    """پیوند گوگل، تاریخ‌ها را در قالب UTC و متن را رمزگذاری‌شده می‌فرستد."""
    url = ics.google_calendar_url(reservation)
    assert url.startswith("https://calendar.google.com/calendar/render?")
    assert "action=TEMPLATE" in url
    assert "dates=" in url and "%2F" in url
    assert " " not in url


def test_download_view_sets_headers_and_audits(client: Client, guest, reservation) -> None:
    """نمای دانلود: سرصفحهٔ درست، بی‌کش، و ثبت در حسابرسی."""
    client.force_login(guest)
    response = client.get(f"/reservations/{reservation.code}/calendar.ics")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/calendar")
    assert "attachment" in response["Content-Disposition"]
    assert response["Cache-Control"] == "private, no-store"
    body = response.content.decode("utf-8")
    assert reservation.code in body
    assert AuditRecord.objects.filter(action=AuditAction.CALENDAR_EXPORTED).exists()


def test_download_is_forbidden_for_strangers(client: Client, other_guest, reservation) -> None:
    """زائر دیگر، تقویم رزرو نامرتبط را نمی‌گیرد (۴۰۴ بدون افشا)."""
    client.force_login(other_guest)
    response = client.get(f"/reservations/{reservation.code}/calendar.ics")
    assert response.status_code == 404


def test_staff_can_download_any_calendar(client: Client, operator, reservation) -> None:
    """کارکنان برای همهٔ رزروها تقویم می‌گیرند (نیاز میز پذیرش)."""
    client.force_login(operator)
    response = client.get(f"/reservations/{reservation.code}/calendar.ics")
    assert response.status_code == 200


def test_guest_feed_and_staff_feed(client: Client, operator, guest, units) -> None:
    """تقویم «همهٔ اقامت‌های من» و تقویم میز پذیرش هر دو کار می‌کنند."""
    start, end = lattice_period(start_day=days_ahead(3), index=0, cells=2)
    created = reservation_services.create_reservation(
        guest=guest, unit=units["A1"], start=start, end=end, guests_count=2
    )
    client.force_login(guest)
    mine = client.get("/my/calendar.ics")
    assert mine.status_code == 200
    assert created.code in mine.content.decode()

    staff = Client()
    staff.force_login(operator)
    board = staff.get("/board/calendar.ics?days=30")
    assert board.status_code == 200
    assert created.code in board.content.decode()


def test_bad_window_parameter_falls_back_to_default(operator) -> None:
    """پارامتر نامعتبر ``days`` باعث خطا نمی‌شود؛ پیش‌فرض جایگزین می‌شود."""
    from apps.web.views_calendar import StaffCalendarView

    class _Request:
        GET: dict[str, str] = {"days": "abc"}

    assert StaffCalendarView._days(_Request()) == 60  # type: ignore[arg-type]


def test_format_utc_accepts_naive_datetime() -> None:
    """زمان بدون منطقه، «وقت محلی» فرض و به UTC تبدیل می‌شود (بدون خطا).

    این رفتار آگاهانه است: مدل‌ها همیشه زمان منطقه‌دار می‌دهند؛ اگر روزی کسی
    مقدار naive بدهد، ترجیح می‌دهیم به وقت محلی تفسیر شود تا اینکه تقویم خروجی
    با ۳:۳۰ اختلاف ساخته شود.
    """
    naive = datetime(2026, 3, 21, 8, 30)
    assert ics.format_utc(naive) == "20260321T050000Z"
    assert ics.format_utc(timezone.now()).endswith("Z")
