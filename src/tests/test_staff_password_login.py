"""ورود کارکنان با گذرواژه — همان مسیری که مدیر روز اول روی میزبان طی می‌کند.

درس واقعی (گام ۸ استقرار): ``createsuperuser`` تلفن را نرمال می‌کرد (``+98…``)
ولی فرم ورود پنل جنگو متن خام را جست‌وجو می‌کرد؛ ``09…`` «گذرواژه نادرست» می‌گرفت.
این‌جا زنجیرهٔ کامل آزموده می‌شود: ساخت مدیر ← ورود پنل با هر قالب تلفن ←
هدایت اجباری به عامل دوم.
"""

from __future__ import annotations

from io import StringIO

from django.contrib.auth import authenticate
from django.core.management import call_command
from django.test import Client, override_settings
import pytest

from apps.identity.choices import UserRole
from apps.identity.models import User

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

PASSWORD = "Zx9!kQ2#mLp7Rv"  # pragma: allowlist secret
WRONG_PASSWORD = "wrong-password-123"  # pragma: allowlist secret
STORED = "+989121234567"


@pytest.fixture
def superuser(monkeypatch: pytest.MonkeyPatch) -> User:
    """ساخت مدیر دقیقاً با همان دستوری که روی میزبان اجرا می‌شود."""
    monkeypatch.setenv("DJANGO_SUPERUSER_PHONE", "09121234567")
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", PASSWORD)
    call_command("createsuperuser", "--noinput", stdout=StringIO())
    return User.objects.get()


def test_createsuperuser_builds_a_full_admin(superuser: User) -> None:
    assert superuser.phone == STORED
    assert superuser.role == UserRole.ADMIN
    assert superuser.is_staff
    assert superuser.is_superuser
    assert superuser.profile_completed_at is not None
    assert superuser.check_password(PASSWORD)


@pytest.mark.parametrize(
    "typed",
    [
        "09121234567",
        "9121234567",
        "+989121234567",
        "00989121234567",
        "۰۹۱۲ ۱۲۳ ۴۵۶۷",
        "0912-123-4567",
    ],
)
def test_password_login_accepts_every_phone_spelling(superuser: User, typed: str) -> None:
    assert authenticate(username=typed, password=PASSWORD) == superuser


@pytest.mark.parametrize("typed", ["", "not-a-phone", "0912", "09121234568"])
def test_password_login_rejects_unknown_or_malformed_phones(superuser: User, typed: str) -> None:
    del superuser
    assert authenticate(username=typed, password=PASSWORD) is None


def test_password_login_still_needs_the_right_password(superuser: User) -> None:
    del superuser
    assert authenticate(username="09121234567", password=WRONG_PASSWORD) is None


def test_createsuperuser_refuses_a_duplicate_in_another_spelling(
    superuser: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    del superuser
    monkeypatch.setenv("DJANGO_SUPERUSER_PHONE", "+98 912 123 4567")
    with pytest.raises(Exception, match=r"(?i)already|وجود|قبلا|تکراری"):
        call_command("createsuperuser", "--noinput", stdout=StringIO(), stderr=StringIO())
    assert User.objects.count() == 1


@override_settings(STAFF_MFA_REQUIRED=True)
def test_admin_login_with_local_phone_then_forced_to_enrol_mfa(superuser: User) -> None:
    del superuser
    client = Client()
    login = client.post(
        "/django-admin/login/",
        {"username": "09121234567", "password": PASSWORD, "next": "/django-admin/"},
    )
    assert login.status_code == 302, login.content.decode()[:400]
    assert login["Location"] == "/django-admin/"

    admin = client.get("/django-admin/")
    assert admin.status_code == 302
    assert admin["Location"].startswith("/board/mfa/")
    assert "next=/django-admin/" in admin["Location"]

    enrol = client.get(admin["Location"])
    assert enrol.status_code == 200
