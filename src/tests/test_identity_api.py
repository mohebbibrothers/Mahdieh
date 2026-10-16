"""تست‌های API هویت — ورود با کد یک‌بارمصرف و تکمیل پروفایل.

سناریوهای امنیتی محوری (بخش S8 سند دامنه):
    • پاسخ یکسان برای شمارهٔ ثبت‌شده و ثبت‌نشده (ضد شمارش کاربران).
    • سقف درخواست، فاصلهٔ اجباری، سقف تلاش، و بی‌اعتباری کد پیشین.
    • کد یک‌بارمصرف هرگز در پاسخ، لاج یا دیتابیس به‌صورت روشن نمی‌نشیند.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.db import connection
import pytest

from apps.audit.choices import AuditAction
from apps.audit.models import AuditRecord
from apps.core.outbox import OutboxEvent
from apps.identity.models import OtpChallenge
from apps.notifications.choices import SmsStatus
from apps.notifications.models import SmsDelivery
from apps.notifications.providers import MockSmsProvider
from tests.factories import make_user, make_valid_national_id

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

User = get_user_model()
PHONE = "09121234567"
NORMALIZED = "+989121234567"
CODE_PATTERN = re.compile(r"\b(\d{5})\b")


def _request_otp(api: object, phone: str = PHONE) -> object:
    """درخواست کد و تضمین ارسال آن به صندوق ماک."""
    response = api.post("/api/v1/auth/otp/request", {"phone": phone}, format="json")  # type: ignore[attr-defined]
    assert response.status_code == 202, response.content
    return response


def _last_code(phone: str = NORMALIZED) -> str:
    """خواندن کد یک‌بارمصرف از صندوق ماک پیامک (تنها راه درست در تست)."""
    message = MockSmsProvider.last_for(phone)
    assert message is not None, "پیامک کد ارسال نشده است"
    match = CODE_PATTERN.search(message.body)
    assert match is not None, message.body
    return match.group(1)


def test_otp_request_response_is_identical_for_new_and_existing_phone(api) -> None:
    """پاسخ درخواست کد نباید وجود کاربر را لو بدهد."""
    first = _request_otp(api, "09120000011")
    make_user(phone="09120000012")
    second = _request_otp(api, "09120000012")
    assert set(first.json()) == set(second.json())


def test_otp_request_delivers_code_without_leaking_it(api) -> None:
    """کد به پیامک می‌رود، ولی در پاسخ HTTP و در دیتابیس روشن نیست."""
    response = _request_otp(api)
    code = _last_code()
    assert code not in response.content.decode()
    challenge = OtpChallenge.objects.get(phone=NORMALIZED)
    assert challenge.code_hash != code
    assert len(challenge.code_hash) == 64
    delivery = SmsDelivery.objects.get(template_key="otp_login")
    assert code not in delivery.body_redacted
    assert "••••" in delivery.body_redacted


def test_otp_request_is_rate_limited_by_cooldown(api) -> None:
    """درخواست دوم پیش از پایان فاصلهٔ اجباری رد می‌شود (۴۲۹)."""
    _request_otp(api)
    second = api.post("/api/v1/auth/otp/request", {"phone": PHONE}, format="json")
    assert second.status_code == 429
    assert second.headers["Content-Type"].startswith("application/problem+json")
    assert second.json()["code"] == "rate_limited"


def test_otp_window_cap_blocks_abuse(api, settings) -> None:
    """سقف پنجره‌ای: پس از سه درخواست در ده دقیقه، درخواست تازه رد می‌شود."""
    settings.OTP_RESEND_COOLDOWN_SECONDS = 0
    settings.OTP_MAX_PER_WINDOW = 3
    for _ in range(3):
        _request_otp(api)
    blocked = api.post("/api/v1/auth/otp/request", {"phone": PHONE}, format="json")
    assert blocked.status_code == 429


def test_verify_logs_user_in_and_creates_account(api) -> None:
    """تأیید کد، حساب را می‌سازد، نشست باز می‌کند و رخداد ثبت می‌کند."""
    _request_otp(api)
    response = api.post(
        "/api/v1/auth/otp/verify",
        {"phone": PHONE, "code": _last_code()},
        format="json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["is_new_user"] is True
    assert body["next_step"] == "complete_profile"
    assert body["profile"]["phone_masked"].startswith("+989")
    user = User.objects.get(phone=NORMALIZED)
    assert user.is_phone_verified is True
    assert OutboxEvent.all_objects.filter(event_type="identity.user_registered").exists()
    assert AuditRecord.all_objects.filter(action=AuditAction.LOGIN_SUCCEEDED).exists()


def test_verify_with_wrong_code_consumes_attempts_then_locks(api, settings) -> None:
    """کد نادرست: تلاش مصرف می‌شود، تعداد باقی‌مانده اعلام می‌شود، سپس قفل."""
    settings.OTP_MAX_ATTEMPTS = 5
    settings.OTP_RESEND_COOLDOWN_SECONDS = 0
    _request_otp(api)

    statuses: list[int] = []
    remaining_seen: list[int] = []
    for _ in range(5):
        response = api.post(
            "/api/v1/auth/otp/verify", {"phone": PHONE, "code": "00001"}, format="json"
        )
        statuses.append(response.status_code)
        if response.status_code == 422:
            remaining_seen.append(response.json()["remaining_attempts"])
    assert statuses[:3] == [422, 422, 422]
    assert remaining_seen[:3] == [4, 3, 2]
    assert statuses[-1] == 429
    assert AuditRecord.all_objects.filter(action=AuditAction.LOGIN_FAILED).count() == 5


def test_new_code_invalidates_previous_one(api, settings) -> None:
    """درخواست کد تازه، کد قبلی را بی‌اعتبار می‌کند (جلوگیری از انبار کد)."""
    settings.OTP_RESEND_COOLDOWN_SECONDS = 0
    _request_otp(api)
    first_code = _last_code()
    _request_otp(api)
    second_code = _last_code()
    assert first_code != second_code
    stale = api.post("/api/v1/auth/otp/verify", {"phone": PHONE, "code": first_code}, format="json")
    assert stale.status_code == 422
    fresh = api.post(
        "/api/v1/auth/otp/verify", {"phone": PHONE, "code": second_code}, format="json"
    )
    assert fresh.status_code == 200


def test_verify_without_request_returns_otp_expired(api) -> None:
    """بدون درخواست کد، تأیید با پیام روشن و بدون افشا رد می‌شود."""
    response = api.post("/api/v1/auth/otp/verify", {"phone": PHONE, "code": "12345"}, format="json")
    assert response.status_code == 422
    assert response.json()["code"] == "otp_expired"


def test_profile_completion_opens_booking_and_is_audited(api) -> None:
    """تکمیل پروفایل: دسترسی رزرو باز، کد ملی رمزنگاری و رخداد Outbox."""
    user = make_user(phone=PHONE, complete_profile=False)
    api.force_authenticate(user=user)
    national_id = make_valid_national_id("007984561")

    response = api.patch(
        "/api/v1/me/profile",
        {
            "first_name": "محمد",
            "last_name": "رضایی",
            "province": "khorasan_razavi",
            "city": "مشهد",
            "national_id": national_id,
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["is_profile_complete"] is True
    assert body["national_id_masked"] == f"***{national_id[-4:]}"
    assert "national_id" not in body

    user.refresh_from_db()
    assert user.is_profile_complete
    assert user.reveal_national_id() == national_id
    # اثبات رمزنگاری «در سکون»: مقدار خام ستون در دیتابیس متن روشن نیست.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT national_id_encrypted FROM identity_user WHERE id = %s", [str(user.pk)]
        )
        stored = cursor.fetchone()[0]
    assert stored.startswith("v1:")
    assert national_id not in stored
    assert OutboxEvent.all_objects.filter(event_type="identity.profile_completed").exists()
    assert AuditRecord.all_objects.filter(action=AuditAction.PROFILE_COMPLETED).exists()
    welcome = SmsDelivery.objects.filter(template_key="welcome").first()
    assert welcome is not None
    assert welcome.status in {SmsStatus.SUPPRESSED, SmsStatus.SENT}


def test_profile_rejects_duplicate_national_id(api) -> None:
    """کد ملی تکراری ⇒ رد با کد خطای مشخص (ضد حساب تکراری، ثابت I12)."""
    taken = make_valid_national_id("111222333")
    make_user(phone="09120000021")
    existing = User.objects.get(phone="+989120000021")
    existing.set_national_id(taken)
    existing.save(update_fields=["national_id_encrypted", "national_id_blind"])

    newcomer = make_user(phone="09120000022", complete_profile=False)
    api.force_authenticate(user=newcomer)
    response = api.patch(
        "/api/v1/me/profile",
        {
            "first_name": "حسن",
            "last_name": "مرادی",
            "province": "tehran",
            "city": "تهران",
            "national_id": taken,
        },
        format="json",
    )
    assert response.status_code == 422
    assert response.json()["code"] == "national_id_already_registered"


def test_me_requires_authentication(api) -> None:
    """دسترسی بدون ورود ⇒ ۴۰۱ با بدنهٔ problem+json."""
    response = api.get("/api/v1/me")
    assert response.status_code == 401
    assert response.headers["Content-Type"].startswith("application/problem+json")
    assert response.json()["trace_id"]


def test_logout_closes_session(api) -> None:
    """خروج، نشست را می‌بندد و درخواست بعدی ۴۰۱ می‌گیرد."""
    _request_otp(api)
    api.post("/api/v1/auth/otp/verify", {"phone": PHONE, "code": _last_code()}, format="json")
    assert api.get("/api/v1/me").status_code == 200
    assert api.post("/api/v1/auth/logout").status_code == 204
    assert api.get("/api/v1/me").status_code == 401
