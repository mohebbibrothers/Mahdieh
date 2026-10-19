"""ورود کارکنان با گذرواژه — همان مسیری که مدیر روز اول روی میزبان طی می‌کند.

درس واقعی (گام ۸ استقرار): ``createsuperuser`` تلفن را نرمال می‌کرد (``+98…``)
ولی فرم ورود پنل جنگو متن خام را جست‌وجو می‌کرد؛ ``09…`` «گذرواژه نادرست» می‌گرفت.
این‌جا زنجیرهٔ کامل آزموده می‌شود: ساخت مدیر ← ورود پنل با هر قالب تلفن ←
هدایت اجباری به عامل دوم.
"""

from __future__ import annotations

from collections.abc import Iterator
from io import StringIO

from django.contrib.auth import authenticate
from django.core.management import CommandError, call_command
from django.test import Client, override_settings
import pytest

from apps.audit.choices import AuditAction
from apps.audit.models import AuditRecord
from apps.identity.choices import UserRole
from apps.identity.models import User
from tests.factories import make_user

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


# ── گذرواژهٔ ضعیف برای مدیر کل: هیچ راه دور زدنی نیست ─────────────────────────
# درس واقعی (گام ۸): روی میزبان به پرسش «Bypass password validation…? [y/N]»
# پاسخ Y داده شد و مدیر کل با گذرواژهٔ کوتاه ساخته شد.

WEAK = "short1!"  # pragma: allowlist secret


class _Tty(StringIO):
    """ورودی شبیه پایانه تا ``createsuperuser`` در حالت تعاملی اجرا شود."""

    def isatty(self) -> bool:
        return True


def _feed(monkeypatch: pytest.MonkeyPatch, *, inputs: list[str], secrets: list[str]) -> None:
    typed: Iterator[str] = iter(inputs)
    hidden: Iterator[str] = iter(secrets)
    monkeypatch.setattr("builtins.input", lambda *_a: next(typed))
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: next(hidden))


def test_noinput_superuser_with_weak_password_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DJANGO_SUPERUSER_PHONE", "09121234567")
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", WEAK)
    with pytest.raises(CommandError, match="دور زدن قوانین گذرواژه"):
        call_command("createsuperuser", "--noinput", stdout=StringIO())
    assert not User.objects.exists()


def test_interactive_bypass_answer_yes_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _feed(monkeypatch, inputs=["09121234567", "y"], secrets=[WEAK, WEAK])
    with pytest.raises(CommandError, match="دور زدن قوانین گذرواژه"):
        call_command(
            "createsuperuser", interactive=True, stdin=_Tty(), stdout=StringIO(), stderr=StringIO()
        )
    assert not User.objects.exists()


def test_interactive_superuser_with_strong_password_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _feed(monkeypatch, inputs=["۰۹۱۲۱۲۳۴۵۶۷"], secrets=[PASSWORD, PASSWORD])
    out = StringIO()
    call_command("createsuperuser", interactive=True, stdin=_Tty(), stdout=out, stderr=StringIO())
    assert "Superuser created successfully." in out.getvalue()
    assert User.objects.get().phone == STORED


# ── staff_password: جایگزین امن changepassword ─────────────────────────────────

NEW_PASSWORD = "Qm4$tR8!vWz2Lp"  # pragma: allowlist secret


def test_staff_password_accepts_local_phone_and_audits(
    superuser: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _feed(monkeypatch, inputs=[], secrets=[NEW_PASSWORD, NEW_PASSWORD])
    out = StringIO()
    call_command("staff_password", "09121234567", stdout=out, stderr=StringIO())
    superuser.refresh_from_db()
    assert superuser.check_password(NEW_PASSWORD)
    assert not superuser.check_password(PASSWORD)
    assert "گذرواژه تغییر کرد" in out.getvalue()
    entry = AuditRecord.objects.get(action=AuditAction.PASSWORD_SET)
    assert entry.target_id == str(superuser.pk)
    assert NEW_PASSWORD not in str(entry.__dict__)


def test_staff_password_logs_out_open_sessions(
    superuser: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Client()
    client.force_login(superuser)
    assert client.get("/django-admin/").status_code == 200
    _feed(monkeypatch, inputs=[], secrets=[NEW_PASSWORD, NEW_PASSWORD])
    call_command("staff_password", "+989121234567", stdout=StringIO(), stderr=StringIO())
    after = client.get("/django-admin/")
    assert after.status_code == 302
    assert after["Location"].startswith("/django-admin/login/")


def test_staff_password_never_accepts_a_weak_password(
    superuser: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _feed(monkeypatch, inputs=[], secrets=[WEAK, WEAK, NEW_PASSWORD, "mismatch-x9!Q", WEAK, WEAK])
    err = StringIO()
    with pytest.raises(CommandError, match="پس از 3 تلاش"):
        call_command("staff_password", "09121234567", stdout=StringIO(), stderr=err)
    superuser.refresh_from_db()
    assert superuser.check_password(PASSWORD)
    assert "یکسان نیستند" in err.getvalue()
    assert not AuditRecord.objects.filter(action=AuditAction.PASSWORD_SET).exists()


def test_staff_password_recovers_after_a_typo(
    superuser: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _feed(monkeypatch, inputs=[], secrets=[NEW_PASSWORD, "typo", NEW_PASSWORD, NEW_PASSWORD])
    call_command("staff_password", "09121234567", stdout=StringIO(), stderr=StringIO())
    superuser.refresh_from_db()
    assert superuser.check_password(NEW_PASSWORD)


@pytest.mark.parametrize(
    ("phone", "message"),
    [("09350000000", "پیدا نشد"), ("not-a-phone", "پیدا نشد"), ("pilgrim", "کارمند نیست")],
)
def test_staff_password_refuses_unknown_and_pilgrim_accounts(phone: str, message: str) -> None:
    if phone == "pilgrim":
        phone = make_user().phone
    with pytest.raises(CommandError, match=message):
        call_command("staff_password", phone, stdout=StringIO(), stderr=StringIO())
