"""تست‌های پایداری و «افتِ محترمانه» — درس‌آموختهٔ بارسنجی فاز ۸.

این فایل رفتار سامانه را در **بدترین لحظه‌ها** قفل می‌کند:
    • قطعی گذرای پایگاه‌داده ⇒ ۵۰۳ با ``Retry-After`` (نه ۵۰۰ خام)
    • قطعی کش ⇒ سایت سالم می‌ماند (کش بهینه‌سازی است، نه منبع حقیقت)
    • قطعی کش در ورود با پیامک ⇒ **بسته شکست می‌خورد** (۵۰۳)، نه بی‌سقف
    • بار مازاد ⇒ سدِ ظرفیت پاسخ سریع می‌دهد و مسیرهای پایش را معاف می‌گذارد

چرا این‌ها مهم‌اند؟ چون بارسنجی واقعی نشان داد بدون این لایه‌ها، ۱۶ کاربر
هم‌زمان کافی است تا Postgres بگوید «too many clients» و زائر صفحهٔ خطای سرور
ببیند. این تست‌ها جلوی برگشت آن حالت را می‌گیرند.
"""

from __future__ import annotations

import asyncio
from typing import Any

from django.core.cache import cache
from django.db import connection
from django.db.utils import OperationalError
import pytest

from apps.core import cache as cache_helpers
from apps.core.errors import ProviderUnavailableError
from apps.core.shedding import ConcurrencyLimitMiddleware

pytestmark = pytest.mark.django_db


class _SendCapture:
    """جمع‌کنندهٔ پیام‌های ASGI برای بازرسی پاسخ سدِ ظرفیت."""

    def __init__(self) -> None:
        """فهرست خالی پیام‌ها."""
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, message: dict[str, Any]) -> None:
        """ثبت هر پیام ارسالی."""
        self.messages.append(message)

    @property
    def status(self) -> int:
        """کد وضعیت پاسخ ثبت‌شده."""
        return int(self.messages[0]["status"])

    @property
    def headers(self) -> dict[str, str]:
        """هدرهای پاسخ به‌صورت دیکشنری رشته‌ای."""
        return {key.decode(): value.decode() for key, value in self.messages[0]["headers"]}

    @property
    def body(self) -> str:
        """بدنهٔ پاسخ."""
        return bytes(self.messages[-1]["body"]).decode()


async def _noop_receive() -> dict[str, Any]:
    """گیرندهٔ خالی (سرویس‌های ما بدنه نمی‌خوانند)."""
    return {"type": "http.request", "body": b"", "more_body": False}


# ─────────────────────────────── پایگاه‌داده ─────────────────────────────────
def test_transient_database_outage_renders_503_page(client, monkeypatch) -> None:
    """قطعی گذرای اتصال ⇒ صفحهٔ آرام ۵۰۳ + ``Retry-After`` (نه ۵۰۰)."""

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise OperationalError(
            'connection to server at "127.0.0.1", port 5432 failed: '
            "FATAL:  sorry, too many clients already"
        )

    monkeypatch.setattr(connection, "cursor", _boom)

    response = client.get("/")

    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    assert response["Cache-Control"] == "no-store"
    assert "خطای سرور" not in response.content.decode()


def test_transient_database_outage_on_api_returns_problem_json(client, monkeypatch) -> None:
    """مسیر API ⇒ بدنهٔ Problem Details با کد ``service_unavailable``."""

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(connection, "cursor", _boom)

    response = client.get("/api/v1/units")

    assert response.status_code == 503
    assert response["Content-Type"] == "application/problem+json"
    payload = response.json()
    assert payload["code"] == "service_unavailable"
    assert payload["status"] == 503
    assert payload["type"].endswith("/service_unavailable")


def test_non_transient_database_error_is_not_masked(client, monkeypatch) -> None:
    """خطای واقعی کد (مثلاً نبود جدول) نباید «۵۰۳» جا بزند — باید ۵۰۰ بماند."""

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise OperationalError('relation "reservation_reservation" does not exist')

    monkeypatch.setattr(connection, "cursor", _boom)

    client.raise_request_exception = False  # ⇐ صفت کلاینت است، نه آرگومان get()
    response = client.get("/")

    assert response.status_code == 500
    assert response.status_code != 503


# ─────────────────────────────────── کش ──────────────────────────────────────
def test_cache_outage_does_not_break_versioned_reads(monkeypatch) -> None:
    """اگر کش بخوابد، تولیدکننده اجرا می‌شود و پاسخ درست برمی‌گردد."""

    def _broken_get(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("valkey is down")

    def _broken_set(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("valkey is down")

    monkeypatch.setattr(cache_helpers.cache, "get", _broken_get)
    monkeypatch.setattr(cache_helpers.cache, "set", _broken_set)

    calls: list[int] = []

    def _producer() -> str:
        calls.append(1)
        return "دادهٔ تازه از پایگاه‌داده"

    value = cache_helpers.get_or_set_versioned("availability", "unit:1", "2026-10-20", _producer)

    assert value == "دادهٔ تازه از پایگاه‌داده"
    assert len(calls) == 1
    assert cache_helpers.get_epoch("availability", "unit:1") == 1


def test_otp_fails_closed_when_cache_is_unavailable(monkeypatch) -> None:
    """بدون کش نمی‌توان سقف پیامک را تضمین کرد ⇒ ۵۰۳ روشن، نه بی‌سقف‌شدن."""
    from apps.identity import services as identity_services

    class _BrokenCache:
        """کش معیوب: هر خواندن می‌شکند."""

        def get(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("valkey is down")

        def add(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("valkey is down")

        def set(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("valkey is down")

    monkeypatch.setattr(identity_services, "cache", _BrokenCache())

    with pytest.raises(ProviderUnavailableError) as excinfo:
        identity_services.request_otp(phone="09120000007")

    assert excinfo.value.status == 503
    assert "ورود" in excinfo.value.detail


# ───────────────────────────── سدِ ظرفیت (ASGI) ──────────────────────────────
def test_load_shedder_returns_fast_503_when_saturated() -> None:
    """با پر شدن ظرفیت، درخواست جدید بی‌درنگ ۵۰۳ می‌گیرد."""
    reached: list[str] = []

    async def _inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
        reached.append(str(scope.get("path")))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = ConcurrencyLimitMiddleware(_inner, max_concurrency=2)
    middleware.in_flight = 2  # ظرفیت پر است

    capture = _SendCapture()
    asyncio.run(
        middleware({"type": "http", "path": "/units/"}, _noop_receive, capture)  # type: ignore[arg-type]
    )

    assert capture.status == 503
    assert capture.headers["retry-after"] == "2"
    assert capture.headers["content-type"] == "application/problem+json"
    assert reached == [], "درخواست سد‌شده هرگز نباید به اپ برسد"
    assert middleware.shed_total == 1


def test_load_shedder_exempts_monitoring_and_static() -> None:
    """پایش و دارایی ایستا حتی در فشار هم پاسخ می‌گیرند."""
    reached: list[str] = []

    async def _inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
        reached.append(str(scope.get("path")))
        await send({"type": "http.response.start", "status": 200, "headers": []})

    middleware = ConcurrencyLimitMiddleware(_inner, max_concurrency=1)
    middleware.in_flight = 1

    for path in ("/health/live/", "/static/web/css/mahdieh.css"):
        asyncio.run(middleware({"type": "http", "path": path}, _noop_receive, _SendCapture()))  # type: ignore[arg-type]

    assert reached == ["/health/live/", "/static/web/css/mahdieh.css"]


def test_load_shedder_releases_capacity_after_request() -> None:
    """پس از پایان درخواست، ظرفیت آزاد می‌شود (بدون نشت شمارنده)."""

    async def _inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})

    middleware = ConcurrencyLimitMiddleware(_inner, max_concurrency=1)

    for _ in range(3):
        asyncio.run(
            middleware({"type": "http", "path": "/units/"}, _noop_receive, _SendCapture())  # type: ignore[arg-type]
        )

    assert middleware.in_flight == 0
    assert middleware.shed_total == 0


def test_load_shedder_releases_capacity_even_on_error() -> None:
    """خطای درون اپ نباید ظرفیت را برای همیشه اشغال کند."""

    async def _exploding(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise RuntimeError("view exploded")

    middleware = ConcurrencyLimitMiddleware(_exploding, max_concurrency=1)

    with pytest.raises(RuntimeError):
        asyncio.run(
            middleware({"type": "http", "path": "/units/"}, _noop_receive, _SendCapture())  # type: ignore[arg-type]
        )

    assert middleware.in_flight == 0


def test_cache_is_wired_as_resilient_backend() -> None:
    """تنظیم کش **پایه** باید خطاها را ببلعد تا یک قطعی، کل سایت را نخواباند.

    (در تست، کش لوکال‌مموری است؛ بنابراین تنظیم واقعی را از ماژول پایه می‌خوانیم.)
    """
    import config.settings.base as base_settings

    options = base_settings.CACHES["default"]["OPTIONS"]
    assert options.get("IGNORE_EXCEPTIONS") is True
    assert options.get("SOCKET_CONNECT_TIMEOUT", 0) > 0
    assert cache.get("health:probe") in (None, "ok")


def test_personal_pages_are_never_cached(client, guest) -> None:
    """صفحات شخصی باید ``private, no-store`` بگیرند (ASVS 8.1.1)."""
    client.force_login(guest)

    for path in ("/my/", "/profile/"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response["Cache-Control"] == "private, no-store", path
        assert response["Pragma"] == "no-cache"


def test_public_pages_keep_default_caching(client) -> None:
    """صفحات عمومی نباید بیهوده no-store بگیرند (کارایی)."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers.get("Cache-Control", "") != "private, no-store"


def test_calendar_export_header_is_not_overridden(client, guest) -> None:
    """هدر دقیق‌ترِ نمای تقویم نباید بازنویسی شود."""
    response = client.get("/my/calendar.ics")
    # بدون ورود ⇒ هدایت؛ همین هم باید no-store بماند تا کد رزرو در کش نماند.
    assert response.status_code in (200, 302)
    assert "no-store" in response.headers.get("Cache-Control", "")
