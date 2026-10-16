"""تست صفحهٔ خانه — SSR واقعی، دادهٔ واقعی دیتابیس، بدون وابستگی بیرونی."""

from __future__ import annotations

from django.utils import timezone
import pytest

from apps.core.jalali import format_jalali

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_home_renders_units_and_jalali_date(client, units) -> None:  # type: ignore[no-untyped-def]
    """صفحهٔ خانه باید واحدهای واقعی، تاریخ جلالی امروز و وضعیت زنده را نشان دهد."""
    response = client.get("/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "زائرسرای مهدیه" in content
    assert format_jalali(timezone.localdate()) in content
    for code in units:
        assert code in content
    assert "تقویم و رزرو فوری" in content
    assert "کلید درخواست شما" in content
    # وضعیت سلول‌ها از تقویم واقعی می‌آید، نه از متن ثابت
    assert "آزاد" in content


def test_home_without_units_does_not_crash(client) -> None:  # type: ignore[no-untyped-def]
    """سایت نباید به‌خاطر نبود داده سقوط کند — پیام خالی محترمانه نشان می‌دهد."""
    response = client.get("/")
    assert response.status_code == 200
    assert "هنوز واحدی ثبت نشده است" in response.content.decode()


def test_home_has_trace_id_header(client) -> None:  # type: ignore[no-untyped-def]
    """هر پاسخ باید شناسهٔ درخواست داشته باشد (قابلیت ردیابی)."""
    response = client.get("/")
    assert response["X-Request-Id"]
    assert response["X-Response-Time-ms"]


def test_robots_protects_private_paths(client) -> None:  # type: ignore[no-untyped-def]
    """robots.txt باید مسیرهای خصوصی را فهرست کند."""
    response = client.get("/robots.txt")
    assert response.status_code == 200
    text = response.content.decode()
    assert "Disallow: /django-admin/" in text
    assert "Disallow: /api/" in text
    assert "Disallow: /board/" in text


def test_meta_endpoint_shape(client) -> None:  # type: ignore[no-untyped-def]
    """فرادادهٔ سیستم باید محیط، منطقهٔ زمانی و نسخهٔ طرح‌واره را بدهد."""
    response = client.get("/api/v1/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "v1"
    assert body["timezone"] == "Asia/Tehran"


def test_security_headers_present(client) -> None:  # type: ignore[no-untyped-def]
    """هدرهای امنیتی تکمیلی باید در پاسخ باشند."""
    response = client.get("/")
    assert "Permissions-Policy" in response
    assert response["X-Content-Type-Options"] == "nosniff"
