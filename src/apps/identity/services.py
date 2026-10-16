"""سرویس‌های هویت — ورود با کد یک‌بارمصرف و تکمیل پروفایل.

این ماژول **تنها** مسیر مجاز برای تغییر وضعیت هویت است؛ نماها (views) هیچ
نوشتنی انجام نمی‌دهند. دلایل طراحی:

    • **ضد شمارش کاربران**: پاسخ درخواست کد برای شمارهٔ ثبت‌شده و ثبت‌نشده یکسان
      است؛ تفاوت فقط در ارسال پیامک (که مهاجم نمی‌بیند).
    • **ضد حملهٔ S8**: سقف پنجره‌ای، فاصلهٔ اجباری بین درخواست‌ها، سقف تلاش روی
      هر کد، و بی‌اعتبارسازی کدهای قبلی در هر درخواست جدید.
    • **هرگز راز در پاسخ/لاگ**: کد فقط در پیامک می‌رود؛ نه در پاسخ HTTP، نه در
      رکورد حسابرسی، نه در پیام خطا.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hmac
import logging
import math
import secrets
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.audit.choices import AuditAction, AuditOutcome
from apps.audit.services import AuditEntry, record as audit_record
from apps.core import outbox
from apps.core.crypto import blind_index
from apps.core.errors import RateLimitedError, ValidationError as DomainValidationError
from apps.core.middleware import client_ip
from apps.identity.choices import OtpPurpose, Province, UserRole
from apps.identity.models import NATIONAL_ID_NAMESPACE, OTP_CODE_NAMESPACE, OtpChallenge, User
from apps.identity.validators import (
    CITY_MAX_LENGTH,
    mask_phone,
    normalize_phone,
    validate_national_id,
    validate_person_name,
)
from apps.notifications.choices import SmsTemplateKey
from apps.notifications.services import send_sms

logger = logging.getLogger(__name__)

CODE_MIN = 10_000
CODE_SPAN = 90_000
CODE_LENGTH = 5
SECONDS_PER_MINUTE = 60
OTP_WINDOW_KEY = "otp:window:{phone}"
OTP_COOLDOWN_KEY = "otp:cooldown:{phone}"
OTP_VERIFY_FAIL_KEY = "otp:verify_fail:{phone}"


@dataclass(frozen=True, slots=True)
class OtpRequestResult:
    """نتیجهٔ درخواست کد — حداقلی و بدون افشای وجود کاربر."""

    expires_in: int
    resend_after: int
    delivery_status: str


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """نتیجهٔ تأیید موفق کد."""

    user: User
    is_new_user: bool


def _setting_int(name: str, default: int) -> int:
    """خواندن یک تنظیم عددی با پیش‌فرض ایمن."""
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):  # pragma: no cover - پیکربندی معیوب
        return default


def otp_ttl_seconds() -> int:
    """مدت اعتبار کد (ثانیه) — قابل تنظیم در محیط."""
    return _setting_int("OTP_TTL_SECONDS", 120)


def otp_resend_cooldown_seconds() -> int:
    """فاصلهٔ اجباری بین دو درخواست کد برای یک شماره (ثانیه)."""
    return _setting_int("OTP_RESEND_COOLDOWN_SECONDS", 60)


def otp_max_attempts() -> int:
    """سقف تلاش ناموفق روی یک کد."""
    return _setting_int("OTP_MAX_ATTEMPTS", 5)


def _generate_code() -> str:
    """تولید کد یک‌بارمصرف با CSPRNG (هرگز ``random``)."""
    return str(secrets.randbelow(CODE_SPAN) + CODE_MIN)


def _hash_code(code: str) -> str:
    """هش HMAC کد — مقدار ذخیرهشده در دیتابیس، بازگردانی‌پذیر نیست."""
    return blind_index(code, namespace=OTP_CODE_NAMESPACE)


def _normalize_or_fail(phone: str) -> str:
    """نرمال‌سازی شماره یا خطای دامنهٔ ۴۲۲ با پیام فارسی."""
    try:
        return normalize_phone(phone)
    except DjangoValidationError as exc:
        raise DomainValidationError(
            "شمارهٔ تلفن همراه معتبر نیست. مثال درست: ۰۹۱۲۳۴۵۶۷۸۹",
            code="invalid_phone",
        ) from exc


def _enforce_rate_limits(phone: str) -> None:
    """اعمال سقف پنجره‌ای و فاصلهٔ اجباری (ضد حملهٔ پیامکی)."""
    window_seconds = _setting_int("OTP_WINDOW_SECONDS", 600)
    max_per_window = _setting_int("OTP_MAX_PER_WINDOW", 3)
    window_key = OTP_WINDOW_KEY.format(phone=phone)
    used = int(cache.get(window_key) or 0)
    if used >= max_per_window:
        raise RateLimitedError(
            "تعداد درخواست کد زیاد بوده است. کمی بعد دوباره تلاش کنید.",
            extensions={"retry_after": window_seconds},
        )
    cooldown_key = OTP_COOLDOWN_KEY.format(phone=phone)
    if not cache.add(cooldown_key, True, timeout=otp_resend_cooldown_seconds()):
        raise RateLimitedError(
            "کد قبلی هنوز معتبر است؛ برای دریافت کد تازه کمی صبر کنید.",
            extensions={"retry_after": otp_resend_cooldown_seconds()},
        )
    cache.set(window_key, used + 1, window_seconds)


def _active_challenge(phone: str, purpose: str) -> OtpChallenge | None:
    """جدیدترین چالش قابل‌استفاده برای یک شماره و کاربرد."""
    return (
        OtpChallenge.objects.filter(
            phone=phone,
            purpose=purpose,
            consumed_at__isnull=True,
            expires_at__gt=timezone.now(),
            attempts__lt=otp_max_attempts(),
        )
        .order_by("-created_at")
        .first()
    )


def request_otp(
    *,
    phone: str,
    purpose: str = OtpPurpose.LOGIN,
    request: HttpRequest | None = None,
) -> OtpRequestResult:
    """درخواست کد ورود و ارسال آن با پیامک.

    پاسخ برای همهٔ شماره‌ها یکسان است — تفاوت تنها در صندوق پیامک کاربر.
    """
    normalized = _normalize_or_fail(phone)
    _enforce_rate_limits(normalized)

    ttl = otp_ttl_seconds()
    code = _generate_code()
    now = timezone.now()

    # کدهای قبلی این شماره بی‌اعتبار می‌شوند: فقط آخرین کد معتبر است.
    OtpChallenge.objects.filter(phone=normalized, purpose=purpose, consumed_at__isnull=True).update(
        consumed_at=now
    )

    challenge = OtpChallenge.objects.create(
        phone=normalized,
        code_hash=_hash_code(code),
        purpose=purpose,
        max_attempts=otp_max_attempts(),
        expires_at=now + timedelta(seconds=ttl),
        request_ip=client_ip(request) if request is not None else None,
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:300] if request else ""),
        created_at=now,
    )

    delivery = send_sms(
        phone=normalized,
        template_key=SmsTemplateKey.OTP_LOGIN,
        context={"code": code, "ttl_minutes": max(1, math.ceil(ttl / SECONDS_PER_MINUTE))},
        dedup_key=f"otp:{challenge.id}",
    )

    return OtpRequestResult(
        expires_in=ttl,
        resend_after=otp_resend_cooldown_seconds(),
        delivery_status=str(delivery.status),
    )


def verify_otp(
    *,
    phone: str,
    code: str,
    purpose: str = OtpPurpose.LOGIN,
    request: HttpRequest | None = None,
) -> VerifiedIdentity:
    """تأیید کد یک‌بارمصرف و ورود کاربر (با ثبت/بازیابی حساب در همین لحظه)."""
    normalized = _normalize_or_fail(phone)
    challenge = _active_challenge(normalized, purpose)
    if challenge is None:
        raise DomainValidationError(
            "کد وارد‌شده معتبر نیست یا منقضی شده است. یک کد تازه بگیرید.",
            code="otp_expired",
        )

    candidate = (code or "").strip()
    if not hmac.compare_digest(challenge.code_hash, _hash_code(candidate)):
        remaining = challenge.register_failure()
        audit_record(
            AuditEntry(
                action=AuditAction.LOGIN_FAILED,
                outcome=AuditOutcome.FAILURE,
                actor_label=mask_phone(normalized),
                target_type="otp_challenge",
                target_id=str(challenge.id),
                metadata={"remaining_attempts": remaining, "purpose": purpose},
                request=request,
            )
        )
        if remaining <= 0:
            raise RateLimitedError(
                "تعداد تلاش‌های ناموفق زیاد بود. برای امنیت حساب، یک کد تازه بگیرید.",
                extensions={"retry_after": otp_resend_cooldown_seconds()},
            )
        raise DomainValidationError(
            "کد وارد‌شده درست نیست.",
            code="otp_mismatch",
            extensions={"remaining_attempts": remaining},
        )

    ip = client_ip(request) if request is not None else None
    with transaction.atomic():
        challenge.consume()
        user, created = User.objects.get_or_create(
            phone=normalized,
            defaults={"is_phone_verified": True, "last_login_ip": ip},
        )
        if not created:
            user.is_phone_verified = True
            if ip:
                user.last_login_ip = ip
            if user.deleted_at is not None:
                user.deleted_at = None
                logger.info("identity_soft_deleted_user_restored", extra={"user_id": str(user.id)})
            user.save(update_fields=["is_phone_verified", "last_login_ip", "deleted_at"])

        if created:
            outbox.publish(
                "identity.user_registered",
                aggregate_type="user",
                aggregate_id=user.id,
                payload={"user_id": str(user.id), "phone_masked": user.masked_phone},
            )
        outbox.publish(
            "identity.login_succeeded",
            aggregate_type="user",
            aggregate_id=user.id,
            payload={"user_id": str(user.id), "is_new_user": created},
        )

    audit_record(
        AuditEntry(
            action=AuditAction.LOGIN_SUCCEEDED,
            actor_id=user.id,
            actor_label=mask_phone(normalized),
            target_type="user",
            target_id=str(user.id),
            metadata={"is_new_user": created, "role": user.role},
            request=request,
        )
    )
    return VerifiedIdentity(user=user, is_new_user=created)


def complete_profile(
    *,
    user: User,
    first_name: str,
    last_name: str,
    province: str,
    city: str,
    national_id: str,
    request: HttpRequest | None = None,
) -> User:
    """تکمیل پروفایل و باز کردن دسترسی رزرو (ثابت‌های I5 و I12)."""
    try:
        clean_first = validate_person_name(first_name, field="نام")
        clean_last = validate_person_name(last_name, field="نام خانوادگی")
        clean_national_id = validate_national_id(national_id)
    except DjangoValidationError as exc:
        raise DomainValidationError(
            "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc),
            code="invalid_profile",
        ) from exc

    clean_city = (city or "").strip()
    if not 2 <= len(clean_city) <= CITY_MAX_LENGTH:  # noqa: PLR2004 - کرانهٔ دادهٔ ورودی
        raise DomainValidationError("نام شهر باید بین ۲ تا ۶۰ نویسه باشد.", code="invalid_city")
    if province not in Province.values:
        raise DomainValidationError(
            "استان انتخاب‌شده در فهرست استان‌های کشور نیست.", code="invalid_province"
        )

    blind = blind_index(clean_national_id, namespace=NATIONAL_ID_NAMESPACE)
    duplicate = (
        User.objects.filter(national_id_blind=blind, deleted_at__isnull=True)
        .exclude(pk=user.pk)
        .exists()
    )
    if duplicate:
        raise DomainValidationError(
            "این کد ملی قبلاً در سامانه ثبت شده است. اگر حساب قبلی دارید، با همان "
            "شمارهٔ تلفن وارد شوید.",
            code="national_id_already_registered",
        )

    with transaction.atomic():
        try:
            user.complete_profile(
                first_name=clean_first,
                last_name=clean_last,
                province=province,
                city=clean_city,
                national_id=clean_national_id,
            )
        except IntegrityError as exc:  # مسابقهٔ هم‌زمانی روی یکتایی کد ملی
            raise DomainValidationError(
                "این کد ملی هم‌زمان توسط حساب دیگری ثبت شد.", code="national_id_conflict"
            ) from exc

        outbox.publish(
            "identity.profile_completed",
            aggregate_type="user",
            aggregate_id=user.id,
            payload={"user_id": str(user.id), "province": province, "role": user.role},
        )
        audit_record(
            AuditEntry(
                action=AuditAction.PROFILE_COMPLETED,
                actor_id=user.id,
                actor_label=user.display_name,
                target_type="user",
                target_id=str(user.id),
                metadata={"province": province, "city": clean_city},
                request=request,
            )
        )

    send_sms(
        phone=user.phone,
        template_key=SmsTemplateKey.WELCOME,
        context={"full_name": user.display_name},
        dedup_key=f"welcome:{user.id}",
    )
    return user


def promote_role(*, user: User, role: str, reason: str, actor: User) -> User:
    """تغییر نقش کاربر — عملیاتی با دلیل اجباری و ثبت کامل در حسابرسی."""
    if role not in UserRole.values:
        raise DomainValidationError("نقش انتخاب‌شده معتبر نیست.", code="invalid_role")
    if not reason or len(reason.strip()) < 5:  # noqa: PLR2004 - حداقل طول دلیل
        raise DomainValidationError("ثبت دلیل تغییر نقش الزامی است.", code="reason_required")
    previous = user.role
    user.role = role
    user.is_staff = role in UserRole.staff_roles()
    user.save(update_fields=["role", "is_staff"])
    audit_record(
        AuditEntry(
            action=AuditAction.ROLE_CHANGED,
            actor_id=actor.id,
            actor_label=actor.display_name,
            target_type="user",
            target_id=str(user.id),
            reason=reason,
            metadata={"from": previous, "to": role},
        )
    )
    return user


def serialize_profile(user: User) -> dict[str, Any]:
    """نمای ایمن پروفایل — بدون هیچ دادهٔ روشن حساس."""
    return {
        "id": str(user.id),
        "phone_masked": user.masked_phone,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "display_name": user.display_name,
        "province": user.province,
        "province_title": Province.label_of(user.province) if user.province else "",
        "city": user.city,
        "national_id_masked": user.masked_national_id,
        "role": user.role,
        "is_profile_complete": user.is_profile_complete,
        "is_staff_member": user.is_staff_member,
        "date_joined": user.date_joined.isoformat(),
    }
