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

    # رزرو (فاز ۳)
    RESERVATION_CREATED = "reservation.created", _("ایجاد رزرو")
    RESERVATION_CANCELLED = "reservation.cancelled", _("لغو رزرو")
    RESERVATION_MOVED = "reservation.moved", _("جابه‌جایی رزرو")
    RESERVATION_CHECKED_IN = "reservation.checked_in", _("پذیرش مهمان")
    RESERVATION_CHECKED_OUT = "reservation.checked_out", _("تخلیهٔ مهمان")
    RESERVATION_NO_SHOW = "reservation.no_show", _("ثبت عدم‌حضور")

    # عملیات مدیریتی
    POLICY_CHANGED = "policy.changed", _("تغییر سیاست")
    BLOCK_CREATED = "block.created", _("ایجاد بلوک نگهداری")
    BLOCK_REMOVED = "block.removed", _("برداشتن بلوک نگهداری")
    EXPORT_REQUESTED = "export.requested", _("درخواست خروجی")
    SENSITIVE_DATA_READ = "data.sensitive_read", _("خواندن دادهٔ حساس")

    # سیستم
    AUTHZ_DENIED = "authz.denied", _("رد دسترسی")
    ANCHOR_CREATED = "audit.anchor_created", _("ثبت لنگر زنجیره")


class AuditOutcome(models.TextChoices):
    """نتیجهٔ کنش."""

    SUCCESS = "success", _("موفق")
    FAILURE = "failure", _("ناموفق")
    DENIED = "denied", _("ردشده")
