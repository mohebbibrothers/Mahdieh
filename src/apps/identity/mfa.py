"""تأیید دومرحله‌ای کارکنان (ADR-0018) — TOTP + کدهای بازیابی + تأیید تازه.

مدل تهدید: کارمند با پیامک وارد می‌شود. پیامک به‌تنهایی برای حساب‌هایی که
می‌توانند رزرو دیگران را لغو/جابه‌جا کنند، سیاست‌ها را تغییر دهند و شمارهٔ کامل
زائران را ببینند، کافی نیست (سیم‌کارت‌سوآپ، گوشی رهاشده روی پیشخوان). پس:

    ۱. **عامل دوم اجباری:** نشست کارمند تا تأیید TOTP، فقط به مسیرهای ورود و
       ثبت عامل دوم دسترسی دارد (``StaffMfaMiddleware``).
    ۲. **تأیید تازه (step-up):** کارهای پرخطر — تغییر سیاست، دیدن شمارهٔ کامل،
       خروجی اکسل با شمارهٔ کامل، ساخت دوبارهٔ کدهای بازیابی — به تأییدی نیاز
       دارند که از ``STAFF_MFA_STEP_UP_SECONDS`` قدیمی‌تر نباشد.
    ۳. **ضد حدس:** پس از ``MAX_FAILURES`` تلاش ناموفق در ``LOCKOUT_SECONDS``،
       تأیید موقتاً قفل می‌شود؛ هر تلاش در زنجیرهٔ حسابرسی ثبت می‌گردد.

وضعیت «تأییدشده» در **خودِ نشست** است (نه در کاربر): خروج، ابطال نشست یا ورود
از دستگاه دیگر، تأیید را از نو می‌خواهد.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import secrets
import time
from typing import Any, Final

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.audit.choices import AuditAction, AuditOutcome
from apps.audit.services import AuditEntry, record as audit_record
from apps.core.crypto import blind_index
from apps.core.errors import (
    ConflictError,
    PermissionDeniedError,
    RateLimitedError,
    ValidationError as DomainValidationError,
)
from apps.identity import totp
from apps.identity.models import (
    RECOVERY_CODE_NAMESPACE,
    StaffRecoveryCode,
    StaffTotpDevice,
    User,
)

SESSION_VERIFIED_AT: Final[str] = "mfa_verified_at"
SESSION_METHOD: Final[str] = "mfa_method"
ISSUER: Final[str] = "Mahdieh"
RECOVERY_CODE_COUNT: Final[int] = 10
# الفبای کد بازیابی (بدون 0/O و 1/I/L تا خواندن از کاغذ خطا نزاید) — راز نیست.
RECOVERY_ALPHABET: Final[str] = "23456789ABCDEFGHJKMNPQRSTVWXYZ"  # pragma: allowlist secret
RECOVERY_GROUP: Final[int] = 5
MAX_FAILURES: Final[int] = 5
LOCKOUT_SECONDS: Final[int] = 15 * 60
METHOD_TOTP: Final[str] = "totp"
METHOD_RECOVERY: Final[str] = "recovery"


class MfaRequiredError(PermissionDeniedError):
    """نشست کارمند هنوز عامل دوم را تأیید نکرده است."""

    code = "mfa_required"
    title = "تأیید دومرحله‌ای لازم است"
    default_detail = "برای ادامه، کد اپ احراز هویت را وارد کنید."


class StepUpRequiredError(PermissionDeniedError):
    """کار پرخطر — تأیید دومرحله‌ای باید تازه باشد."""

    code = "step_up_required"
    title = "تأیید دوباره لازم است"
    default_detail = "این کار حساس است؛ لطفاً کد اپ احراز هویت را دوباره وارد کنید."


@dataclass(frozen=True, slots=True)
class Enrollment:
    """دادهٔ صفحهٔ ثبت عامل دوم — راز فقط همین یک بار نمایش داده می‌شود."""

    secret: str
    secret_display: str
    otpauth_uri: str
    qr_svg: str


@dataclass(frozen=True, slots=True)
class MfaStatus:
    """وضعیت عامل دوم برای نشست جاری (پاسخ ``/auth/mfa/status``)."""

    required: bool
    enrolled: bool
    verified: bool
    verified_at: datetime | None
    step_up_valid_until: datetime | None
    step_up_seconds: int
    recovery_codes_remaining: int
    method: str

    def as_dict(self) -> dict[str, Any]:
        """نمای سریال‌پذیر."""
        return {
            "required": self.required,
            "enrolled": self.enrolled,
            "verified": self.verified,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "step_up_valid_until": (
                self.step_up_valid_until.isoformat() if self.step_up_valid_until else None
            ),
            "step_up_seconds": self.step_up_seconds,
            "recovery_codes_remaining": self.recovery_codes_remaining,
            "method": self.method,
        }


# ── خواندن وضعیت ────────────────────────────────────────────────────────────


def mfa_enforced() -> bool:
    """آیا عامل دوم کارکنان در این استقرار اجباری است؟ (تولید: همیشه بله)."""
    return bool(getattr(settings, "STAFF_MFA_REQUIRED", True))


def step_up_seconds() -> int:
    """طول پنجرهٔ «تأیید تازه» به ثانیه."""
    return int(getattr(settings, "STAFF_MFA_STEP_UP_SECONDS", 600))


def applies_to(user: Any) -> bool:
    """آیا این کاربر مشمول عامل دوم است؟ (فقط کارکنان واردشده)."""
    return bool(
        mfa_enforced()
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_staff_member", False)
    )


def _verified_epoch(request: HttpRequest) -> float | None:
    session = getattr(request, "session", None)
    if session is None:
        return None
    raw = session.get(SESSION_VERIFIED_AT)
    return float(raw) if isinstance(raw, int | float) else None


def is_satisfied(request: HttpRequest) -> bool:
    """آیا نشست جاری از سد عامل دوم گذشته است؟ (غیرکارمند: همیشه بله)."""
    if not applies_to(getattr(request, "user", None)):
        return True
    return _verified_epoch(request) is not None


def is_fresh(request: HttpRequest) -> bool:
    """آیا تأیید نشست برای کار پرخطر «تازه» است؟"""
    if not applies_to(getattr(request, "user", None)):
        return True
    verified = _verified_epoch(request)
    return verified is not None and time.time() - verified <= step_up_seconds()


def require_fresh(request: HttpRequest) -> None:
    """نگهبان کارهای پرخطر — در نبود تأیید تازه، خطای ``step_up_required``."""
    if not is_fresh(request):
        raise StepUpRequiredError(extensions={"mfa": _problem_hint(request)})


def has_staff_powers(request: HttpRequest) -> bool:
    """کارمندی که از سد عامل دوم گذشته است — یگانه پرسش «اختیار کارمندی».

    نماهای مشترک (جزئیات/لغو رزرو، تقویم) به‌جای ``user.is_staff_member`` همین
    را می‌پرسند تا نشست تأییدنشده، حتی از مسیرهای زائر، اختیار کارمندی نگیرد.
    """
    user = getattr(request, "user", None)
    return bool(getattr(user, "is_staff_member", False)) and is_satisfied(request)


def _device(user: User) -> StaffTotpDevice | None:
    return StaffTotpDevice.objects.filter(user=user).first()


def _problem_hint(request: HttpRequest) -> dict[str, Any]:
    user = getattr(request, "user", None)
    enrolled = bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and StaffTotpDevice.objects.filter(user=user, confirmed_at__isnull=False).exists()
    )
    return {"enrolled": enrolled, "step_up_seconds": step_up_seconds()}


def mfa_required_error(request: HttpRequest) -> MfaRequiredError:
    """خطای «عامل دوم لازم است» با راهنمای گام بعد برای فرانت."""
    return MfaRequiredError(extensions={"mfa": _problem_hint(request)})


def status_for(request: HttpRequest) -> MfaStatus:
    """وضعیت کامل عامل دوم برای نشست جاری."""
    user = request.user
    device = _device(user) if getattr(user, "is_authenticated", False) else None  # type: ignore[arg-type]
    verified = _verified_epoch(request)
    verified_at = (
        datetime.fromtimestamp(verified, tz=timezone.get_current_timezone()) if verified else None
    )
    fresh_until = (
        datetime.fromtimestamp(verified + step_up_seconds(), tz=timezone.get_current_timezone())
        if verified
        else None
    )
    remaining = (
        StaffRecoveryCode.objects.filter(user_id=device.user_id, used_at__isnull=True).count()
        if device is not None
        else 0
    )
    session = getattr(request, "session", None)
    return MfaStatus(
        required=applies_to(user),
        enrolled=bool(device and device.is_confirmed),
        verified=verified is not None,
        verified_at=verified_at,
        step_up_valid_until=fresh_until,
        step_up_seconds=step_up_seconds(),
        recovery_codes_remaining=remaining,
        method=str(session.get(SESSION_METHOD, "")) if session is not None else "",
    )


# ── ثبت عامل دوم ────────────────────────────────────────────────────────────


def _require_staff(user: User) -> None:
    if not user.is_staff_member:
        raise PermissionDeniedError("تأیید دومرحله‌ای فقط برای حساب کارکنان است.")


def begin_enrollment(*, user: User) -> Enrollment:
    """آغاز ثبت: راز تازه + QR. دستگاه تأییدشدهٔ قبلی را بی‌صدا عوض نمی‌کند."""
    _require_staff(user)
    device = _device(user)
    if device is not None and device.is_confirmed:
        raise ConflictError(
            "تأیید دومرحله‌ای این حساب قبلاً فعال شده است. برای تعویض گوشی، "
            "از کد بازیابی استفاده کنید یا از مدیر سامانه بخواهید آن را بازنشانی کند.",
            code="mfa_already_enrolled",
        )
    secret = totp.generate_secret()
    StaffTotpDevice.objects.update_or_create(
        user=user, defaults={"secret": secret, "confirmed_at": None, "last_used_step": None}
    )
    uri = totp.provisioning_uri(secret, account=user.masked_phone, issuer=ISSUER)
    return Enrollment(
        secret=secret,
        secret_display=totp.format_secret(secret),
        otpauth_uri=uri,
        qr_svg=_qr_svg(uri),
    )


def _qr_svg(payload: str) -> str:
    import segno  # بارگذاری تنبل — فقط هنگام ثبت عامل دوم

    # پس‌زمینهٔ سفید + حاشیهٔ آرام ۴ ماژولی (استاندارد ISO/IEC 18004) درون خود SVG:
    # صفحهٔ کارکنان تیره است و QR شفاف (light=None) تیره‌روی‌تیره، نامرئی می‌شد
    # (یافتهٔ گام ۸ب). ``omitsize`` ⇒ فقط viewBox، تا CSS اندازه را بی‌اعوجاج تعیین کند.
    qr = segno.make(payload, error="m")
    return str(
        qr.svg_inline(
            scale=5,
            dark="#0b1220",
            light="#ffffff",
            border=4,
            omitsize=True,
            title="کد QR فعال‌سازی اپ احراز هویت",
        )
    )


def confirm_enrollment(*, request: HttpRequest, user: User, code: str) -> list[str]:
    """تأیید نخستین کد ⇒ فعال‌سازی دستگاه + کدهای بازیابی (فقط همین یک بار)."""
    _require_staff(user)
    _guard_lockout(user)
    device = _device(user)
    if device is None or device.is_confirmed:
        raise ConflictError(
            "ابتدا ثبت تأیید دومرحله‌ای را آغاز کنید.", code="mfa_enrollment_not_started"
        )
    step = totp.match_step(device.secret, code, unix_seconds=time.time())
    if step is None:
        _register_failure(request, user, stage="enroll")
    with transaction.atomic():
        device.confirmed_at = timezone.now()
        device.last_used_step = step
        device.last_used_at = timezone.now()
        device.save(update_fields=["confirmed_at", "last_used_step", "last_used_at"])
        codes = _replace_recovery_codes(user)
        audit_record(
            AuditEntry(
                action=AuditAction.MFA_ENROLLED,
                actor_id=user.pk,
                actor_label=user.display_name,
                target_type="user",
                target_id=str(user.pk),
                request=request,
            )
        )
    _mark_verified(request, method=METHOD_TOTP)
    return codes


# ── تأیید ───────────────────────────────────────────────────────────────────


def verify(*, request: HttpRequest, user: User, code: str) -> str:
    """تأیید عامل دوم با کد TOTP یا کد بازیابی؛ بازگشت روش استفاده‌شده."""
    _require_staff(user)
    _guard_lockout(user)
    device = _device(user)
    if device is None or not device.is_confirmed:
        raise ConflictError(
            "تأیید دومرحله‌ای هنوز برای این حساب فعال نشده است.", code="mfa_not_enrolled"
        )
    digits = "".join(char for char in code if char.isdigit())
    if len(digits) == totp.DIGITS and len(code.strip()) <= totp.DIGITS + 1:
        method = _verify_totp(device, digits)
    else:
        method = _verify_recovery(user, code)
    if method is None:
        _register_failure(request, user, stage="verify")
    cache.delete(_lockout_key(user))
    audit_record(
        AuditEntry(
            action=AuditAction.MFA_VERIFIED,
            actor_id=user.pk,
            actor_label=user.display_name,
            target_type="user",
            target_id=str(user.pk),
            metadata={"method": method},
            request=request,
        )
    )
    _mark_verified(request, method=str(method))
    return str(method)


def _verify_totp(device: StaffTotpDevice, code: str) -> str | None:
    with transaction.atomic():
        locked = StaffTotpDevice.objects.select_for_update().get(pk=device.pk)
        step = totp.match_step(
            locked.secret, code, unix_seconds=time.time(), last_used_step=locked.last_used_step
        )
        if step is None:
            return None
        locked.last_used_step = step
        locked.last_used_at = timezone.now()
        locked.save(update_fields=["last_used_step", "last_used_at"])
    return METHOD_TOTP


def _normalize_recovery(code: str) -> str:
    return "".join(char for char in code.upper() if char.isalnum())


def _verify_recovery(user: User, code: str) -> str | None:
    digest = blind_index(_normalize_recovery(code), namespace=RECOVERY_CODE_NAMESPACE)
    if not digest:
        return None
    with transaction.atomic():
        row = (
            StaffRecoveryCode.objects.select_for_update()
            .filter(user=user, code_hash=digest, used_at__isnull=True)
            .first()
        )
        if row is None:
            return None
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
    return METHOD_RECOVERY


def regenerate_recovery_codes(*, request: HttpRequest, user: User) -> list[str]:
    """ساخت دوبارهٔ کدهای بازیابی (کدهای قبلی باطل) — نیازمند تأیید تازه."""
    _require_staff(user)
    require_fresh(request)
    if not StaffTotpDevice.objects.filter(user=user, confirmed_at__isnull=False).exists():
        raise ConflictError("ابتدا تأیید دومرحله‌ای را فعال کنید.", code="mfa_not_enrolled")
    with transaction.atomic():
        codes = _replace_recovery_codes(user)
        audit_record(
            AuditEntry(
                action=AuditAction.MFA_RECOVERY_REGENERATED,
                actor_id=user.pk,
                actor_label=user.display_name,
                target_type="user",
                target_id=str(user.pk),
                request=request,
            )
        )
    return codes


def reset_device(*, user: User, actor_label: str, reason: str) -> bool:
    """بازنشانی عامل دوم (گوشی گم‌شده) — فقط از فرمان مدیریتی، با دلیل اجباری."""
    with transaction.atomic():
        deleted, _ = StaffTotpDevice.objects.filter(user=user).delete()
        StaffRecoveryCode.objects.filter(user=user).delete()
        audit_record(
            AuditEntry(
                action=AuditAction.MFA_RESET,
                actor_label=actor_label,
                target_type="user",
                target_id=str(user.pk),
                reason=reason,
            )
        )
    return bool(deleted)


# ── کمکی‌ها ─────────────────────────────────────────────────────────────────


def _replace_recovery_codes(user: User) -> list[str]:
    StaffRecoveryCode.objects.filter(user=user).delete()
    codes: list[str] = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_GROUP * 2))
        codes.append(f"{raw[:RECOVERY_GROUP]}-{raw[RECOVERY_GROUP:]}")
    StaffRecoveryCode.objects.bulk_create(
        [
            StaffRecoveryCode(
                user=user,
                code_hash=blind_index(_normalize_recovery(code), namespace=RECOVERY_CODE_NAMESPACE),
            )
            for code in codes
        ]
    )
    return codes


def _mark_verified(request: HttpRequest, *, method: str) -> None:
    """ثبت تأیید در نشست + چرخش کلید نشست (ضد تثبیت نشست)."""
    session = getattr(request, "session", None)
    if session is None:
        return
    old_key = session.session_key
    session.cycle_key()
    session[SESSION_VERIFIED_AT] = time.time()
    session[SESSION_METHOD] = method
    from apps.identity import sessions as session_registry

    session_registry.rekey(old_key=old_key, new_key=session.session_key)


def _lockout_key(user: User) -> str:
    return f"mfa:failures:{user.pk}"


def _guard_lockout(user: User) -> None:
    failures = int(cache.get(_lockout_key(user)) or 0)
    if failures >= MAX_FAILURES:
        raise RateLimitedError(
            "به‌خاطر چند تلاش ناموفق، تأیید دومرحله‌ای ۱۵ دقیقه قفل شد.",
            code="mfa_locked",
            extensions={"retry_after": LOCKOUT_SECONDS},
        )


def _register_failure(request: HttpRequest, user: User, *, stage: str) -> None:
    """ثبت تلاش ناموفق (شمارنده + حسابرسی) و برگرداندن خطای ۴۲۲ روی فیلد ``code``."""
    key = _lockout_key(user)
    if not cache.add(key, 1, timeout=LOCKOUT_SECONDS):
        try:
            cache.incr(key)
        except ValueError:  # pragma: no cover - کلید در همین لحظه منقضی شد
            cache.set(key, 1, timeout=LOCKOUT_SECONDS)
    audit_record(
        AuditEntry(
            action=AuditAction.MFA_FAILED,
            outcome=AuditOutcome.FAILURE,
            actor_id=user.pk,
            actor_label=user.display_name,
            target_type="user",
            target_id=str(user.pk),
            metadata={"stage": stage},
            request=request,
        )
    )
    message = "کد واردشده درست نیست یا منقضی شده است."
    raise DomainValidationError(message, code="invalid_mfa_code", errors={"code": [message]})
