"""فیکسچرهای مشترک تست — انزوا، عدم‌حالت مخفی، و دادهٔ پایهٔ آماده."""

from __future__ import annotations

from collections.abc import Iterator

from django.core.cache import cache
from django.test import Client
import pytest
from rest_framework.test import APIClient

from apps.identity.choices import UserRole
from apps.identity.models import User
from apps.inventory.models import Unit
from apps.notifications.providers import MockSmsProvider
from tests.factories import make_floor, make_staff, make_unit, make_user


@pytest.fixture(autouse=True)
def _isolate_side_effects() -> Iterator[None]:
    """پاک‌سازی صندوق پیامک و کش پیش و پس از هر تست.

    دلیل: کش لوکال‌مموری و صندوق ماک در فرآیند تست باقی می‌مانند؛ بدون این
    پاک‌سازی، تست‌های تقویم و OTP می‌توانند به‌طور موذیانه به هم وابسته شوند.
    """
    MockSmsProvider.reset()
    cache.clear()
    yield
    MockSmsProvider.reset()
    cache.clear()


@pytest.fixture
def client() -> Client:
    """کلاینت تست جنگو (برای نماهای SSR)."""
    return Client()


@pytest.fixture
def api() -> APIClient:
    """کلاینت DRF برای تست قرارداد API."""
    return APIClient()


@pytest.fixture
def guest() -> User:
    """زائر با پروفایل کامل — آمادهٔ رزرو."""
    return make_user(phone="09120000001")


@pytest.fixture
def other_guest() -> User:
    """زائر دوم — برای تست‌های جداسازی دسترسی."""
    return make_user(phone="09120000002")


@pytest.fixture
def operator() -> User:
    """کارمند پذیرش."""
    return make_staff(phone="09120000003", role=UserRole.OPERATOR)


@pytest.fixture
def manager() -> User:
    """مدیر زائرسرا."""
    return make_staff(phone="09120000004", role=UserRole.MANAGER)


@pytest.fixture
def units() -> dict[str, Unit]:
    """ساختار واقعی زائرسرا: ۳ طبقه و ۵ واحد."""
    floor1 = make_floor(1)
    floor2 = make_floor(2)
    floor3 = make_floor(3)
    return {
        "A1": make_unit("A1", floor=floor1, title="سوئیت طبقهٔ اول"),
        "B1": make_unit("B1", floor=floor1, title="اتاق بزرگ طبقهٔ اول"),
        "A2": make_unit("A2", floor=floor2, title="سوئیت طبقهٔ دوم"),
        "B2": make_unit("B2", floor=floor2, title="اتاق بزرگ طبقهٔ دوم"),
        "B3": make_unit("B3", floor=floor3, title="اتاق بزرگ طبقهٔ سوم"),
    }
