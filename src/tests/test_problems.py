"""تست قرارداد خطا — RFC 9457 و نشت‌نداشتن اطلاعات داخلی."""

from __future__ import annotations

import json
import sys
import types

import pytest
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from apps.core.errors import ReservationConflictError, ValidationError

pytestmark = [pytest.mark.django_db, pytest.mark.contract]

PROBLEM_CONTENT_TYPE = "application/problem+json"


@api_view(["GET"])
@permission_classes([AllowAny])
def _raise_domain(_request):  # type: ignore[no-untyped-def]
    """نمای آزمایشی: خطای دامنه پرتاب می‌کند."""
    raise ReservationConflictError(
        "این بازه در همین لحظه گرفته شد.",
        extensions={"alternatives": ["1405/07/24 - عصر", "1405/07/25 - صبح"]},
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def _raise_validation(_request):  # type: ignore[no-untyped-def]
    """نمای آزمایشی: خطای اعتبارسنجی دامنه."""
    raise ValidationError("تعداد نفرات باید عددی مثبت باشد.")


@api_view(["GET"])
@permission_classes([AllowAny])
def _raise_unexpected(_request):  # type: ignore[no-untyped-def]
    """نمای آزمایشی: خطای پیش‌بینی‌نشده (نباید جزئیاتش لو برود)."""
    raise RuntimeError("secret-internal-detail: connection string leaked")


@pytest.fixture
def client_urls(settings):  # type: ignore[no-untyped-def]
    """URLconf آزمایشی مجزا — روش درست، بدون دست‌کاری URLconf پروژه.

    نکته: رزولور جنگو نتیجه را کش می‌کند؛ پس باید کش پاک شود، وگرنه مسیرهای
    آزمایشی دیده نمی‌شوند و تست، نتیجهٔ گمراه‌کننده می‌دهد.
    """
    from django.urls import clear_url_caches, re_path

    module_name = "tests._problem_test_urls"
    module = types.ModuleType(module_name)
    module.urlpatterns = [  # type: ignore[attr-defined]
        re_path(r"^api/v1/test/conflict$", _raise_domain),
        re_path(r"^api/v1/test/validation$", _raise_validation),
        re_path(r"^api/v1/test/unexpected$", _raise_unexpected),
    ]
    sys.modules[module_name] = module
    settings.ROOT_URLCONF = module_name
    clear_url_caches()
    yield
    settings.ROOT_URLCONF = "config.urls"
    clear_url_caches()
    sys.modules.pop(module_name, None)


def test_domain_conflict_returns_problem_details(client, client_urls) -> None:  # type: ignore[no-untyped-def]
    """تصادم رزرو باید با کد اختصاصی و فهرست گزینه‌های جایگزین برگردد."""
    response = client.get("/api/v1/test/conflict")
    assert response.status_code == 409
    assert response["Content-Type"].startswith(PROBLEM_CONTENT_TYPE)

    body = json.loads(response.content)
    assert body["code"] == "reservation_conflict"
    assert body["status"] == 409
    assert body["type"].startswith("https://docs.mahdieh.ir/problems/")
    assert body["alternatives"], "گزینه‌های جایگزین باید به کاربر ارائه شود"
    assert body["trace_id"], "هر خطا باید شناسهٔ ردیابی داشته باشد"


def test_validation_error_uses_422(client, client_urls) -> None:  # type: ignore[no-untyped-def]
    """خطای ورودی نامعتبر با کد ۴۲۲ برگردد."""
    response = client.get("/api/v1/test/validation")
    assert response.status_code == 422
    body = json.loads(response.content)
    assert body["code"] == "validation_error"
    assert "تعداد نفرات" in body["detail"]


def test_unexpected_error_never_leaks_internals(client, client_urls) -> None:  # type: ignore[no-untyped-def]
    """خطای داخلی باید ۵۰۰ بدهد، ولی جزئیات فنی را فاش نکند."""
    response = client.get("/api/v1/test/unexpected")
    assert response.status_code == 500
    body = json.loads(response.content)
    assert body["code"] == "internal_error"
    assert "secret-internal-detail" not in response.content.decode()
    assert "RuntimeError" not in response.content.decode()
