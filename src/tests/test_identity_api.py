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


def test_session_state_is_200_for_guest(api) -> None:
    """مهمان ⇒ ۲۰۰ با authenticated=false (نه ۴۰۱) و بدون کش."""
    response = api.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.json() == {"authenticated": False, "profile": None, "mfa_pending": False}
    assert response.headers["Cache-Control"] == "no-store"


def test_session_state_reports_signed_in_profile(api) -> None:
    """پس از ورود ⇒ پروفایل ایمن همان /me، و پس از خروج دوباره مهمان."""
    _request_otp(api)
    api.post("/api/v1/auth/otp/verify", {"phone": PHONE, "code": _last_code()}, format="json")
    body = api.get("/api/v1/auth/session").json()
    assert body["authenticated"] is True
    assert body["mfa_pending"] is False
    assert body["profile"] == api.get("/api/v1/me").json()
    assert "phone" not in body["profile"]
    api.post("/api/v1/auth/logout")
    assert api.get("/api/v1/auth/session").json()["authenticated"] is False


def test_logout_closes_session(api) -> None:
    """خروج، نشست را می‌بندد و درخواست بعدی ۴۰۱ می‌گیرد."""
    _request_otp(api)
    api.post("/api/v1/auth/otp/verify", {"phone": PHONE, "code": _last_code()}, format="json")
    assert api.get("/api/v1/me").status_code == 200
    assert api.post("/api/v1/auth/logout").status_code == 204
    assert api.get("/api/v1/me").status_code == 401


# ── ویرایش پروفایل کامل: مدرک هویتی ثبت‌شده بی بازنویسی نگه داشته می‌شود ──────────────


def _edit(api: object, **fields: str) -> object:
    """PATCH پروفایل با پیش‌فرض‌های یک زائر ایرانی."""
    body = {
        "first_name": "زهرا",
        "last_name": "محمدی",
        "province": "khorasan_razavi",
        "city": "مشهد",
        "national_id": "",
        **fields,
    }
    return api.patch("/api/v1/me/profile", body, format="json")  # type: ignore[attr-defined]


def test_profile_edit_with_blank_national_id_keeps_the_stored_one(api) -> None:
    """ویرایش نام/شهر بی کد ملی ⇒ ۲۰۰؛ کد ملی رمزنگاری‌شده همان می‌ماند و ممیزی «نگه‌داشته» می‌گوید."""
    user = make_user(phone=PHONE)
    stored = user.reveal_national_id()
    api.force_authenticate(user=user)

    response = _edit(api, city="نیشابور", last_name="محمدی‌نیا")
    assert response.status_code == 200, response.content
    assert response.json()["national_id_masked"] == f"***{stored[-4:]}"

    user.refresh_from_db()
    assert user.city == "نیشابور"
    assert user.last_name == "محمدی‌نیا"
    assert user.reveal_national_id() == stored
    audit = AuditRecord.all_objects.filter(action=AuditAction.PROFILE_COMPLETED).latest(
        "created_at"
    )
    assert audit.metadata["document_kept"] is True


def test_profile_edit_with_new_national_id_replaces_it(api) -> None:
    """کد ملی تازه در ویرایش ⇒ جایگزین (با همان سنجش رقم کنترل و یکتایی)."""
    user = make_user(phone=PHONE)
    api.force_authenticate(user=user)
    fresh = make_valid_national_id("246813579")

    response = _edit(api, national_id=fresh)
    assert response.status_code == 200, response.content
    user.refresh_from_db()
    assert user.reveal_national_id() == fresh


def test_first_completion_still_requires_national_id(api) -> None:
    """تکمیل نخستین بی کد ملی ⇒ ۴۲۲ روی همان فیلد (نگه‌داشتن فقط برای پروفایل کامل است)."""
    api.force_authenticate(user=make_user(phone=PHONE, complete_profile=False))
    response = _edit(api)
    assert response.status_code == 422
    assert "national_id" in response.json()["errors"]


def test_switching_to_foreign_requires_a_passport(api) -> None:
    """تغییر نوع تابعیت ⇒ مدرک تازه لازم است؛ کد ملی پیشین به‌جای گذرنامه پذیرفته نمی‌شود."""
    api.force_authenticate(user=make_user(phone=PHONE))
    response = _edit(api, nationality="AF", first_name="Ahmad", last_name="Karimi")
    assert response.status_code == 422
    assert "passport_number" in response.json()["errors"]


def test_foreign_edit_keeps_passport_only_for_the_same_nationality(api) -> None:
    """گذرنامه به ملیت گره خورده است: همان ملیت ⇒ نگه‌داشتن؛ ملیت دیگر ⇒ گذرنامهٔ تازه."""
    user = make_user(phone=PHONE, complete_profile=False)
    api.force_authenticate(user=user)
    first = _edit(
        api, nationality="AF", first_name="Ahmad", last_name="Karimi", passport_number="P1234567"
    )
    assert first.status_code == 200, first.content

    same = _edit(api, nationality="AF", first_name="Ahmad", last_name="Karimi", city="Herat")
    assert same.status_code == 200, same.content
    user.refresh_from_db()
    assert user.reveal_national_id() == "P1234567"
    assert user.city == "Herat"

    other = _edit(api, nationality="IQ", first_name="Ahmad", last_name="Karimi", city="Herat")
    assert other.status_code == 422
    assert "passport_number" in other.json()["errors"]


def test_security_headers_allow_camera_only_for_same_origin(client) -> None:  # type: ignore[no-untyped-def]
    """اسکن QR درون‌سایتی ⇒ دوربین فقط برای همین مبدأ؛ میکروفون/موقعیت/پرداخت بسته می‌مانند."""
    policy = client.get("/api/v1/health").get("Permissions-Policy", "")
    assert "camera=(self)" in policy
    for closed in ("geolocation=()", "microphone=()", "payment=()"):
        assert closed in policy
