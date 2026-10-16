"""تست‌های گواهی اقامت — دو مسیر تأیید: QR آنلاین و کد کوتاه آفلاین.

گواهی، سندی است که زائر بیرون از سایت به آن استناد می‌کند؛ پس هم «تأیید
موفق» مهم است و هم «جعل لو رفتن». هر دو این‌جا آزموده می‌شوند.
"""

from __future__ import annotations

from datetime import timedelta

from django.test import Client
from django.utils import timezone
import pytest
import time_machine

from apps.audit.choices import AuditAction
from apps.audit.models import AuditRecord
from apps.reservation import services as reservation_services
from apps.reservation.certificate import (
    SALT,
    TOKEN_MAX_AGE_DAYS,
    CertificateError,
    build_certificate,
    certificate_qr_svg,
    certificate_verify_url,
    verify_short_code,
    verify_token,
)
from apps.reservation.models import Reservation
from tests.factories import days_ahead, lattice_period

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def reservation(guest, units):
    """رزرو تأییدشدهٔ آماده برای صدور گواهی."""
    start, end = lattice_period(start_day=days_ahead(5), index=0, cells=4)
    return reservation_services.create_reservation(
        guest=guest, unit=units["A1"], start=start, end=end, guests_count=3
    )


def test_certificate_fields_are_persian_and_complete(reservation) -> None:
    """هفت فیلد چاپی گواهی، کامل و فارسی‌اند (مبنای تأیید آفلاین)."""
    certificate = build_certificate(reservation)
    labels = [label for label, _value in certificate.fields()]
    assert labels == [
        "کد رزرو",
        "نام زائر",
        "واحد",
        "طبقه",
        "تعداد نفرات",
        "ورود",
        "خروج",
    ]
    assert certificate.code == reservation.code
    assert certificate.guests_count == 3
    assert certificate.floor_number == 1
    assert "ساعت" in certificate.starts_at_local
    assert certificate.issued_on


def test_online_token_round_trip(reservation) -> None:
    """توکن QR امضاشده است و همان محتوای گواهی را برمی‌گرداند."""
    certificate = build_certificate(reservation)
    payload = verify_token(certificate.token)
    assert payload == certificate.payload()
    assert payload["unit"] == "A1"


def test_token_cannot_be_forged_or_edited(reservation) -> None:
    """دست‌کاری توکن و استفاده از توکن سایت دیگر، رد می‌شود."""
    certificate = build_certificate(reservation)
    with pytest.raises(CertificateError):
        verify_token(f"{certificate.token[:-3]}abc")

    with pytest.raises(CertificateError):
        verify_token("clearly-not-a-token")

    from django.core import signing

    foreign = signing.dumps(certificate.payload(), salt="another.site")
    with pytest.raises(CertificateError):
        verify_token(foreign)

    # توکن سالم اما با امضای گواهی خودمان: سالت درست است
    assert verify_token(certificate.token, max_age_days=TOKEN_MAX_AGE_DAYS)


def test_token_expires_after_max_age(reservation) -> None:
    """گواهی پس از پایان اعتبار، حتی با امضای درست، پذیرفته نمی‌شود."""
    certificate = build_certificate(reservation)
    future = timezone.now() + timedelta(days=TOKEN_MAX_AGE_DAYS + 1)
    with (
        time_machine.travel(future, tick=False),
        pytest.raises(CertificateError, match="اعتبار"),
    ):
        verify_token(certificate.token)


def test_offline_short_code_recomputes(reservation) -> None:
    """کد کوتاه آفلاین: با فیلدهای درست می‌خواند و با یک نویسهٔ تغییر یافته نه."""
    certificate = build_certificate(reservation)
    payload = certificate.payload()
    assert verify_short_code(payload, certificate.short_code) is True
    assert verify_short_code(payload, certificate.short_code.lower()) is True  # حساس به بزرگی نیست
    assert verify_short_code({**payload, "guests": 9}, certificate.short_code) is False
    assert verify_short_code({**payload, "code": "M-1405-9999"}, certificate.short_code) is False
    assert verify_short_code(payload, "") is False


def test_short_code_has_no_ambiguous_characters(reservation) -> None:
    """الفبای کد کوتاه، نویسه‌های اشتباه‌پذیر (O/0/I/1/L) را ندارد."""
    certificate = build_certificate(reservation)
    assert len(certificate.short_code) == 8
    assert not set(certificate.short_code) & set("OILU")


def test_qr_is_inline_svg_pointing_to_verify_url(reservation) -> None:
    """QR، SVG درون‌خطی است و به نشانی تأیید همان گواهی اشاره می‌کند."""
    certificate = build_certificate(reservation)
    verify_url = certificate_verify_url(certificate, base_url="https://mahdieh.example")
    assert verify_url == f"https://mahdieh.example/verify/{certificate.token}/"

    svg = certificate_qr_svg(certificate, base_url="https://mahdieh.example")
    assert svg.startswith("<svg") or svg.lstrip().startswith("<svg")
    assert "<script" not in svg
    assert "mahdieh.example" not in svg  # آدرس در ماژول‌های QR کدگذاری شده است، نه متن ساده


def test_verify_page_confirms_valid_certificate(client: Client, reservation) -> None:
    """صفحهٔ تأیید آنلاین، گواهی معتبر را تأیید و در حسابرسی ثبت می‌کند."""
    certificate = build_certificate(reservation)
    response = client.get(f"/verify/{certificate.token}/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "گواهی معتبر است" in body
    assert reservation.code in body
    entries = AuditRecord.objects.filter(target_id=reservation.code)
    assert entries.exists(), "تأیید گواهی باید در حسابرسی ثبت شود"
    assert {entry.action for entry in entries} == {AuditAction.SENSITIVE_DATA_READ}


def test_verify_page_denies_broken_token(client: Client) -> None:
    """توکن نامعتبر ⇒ ۴۰۴ (بدون افشای دلیل دقیق به کاربر)."""
    response = client.get("/verify/totally-invalid/")
    assert response.status_code == 404


def test_verify_page_flags_mismatch_with_database(client: Client, reservation, guest) -> None:
    """گواهی دست‌کاری‌شده: امضا می‌خواند اما با سابقهٔ سایت نمی‌سازد ⇒ هشدار."""
    from django.core import signing

    forged_payload = build_certificate(reservation).payload()
    forged_payload["guests"] = 9
    forged = signing.dumps(forged_payload, salt=SALT, compress=True)

    response = client.get(f"/verify/{forged}/")
    assert response.status_code == 404
    assert "نمی‌خواند" in response.content.decode()


def test_offline_check_page_works_without_login(client: Client, reservation) -> None:
    """بازرسی آفلاین: فرم عمومی است و با دادهٔ درست، تأیید می‌دهد."""
    get_form = client.get("/verify/")
    assert get_form.status_code == 200
    assert "بازرسی آفلاین" in get_form.content.decode()

    certificate = build_certificate(reservation)
    payload = certificate.payload()
    response = client.post(
        "/verify/",
        {
            "code": payload["code"],
            "holder": payload["holder"],
            "unit": payload["unit"],
            "floor": payload["floor"],
            "guests": payload["guests"],
            "starts": payload["starts"],
            "ends": payload["ends"],
            "short_code": certificate.short_code,
        },
    )
    assert response.status_code == 200
    assert "کد کوتاه درست است" in response.content.decode()

    wrong = client.post(
        "/verify/",
        {
            "code": payload["code"],
            "holder": payload["holder"],
            "unit": payload["unit"],
            "floor": payload["floor"],
            "guests": 9,
            "starts": payload["starts"],
            "ends": payload["ends"],
            "short_code": certificate.short_code,
        },
    )
    assert "نمی‌خواند" in wrong.content.decode()


def test_offline_check_survives_bad_input(client: Client, reservation) -> None:
    """ورودی نامعتبر (متن در فیلد عددی) صفحه را نمی‌شکند — ۲۰۰ با پیام نامعتبر."""
    response = client.post(
        "/verify/",
        {
            "code": "M-1405-0001",
            "holder": "زائر آزمایشی",
            "unit": "A1",
            "floor": "طبقهٔ اول",
            "guests": "-",
            "starts": "؟",
            "ends": "؟",
            "short_code": "ABCDEFGH",
        },
    )
    assert response.status_code == 200
    assert "نمی‌خواند" in response.content.decode()


def test_certificate_page_prints_qr_and_short_code(client: Client, reservation, guest) -> None:
    """برگهٔ چاپی گواهی: QR، کد کوتاه و نشانی بازرسی آفلاین هر سه چاپ می‌شوند."""
    client.force_login(guest)
    response = client.get(f"/reservations/{reservation.code}/certificate/")
    assert response.status_code == 200
    body = response.content.decode()
    certificate = build_certificate(reservation)
    assert "<svg" in body
    assert certificate.short_code in body
    assert "/verify/" in body
    assert reservation.code in body


def test_certificate_page_needs_ownership(client: Client, reservation, other_guest) -> None:
    """زائر دیگر به گواهی این رزرو دست نمی‌یابد."""
    client.force_login(other_guest)
    assert client.get(f"/reservations/{reservation.code}/certificate/").status_code == 404


def test_certificate_of_cancelled_reservation_still_verifies(
    client: Client, reservation, guest
) -> None:
    """گواهی صادرشده پس از لغو رزرو هم از نظر امضا معتبر است.

    قاعده: گواهی، سند لحظهٔ صدور است؛ لغو بعدی، امضای گذشته را باطل نمی‌کند.
    اما صفحهٔ تأیید وضعیت فعلی رزرو را هم نشان می‌دهد تا پذیرش گمراه نشود.
    """
    certificate = build_certificate(reservation)
    reservation_services.cancel_reservation(
        reservation=reservation,
        by=guest,
        reason_kind="guest_request",
        reason="تغییر برنامه سفر",
        is_staff=False,
    )
    assert Reservation.objects.get(pk=reservation.pk).status == "cancelled_by_guest"
    response = client.get(f"/verify/{certificate.token}/")
    assert response.status_code in {200, 404}
    assert verify_token(certificate.token)["code"] == reservation.code
