"""تأیید دومرحله‌ای کارکنان (ADR-0018) — از بردارهای RFC تا سد میان‌افزار.

همهٔ تست‌های این فایل با ``STAFF_MFA_REQUIRED=True`` اجرا می‌شوند (تنظیمات تست
پیش‌فرض آن را خاموش نگه می‌دارد تا بقیهٔ مجموعه درگیر عامل دوم نشود). ورود با
``force_login`` انجام می‌شود تا نشست واقعی ساخته شود و میان‌افزار واقعاً اجرا گردد.
"""

from __future__ import annotations

from io import StringIO
import time
from typing import Any

from django.core.management import CommandError, call_command
from django.test import override_settings
import pytest
from rest_framework.test import APIClient
import time_machine

from apps.audit.models import AuditRecord
from apps.identity import totp
from apps.identity.models import StaffRecoveryCode, StaffTotpDevice, UserSession
from tests.factories import days_ahead, make_staff, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_mfa_on")]

RFC_KEY = b"12345678901234567890"
RFC_VECTORS = (
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
)


@pytest.fixture
def _mfa_on() -> Any:
    """روشن‌کردن سد عامل دوم برای همین فایل."""
    with override_settings(STAFF_MFA_REQUIRED=True, STAFF_MFA_STEP_UP_SECONDS=600):
        yield


def _logged_in(user: Any) -> APIClient:
    """کلاینت با نشست واقعی (نه force_authenticate) تا میان‌افزار اجرا شود."""
    client = APIClient()
    client.force_login(user)
    return client


def _enroll(client: APIClient) -> tuple[str, list[str]]:
    """ثبت کامل عامل دوم از راه API؛ بازگشت راز و کدهای بازیابی."""
    setup = client.post("/api/v1/auth/mfa/totp/setup")
    assert setup.status_code == 200, setup.content
    secret = setup.json()["secret"]
    confirm = client.post(
        "/api/v1/auth/mfa/totp/confirm",
        {"code": totp.totp_at(secret, time.time())},
        format="json",
    )
    assert confirm.status_code == 200, confirm.content
    return secret, confirm.json()["recovery_codes"]


def test_hotp_matches_rfc6238_sha1_vectors() -> None:
    """پیاده‌سازی stdlib با بردارهای رسمی RFC 6238 (۸ رقمی، SHA-1) یکی است."""
    for unix_seconds, expected in RFC_VECTORS:
        step = totp.time_step(unix_seconds)
        assert totp.hotp(RFC_KEY, step, digits=8) == expected


def test_unverified_staff_session_is_fenced_off_api_and_board() -> None:
    """کارمند واردشده ولی تأییدنشده: API کارکنان ۴۰۳ و تابلو ⇒ صفحهٔ عامل دوم."""
    operator = make_staff("09120000301")
    client = _logged_in(operator)

    blocked = client.get("/api/v1/staff/reservations")
    assert blocked.status_code == 403
    body = blocked.json()
    assert body["code"] == "mfa_required"
    assert body["mfa"]["enrolled"] is False

    # مسیرهای ورود/وضعیت باز می‌مانند تا کاربر بتواند ثبت کند.
    assert client.get("/api/v1/auth/mfa/status").status_code == 200
    assert client.get("/api/v1/me").status_code == 200

    page = client.get("/board/")
    assert page.status_code == 302
    assert page["Location"].startswith("/board/mfa/")


def test_guests_are_never_asked_for_a_second_factor(guest) -> None:
    """زائر مشمول عامل دوم نیست؛ مسیرهای خودش آزاد است."""
    client = _logged_in(guest)
    assert client.get("/api/v1/reservations").status_code == 200
    status = client.get("/api/v1/auth/mfa/status").json()
    assert status["required"] is False


def test_enroll_confirm_then_staff_api_opens_and_audit_is_written() -> None:
    """ثبت ⇒ ۱۰ کد بازیابی یک‌بار مصرف، نشست تأییدشده و رویداد حسابرسی."""
    operator = make_staff("09120000302")
    client = _logged_in(operator)
    _secret, codes = _enroll(client)

    assert len(codes) == 10
    assert len(set(codes)) == 10
    assert StaffRecoveryCode.objects.filter(user=operator, used_at__isnull=True).count() == 10
    assert StaffTotpDevice.objects.get(user=operator).confirmed_at is not None
    assert client.get("/api/v1/staff/reservations").status_code == 200
    assert AuditRecord.objects.filter(
        action="auth.mfa_enrolled", target_id=str(operator.pk)
    ).exists()

    # ثبت دوباره روی دستگاه فعال، بی‌صدا جایگزین نمی‌شود.
    again = client.post("/api/v1/auth/mfa/totp/setup")
    assert again.status_code == 409
    assert again.json()["code"] == "mfa_already_enrolled"


def test_new_session_must_verify_and_codes_cannot_be_replayed() -> None:
    """نشست تازه ⇒ کد لازم؛ هر کد فقط یک بار (ضد بازپخش)."""
    operator = make_staff("09120000303")
    secret, _codes = _enroll(_logged_in(operator))

    second = _logged_in(operator)
    assert second.get("/api/v1/staff/reservations").status_code == 403

    later = time.time() + totp.PERIOD_SECONDS
    with time_machine.travel(later, tick=False):
        code = totp.totp_at(secret, later)
        ok = second.post("/api/v1/auth/mfa/verify", {"code": code}, format="json")
        assert ok.status_code == 200, ok.content
        assert ok.json()["method"] == "totp"
        assert second.get("/api/v1/staff/reservations").status_code == 200

        third = _logged_in(operator)
        replay = third.post("/api/v1/auth/mfa/verify", {"code": code}, format="json")
        assert replay.status_code == 422
        assert replay.json()["code"] == "invalid_mfa_code"


def test_recovery_code_works_once() -> None:
    """کد بازیابی یک بار کار می‌کند و بار دوم رد می‌شود."""
    operator = make_staff("09120000304")
    _secret, codes = _enroll(_logged_in(operator))

    first = _logged_in(operator)
    used = first.post("/api/v1/auth/mfa/verify", {"code": codes[0].lower()}, format="json")
    assert used.status_code == 200
    assert used.json()["method"] == "recovery"
    assert used.json()["status"]["recovery_codes_remaining"] == 9

    second = _logged_in(operator)
    reused = second.post("/api/v1/auth/mfa/verify", {"code": codes[0]}, format="json")
    assert reused.status_code == 422


def test_five_wrong_codes_lock_the_factor() -> None:
    """پنج کد نادرست ⇒ قفل موقت با ``retry_after``؛ حتی کد درست هم پذیرفته نمی‌شود."""
    operator = make_staff("09120000305")
    secret, _codes = _enroll(_logged_in(operator))
    client = _logged_in(operator)
    for _ in range(5):
        wrong = client.post("/api/v1/auth/mfa/verify", {"code": "000000"}, format="json")
        assert wrong.status_code == 422
        assert "code" in wrong.json()["errors"]
    locked = client.post(
        "/api/v1/auth/mfa/verify",
        {"code": totp.totp_at(secret, time.time() + totp.PERIOD_SECONDS)},
        format="json",
    )
    assert locked.status_code == 429
    assert locked.json()["code"] == "mfa_locked"
    assert (
        AuditRecord.objects.filter(action="auth.mfa_failed", target_id=str(operator.pk)).count()
        >= 5
    )


def test_policy_change_requires_fresh_verification() -> None:
    """تغییر سیاست: پس از پایان پنجرهٔ تازگی ⇒ ``step_up_required``؛ پس از کد تازه ⇒ مجاز."""
    manager = make_staff("09120000306", role="manager")
    client = _logged_in(manager)
    secret, _codes = _enroll(client)
    body = {"value": 6, "reason": "آزمون تأیید تازه"}

    stale = time.time() + 601
    with time_machine.travel(stale, tick=False):
        refused = client.post("/api/v1/staff/policies/reservation.max_cells", body, format="json")
        assert refused.status_code == 403
        assert refused.json()["code"] == "step_up_required"

        verify = client.post(
            "/api/v1/auth/mfa/verify", {"code": totp.totp_at(secret, stale)}, format="json"
        )
        assert verify.status_code == 200
        allowed = client.post("/api/v1/staff/policies/reservation.max_cells", body, format="json")
        assert allowed.status_code == 200, allowed.content


def test_sessions_list_revoke_and_logout_everywhere(guest) -> None:
    """فهرست نشست‌ها، ابطال نشست دیگر و «خروج از همه‌جا»."""
    phone_a = _logged_in(guest)
    phone_b = _logged_in(guest)

    listed = phone_a.get("/api/v1/me/sessions").json()["results"]
    assert len(listed) == 2
    current = [item for item in listed if item["is_current"]]
    other = [item for item in listed if not item["is_current"]]
    assert len(current) == 1
    assert len(other) == 1

    # نشست جاری را نمی‌توان از این مسیر باطل کرد.
    own = phone_a.post(f"/api/v1/me/sessions/{current[0]['id']}/revoke")
    assert own.status_code == 404

    revoked = phone_a.post(f"/api/v1/me/sessions/{other[0]['id']}/revoke")
    assert revoked.status_code == 204
    assert phone_b.get("/api/v1/reservations").status_code in {401, 403}

    phone_c = _logged_in(guest)
    out = phone_a.post("/api/v1/auth/logout-all")
    assert out.status_code == 204
    assert phone_c.get("/api/v1/reservations").status_code in {401, 403}
    assert phone_a.get("/api/v1/reservations").status_code in {401, 403}
    assert not UserSession.objects.filter(user=guest, revoked_at__isnull=True).exists()


def test_csrf_endpoint_sets_cookie_and_is_not_cached() -> None:
    """``auth/csrf``: کوکی CSRF + نام هدر، بدون کش."""
    response = APIClient().get("/api/v1/auth/csrf")
    assert response.status_code == 200
    payload = response.json()
    assert payload["header_name"] == "X-CSRFToken"
    assert payload["csrf_token"]
    assert "csrftoken" in response.cookies or payload["cookie_name"] in response.cookies
    assert "no-store" in response["Cache-Control"]


def test_mfa_reset_command_requires_reason_and_is_audited() -> None:
    """بازنشانی فقط از خط فرمان، با دلیل، و در حسابرسی."""
    operator = make_staff("09120000307")
    _enroll(_logged_in(operator))

    with pytest.raises(CommandError):
        call_command("mfa_reset", "--phone", "09120000307", "--reason", "کم")
    with pytest.raises(CommandError):
        call_command("mfa_reset", "--phone", "09129999999", "--reason", "گوشی گم شد")
    guest = make_user(phone="09120000308")
    with pytest.raises(CommandError):
        call_command("mfa_reset", "--phone", guest.phone, "--reason", "گوشی گم شد")

    out = StringIO()
    call_command("mfa_reset", "--phone", "09120000307", "--reason", "گوشی کارمند گم شد", stdout=out)
    assert "removed" in out.getvalue()
    assert not StaffTotpDevice.objects.filter(user=operator).exists()
    assert not StaffRecoveryCode.objects.filter(user=operator).exists()
    record = AuditRecord.objects.filter(action="auth.mfa_reset", target_id=str(operator.pk)).get()
    assert record.reason == "گوشی کارمند گم شد"


def test_sensitive_reads_require_fresh_verification(guest, units) -> None:
    """نمایش شمارهٔ کامل و اکسل با شمارهٔ کامل: پنجرهٔ کهنه ⇒ ``step_up_required``."""
    manager = make_staff("09120000307", role="manager")
    client = _logged_in(manager)
    secret, _codes = _enroll(client)
    reveal_url = f"/api/v1/staff/guests/{guest.pk}/reveal-phone"
    span = f"from={days_ahead(0).isoformat()}&to={days_ahead(3).isoformat()}"
    excel_url = f"/api/v1/staff/reports/reservations.xlsx?{span}&phones=full"

    fresh = client.post(reveal_url, {"purpose": "call_guest"}, format="json")
    assert fresh.status_code == 200, fresh.content

    stale = time.time() + 601
    with time_machine.travel(stale, tick=False):
        refused = client.post(reveal_url, {"purpose": "call_guest"}, format="json")
        assert refused.status_code == 403
        assert refused.json()["code"] == "step_up_required"
        excel_refused = client.get(excel_url)
        assert excel_refused.status_code == 403
        assert excel_refused.json()["code"] == "step_up_required"
        masked_still_ok = client.get(excel_url.replace("phones=full", "phones=masked"))
        assert masked_still_ok.status_code == 200

        client.post("/api/v1/auth/mfa/verify", {"code": totp.totp_at(secret, stale)}, format="json")
        assert client.get(excel_url)["X-Report-Phones"] == "full"
    bad_purpose = client.post(reveal_url, {"purpose": "curiosity"}, format="json")
    assert bad_purpose.status_code == 422
