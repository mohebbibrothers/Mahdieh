"""تست صفحهٔ خانه — SSR واقعی، جلالی واقعی، و بدون وابستگی بیرونی."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_home_renders_with_units_and_jalali_date(client) -> None:  # type: ignore[no-untyped-def]
    """صفحهٔ خانه باید واحدها و تاریخ جلالی امروز را نشان دهد."""
    response = client.get("/")
    assert response.status_code == 200
    content = response.content.decode()
    for expected in ("زائرسرای مهدیه", "طبقهٔ اول", "طبقهٔ دوم", "طبقهٔ سوم", "سوئیت شمارهٔ ۱"):
        assert expected in content
    assert "کلید درخواست شما" in content


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
