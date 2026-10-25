"""شمارشی‌های حسابرسی."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditAction(models.TextChoices):
    """کنش‌های حساسی که باید ثبت شوند."""

    # هویت
    USER_REGISTERED = "user.registered", _("ثبت‌نام کاربر")
    PROFILE_COMPLETED = "user.profile_completed", _("تکمیل پروفایل")
    LOGIN_SUCCEEDED = "auth.login_succeeded", _("ورود موفق")
    LOGIN_FAILED = "auth.login_failed", _("ورود ناموفق")
    SESSION_REVOKED = "auth.session_revoked", _("ابطال نشست")
    ROLE_CHANGED = "auth.role_changed", _("تغییر نقش")
    MFA_ENROLLED = "auth.mfa_enrolled", _("فعال‌سازی تأیید دومرحله‌ای")
    MFA_VERIFIED = "auth.mfa_verified", _("تأیید دومرحله‌ای موفق")
    MFA_FAILED = "auth.mfa_failed", _("تأیید دومرحله‌ای ناموفق")
    MFA_RESET = "auth.mfa_reset", _("بازنشانی تأیید دومرحله‌ای")
    MFA_RECOVERY_REGENERATED = "auth.mfa_recovery_regenerated", _("ساخت دوبارهٔ کدهای بازیابی")
    PASSWORD_SET = (
        "auth.password_set",  # pragma: allowlist secret — شناسهٔ کنش، نه راز
        _("تعیین گذرواژهٔ کارمند از خط فرمان"),
    )

    # رزرو (فاز ۳)
    RESERVATION_CREATED = "reservation.created", _("ایجاد رزرو")
    RESERVATION_CANCELLED = "reservation.cancelled", _("لغو رزرو")
    RESERVATION_MOVED = "reservation.moved", _("جابه‌جایی رزرو")
    RESERVATION_CHECKED_IN = "reservation.checked_in", _("پذیرش مهمان")
    RESERVATION_CHECKED_OUT = "reservation.checked_out", _("تخلیهٔ مهمان")
    RESERVATION_NO_SHOW = "reservation.no_show", _("ثبت عدم‌حضور")
    RESERVATION_DETAILS_UPDATED = "reservation.details_updated", _("به‌روزرسانی هماهنگی ورود")

    # عملیات مدیریتی
    POLICY_CHANGED = "policy.changed", _("تغییر سیاست")
    # ── صف انتظار ────────────────────────────────────────────────────────────
    WAITLIST_JOINED = "waitlist.joined", _("ثبت درخواست انتظار")
    WAITLIST_WITHDRAWN = "waitlist.withdrawn", _("انصراف از صف انتظار")
    WAITLIST_OFFERED = "waitlist.offered", _("ارسال پیشنهاد سانس")
    WAITLIST_ACCEPTED = "waitlist.accepted", _("پذیرش پیشنهاد سانس")
    WAITLIST_DECLINED = "waitlist.declined", _("رد پیشنهاد سانس")
    WAITLIST_OFFER_EXPIRED = "waitlist.offer_expired", _("انقضای پیشنهاد سانس")
    BLOCK_CREATED = "block.created", _("ایجاد بلوک نگهداری")
    BLOCK_REMOVED = "block.removed", _("برداشتن بلوک نگهداری")
    EXPORT_REQUESTED = "export.requested", _("درخواست خروجی")
    CALENDAR_EXPORTED = "reservation.calendar_exported", _("خروجی تقویم اقامت")
    FEEDBACK_REQUESTED = "feedback.requested", _("درخواست نظرسنجی اقامت")
    FEEDBACK_SUBMITTED = "feedback.submitted", _("ثبت نظرسنجی اقامت")
    SENSITIVE_DATA_READ = "data.sensitive_read", _("خواندن دادهٔ حساس")
    GUEST_REDACTED = "data.guest_redacted", _("کمینه‌سازی هویت زائر")

    # سیستم
    AUTHZ_DENIED = "authz.denied", _("رد دسترسی")
    ANCHOR_CREATED = "audit.anchor_created", _("ثبت لنگر زنجیره")


class AuditOutcome(models.TextChoices):
    """نتیجهٔ کنش."""

    SUCCESS = "success", _("موفق")
    FAILURE = "failure", _("ناموفق")
    DENIED = "denied", _("ردشده")
