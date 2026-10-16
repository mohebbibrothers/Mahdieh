"""آزمون قرارداد فرانت جدا — CORS، کوکی بی‌دامنه‌ای و مبدأهای مجاز.

چرا این آزمون‌ها لازم‌اند؟ فرانت شرکت روی دامنهٔ دیگری اجرا می‌شود. اگر پیکربندی
بی‌دامنه‌ای غلط باشد، مرورگر **پیام خطای شبکه** نشان می‌دهد (نه پیام API)، و تیم
فرانت ساعت‌ها به‌دنبال باگی می‌گردد که در بک‌اند نیست. این آزمون‌ها همان لحظهٔ
«درست/غلط پیکربندی» را می‌سنجند — پیش از تحویل، نه روی دست فرانت.
"""

from __future__ import annotations

from typing import Any

from django.test import override_settings
import pytest

from apps.core.checks import cors_configuration_check
from apps.core.xorigin import is_valid_origin, parse_origins

FRONTEND = "https://app.mahdieh.example"
OTHER = "https://evil.example"
OPEN_SETTINGS: dict[str, Any] = {
    "CORS_ALLOWED_ORIGINS": [FRONTEND],
    "CORS_ALLOW_CREDENTIALS": True,
    "CSRF_TRUSTED_ORIGINS": [FRONTEND],
}

pytestmark = pytest.mark.django_db


# ── ۱) نرمال‌سازی مبدأها (تابع خالص) ─────────────────────────────────────────


def test_origins_are_normalised_without_duplicates() -> None:
    """فاصله و اسلش پایانی پاک می‌شود؛ تکرار حذف و ترتیب حفظ می‌شود."""
    assert parse_origins([" https://app.example/ ", "", "https://app.example"]) == [
        "https://app.example"
    ]
    assert parse_origins(["http://localhost:5173", "https://a.example"]) == [
        "http://localhost:5173",
        "https://a.example",
    ]


def test_origin_shape_is_strict() -> None:
    """مبدأ باید scheme+host(+port) باشد: مسیر، پرس‌وجو، اسلش پایانی و ``*`` نه."""
    assert is_valid_origin("https://app.example")
    assert is_valid_origin("http://localhost:5173")
    assert not is_valid_origin("https://app.example/")
    assert not is_valid_origin("https://app.example/path")
    assert not is_valid_origin("*")
    assert not is_valid_origin("app.example")


# ── ۲) رفتار واقعی پاسخ‌ها ───────────────────────────────────────────────────


def test_closed_by_default(client: Any) -> None:
    """پیش‌فرض بسته است: هیچ مبدأ بیگانه‌ای هدر CORS نمی‌گیرد."""
    response = client.get("/api/v1/meta", headers={"origin": OTHER})
    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response


@override_settings(**OPEN_SETTINGS)
def test_allowed_origin_gets_exact_echo_and_credentials(client: Any) -> None:
    """مبدأ مجاز، همان مبدأ را پس می‌گیرد (نه ``*``) و کوکی مجاز است."""
    response = client.get("/api/v1/meta", headers={"origin": FRONTEND})
    assert response["Access-Control-Allow-Origin"] == FRONTEND
    assert response["Access-Control-Allow-Credentials"] == "true"
    assert "origin" in response["Vary"].lower()


@override_settings(**OPEN_SETTINGS)
def test_other_origin_still_gets_nothing(client: Any) -> None:
    """باز بودن CORS برای یک مبدأ، به معنی باز بودن برای همه نیست."""
    response = client.get("/api/v1/meta", headers={"origin": OTHER})
    assert "Access-Control-Allow-Origin" not in response


@override_settings(**OPEN_SETTINGS)
def test_preflight_allows_our_write_headers(client: Any) -> None:
    """پیش‌پرواز (OPTIONS) اجازهٔ هدرهای نوشتنی ما را می‌دهد.

    بدون ``idempotency-key``، ثبت رزرو از فرانت ممکن نیست؛ بدون ``x-csrftoken``
    نوشتن رد می‌شود. هر دو باید در پاسخ پیش‌پرواز باشند.
    """
    response = client.options(
        "/api/v1/reservations",
        headers={
            "origin": FRONTEND,
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type, idempotency-key, x-csrftoken",
        },
    )
    assert response.status_code == 200
    allowed = response["Access-Control-Allow-Headers"].lower()
    assert "idempotency-key" in allowed
    assert "x-csrftoken" in allowed


# ── ۳) سیستم‌چک‌ها: پیکربندی غلط باید قرمز شود ───────────────────────────────


@override_settings()
def test_healthy_configuration_has_no_errors() -> None:
    """پیکربندی درست، هیچ ایرادی تولید نمی‌کند."""
    with override_settings(**OPEN_SETTINGS):
        assert cors_configuration_check() == []


def test_wildcard_with_credentials_is_an_error() -> None:
    """``*`` همراه کوکی: مرورگر رد می‌کند؛ پس باید خطای پیکربندی باشد."""
    with override_settings(
        CORS_ALLOWED_ORIGINS=["*"],
        CORS_ALLOW_CREDENTIALS=True,
        CSRF_TRUSTED_ORIGINS=["*"],
    ):
        codes = {issue.id for issue in cors_configuration_check()}
    assert "mahdieh.E001" in codes


def test_malformed_origin_is_an_error() -> None:
    """مبدأ با اسلش/مسیر ⇒ خطا (خطای کلاسیک «دکمه کار نمی‌کند»)."""
    with override_settings(
        CORS_ALLOWED_ORIGINS=["https://app.example/"],
        CORS_ALLOW_CREDENTIALS=True,
        CSRF_TRUSTED_ORIGINS=["https://app.example/"],
    ):
        messages = [issue.msg for issue in cors_configuration_check()]
    assert any("قالب مبدأ نادرست" in message for message in messages)


def test_origin_missing_from_csrf_trusted_is_an_error() -> None:
    """اگر CORS باز باشد ولی CSRF اعتماد نکند، نوشتن ۴۰۳ می‌شود."""
    with override_settings(
        CORS_ALLOWED_ORIGINS=[FRONTEND],
        CORS_ALLOW_CREDENTIALS=True,
        CSRF_TRUSTED_ORIGINS=[],
    ):
        codes = {issue.id for issue in cors_configuration_check()}
    assert "mahdieh.E001" in codes


def test_cross_site_cookies_without_secure_is_an_error() -> None:
    """کوکی SameSite=None بدون Secure بی‌صدا دور ریخته می‌شود ⇒ خطای پیکربندی."""
    with override_settings(
        CROSS_SITE_COOKIES=True,
        SESSION_COOKIE_SAMESITE="None",
        SESSION_COOKIE_SECURE=False,
        CSRF_COOKIE_SECURE=False,
        **OPEN_SETTINGS,
    ):
        codes = {issue.id for issue in cors_configuration_check()}
    assert "mahdieh.E003" in codes


def test_cross_site_cookies_with_secure_pass() -> None:
    """همان پیکربندی با Secure روشن، سالم است."""
    with override_settings(
        CROSS_SITE_COOKIES=True,
        SESSION_COOKIE_SAMESITE="None",
        SESSION_COOKIE_SECURE=True,
        CSRF_COOKIE_SECURE=True,
        **OPEN_SETTINGS,
    ):
        assert cors_configuration_check() == []


# ── ۴) پیوند تنظیمات با واقعیت ───────────────────────────────────────────────


def test_frontend_origins_feed_both_cors_and_csrf() -> None:
    """یک متغیر محیطی، هر دو جا را تغذیه می‌کند (منبع حقیقت یکتا).

    در تنظیمات واقعی، ``CSRF_TRUSTED_ORIGINS`` از ``MAHDIEH_CORS_ORIGINS`` ساخته
    می‌شود؛ این آزمون آن ادعا را روی **سطح کد تنظیمات** بررسی می‌کند تا کسی
    دوباره یک متغیر جدا نسازد که روزی قلم می‌افتد.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "config" / "settings" / "base.py").read_text(
        encoding="utf-8"
    )
    assert "CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, *FRONTEND_ORIGINS]" in source
    assert "CORS_ALLOW_CREDENTIALS = bool(FRONTEND_ORIGINS)" in source
