"""موتور سیاست — «همهٔ اعداد قواعد، داده‌اند نه کد».

فلسفه: مدیر زائرسرا باید بتواند بدون انتشار نسخهٔ جدید نرم‌افزار، مدت اقامت،
افق رزرو، پنجرهٔ لغو و سهمیه‌ها را تغییر دهد. بنابراین:
    • پیش‌فرض‌ها در ``DEFAULTS`` (کد) زندگی می‌کنند تا سیستم «از روز اول کار کند».
    • مقدار جاری در جدول ``Policy`` (داده) است و با هر تغییر، یک ``PolicyRevision``
      تغییرناپذیر ثبت می‌شود (تاریخ‌نگاری کامل تصمیم‌ها).
    • خواندن از کش نسخه‌دار (Epoch) انجام می‌شود: تغییر سیاست، اتمیک و فوری
      اثر می‌کند و هیچ کلید کهنه‌ای باقی نمی‌ماند.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.core import metrics, outbox
from apps.core.cache import get_or_set_versioned, invalidate
from apps.core.errors import ValidationError as DomainValidationError
from apps.identity.models import User
from apps.inventory.lattice import LatticeConfig
from apps.reservation.models import Policy, PolicyRevision

logger = logging.getLogger(__name__)

POLICY_NAMESPACE = "policy"
POLICY_SCOPE = "global"
POLICY_CACHE_TTL_SECONDS = 300
REASON_MIN_LENGTH = 5

# ─── پیش‌فرض‌های سیاست (بخش ۹ سند دامنه) ───────────────────────────────────────
DEFAULTS: dict[str, Any] = {
    "reservation.min_cells": 1,
    "reservation.max_cells": 14,
    "reservation.booking_horizon_days": 90,
    "reservation.min_lead_time_hours": 2,
    "reservation.cancel_free_before_hours": 24,
    "reservation.late_cancel_cooldown_hours": 24,
    "reservation.no_show_grace_hours": 6,
    "reservation.no_show_suspension_days": 7,
    "reservation.max_active_per_user": 1,
    "reservation.max_cells_per_window": 14,
    "reservation.cells_window_days": 30,
    "reservation.max_cells_per_year": 60,
    "reservation.min_gap_between_bookings_hours": 24,
    "reservation.holds_enabled": False,
    "reservation.hold_ttl_minutes": 15,
    "reservation.allow_off_grid": False,
    "waitlist.offer_window_minutes": 30,
    "waitlist.max_offers_before_requeue": 3,
    "waitlist.auto_offer_enabled": True,
    "waitlist.max_active_per_guest": 1,
    "waitlist.max_cells_per_entry": 14,
    "lattice.grid_start_local": 8,
    "lattice.cell_hours": 12,
    "lattice.checkin_cutoff": 14,
    "lattice.checkout_cutoff": 12,
    "lattice.default_cleaning_buffer_minutes": 60,
    "notification.quiet_hours_start": 23,
    "notification.quiet_hours_end": 7,
    "notification.reminder_lead_hours": 24,
}

# ─── برچسب و توضیح فارسی سیاست‌ها (برای پنل مدیر و اسناد زنده) ────────────────
LABELS: dict[str, tuple[str, str]] = {
    "reservation.min_cells": ("کوتاه‌ترین اقامت", "کمترین تعداد نیم‌روز هر رزرو"),
    "reservation.max_cells": ("بلندترین اقامت", "بیشترین تعداد نیم‌روز هر رزرو"),
    "reservation.booking_horizon_days": ("افق رزرو", "تا چند روز آینده می‌توان رزرو کرد"),
    "reservation.min_lead_time_hours": ("مهلت پیش از ورود", "حداقل فاصلهٔ رزرو تا آغاز اقامت"),
    "reservation.cancel_free_before_hours": (
        "لغو رایگان تا",
        "تا چند ساعت قبل، لغو دیرینهنگام نیست",
    ),
    "reservation.late_cancel_cooldown_hours": (
        "خنک‌شدن پس از لغو دیرهنگام",
        "فاصلهٔ اجباری پیش از رزرو بعدی",
    ),
    "reservation.no_show_grace_hours": ("فرصت عدم‌حضور", "چند ساعت پس از آغاز، عدم‌حضور ثبت می‌شود"),
    "reservation.no_show_suspension_days": ("محرومیت عدم‌حضور", "مدت محرومیت پس از عدم‌حضور"),
    "reservation.max_active_per_user": ("رزرو فعال هم‌زمان", "سقف رزرو فعال برای هر زائر"),
    "reservation.max_cells_per_window": ("سقف نیم‌روز در پنجره", "سهمیهٔ نیم‌روز در بازهٔ زمانی"),
    "reservation.cells_window_days": ("طول پنجرهٔ سهمیه", "پنجرهٔ سهمیهٔ نیم‌روز به روز"),
    "reservation.max_cells_per_year": ("سهمیهٔ سالانه", "سقف نیم‌روز هر زائر در سال"),
    "reservation.min_gap_between_bookings_hours": (
        "فاصلهٔ میان رزروها",
        "حداقل فاصلهٔ پایان یک اقامت تا آغاز بعدی",
    ),
    "reservation.holds_enabled": ("نگه‌داشت موقت", "آیا نگه‌داشت چنددقیقه‌ای سانس فعال باشد"),
    "reservation.hold_ttl_minutes": ("عمر نگه‌داشت", "مدت نگه‌داشت سانس پیش از تأیید"),
    "reservation.allow_off_grid": ("رزرو بیرون شبکه", "اجازهٔ بازه‌های خارج از مرزهای نیم‌روز"),
    "waitlist.offer_window_minutes": ("مهلت پاسخ به پیشنهاد", "چند دقیقه برای پذیرش سانس آزادشده"),
    "waitlist.max_offers_before_requeue": (
        "سقف پیشنهادها",
        "پس از چند پیشنهاد، درخواست به انتهای صف برمی‌گردد",
    ),
    "waitlist.auto_offer_enabled": (
        "پیشنهاد خودکار صف",
        "آیا آزادشدن سانس، خودکار به صف پیشنهاد شود",
    ),
    "waitlist.max_active_per_guest": ("درخواست فعال صف", "سقف درخواست در جریان هر زائر"),
    "waitlist.max_cells_per_entry": ("سقف نیم‌روز درخواست", "بیشترین طول یک درخواست انتظار"),
    "lattice.grid_start_local": ("ساعت شروع شبکه", "ساعت محلی آغاز نخستین نیم‌روز"),
    "lattice.cell_hours": ("طول نیم‌روز", "ساعت هر سلول شبکه"),
    "lattice.checkin_cutoff": ("مهلت پذیرش روز", "ساعت پایانی پذیرش در سانس روز"),
    "lattice.checkout_cutoff": ("مهلت تخلیه روز", "ساعت پایانی تخلیه در سانس روز"),
    "lattice.default_cleaning_buffer_minutes": ("بافر نظافت", "فاصلهٔ پاک‌سازی میان دو اقامت"),
    "notification.quiet_hours_start": ("آغاز شب سکوت", "از این ساعت، پیامک ارسال نمی‌شود"),
    "notification.quiet_hours_end": ("پایان شب سکوت", "از این ساعت، پیامک‌ها از سر گرفته می‌شوند"),
    "notification.reminder_lead_hours": (
        "یادآور پیش از پذیرش",
        "چند ساعت پیش از ورود، یادآور برود",
    ),
}


POLICY_KEYS = frozenset(DEFAULTS)


@dataclass(frozen=True, slots=True)
class PolicyValue:
    """یک مقدار سیاست همراه با خاستگاه، پیش‌فرض، نوع و آخرین تغییر.

    این ساختار «read model» پنل مدیر است: بدون کوئری اضافه، هر چه برای نمایش
    و مقایسهٔ «مقدار مؤثر با پیش‌فرض» لازم است در دست است.
    """

    key: str
    value: Any
    source: str
    version: int
    default: Any = None
    label: str = ""
    help_text: str = ""
    updated_at: datetime | None = None
    changed_by: str | None = None
    reason: str = ""

    @property
    def type_name(self) -> str:
        """نام فارسی نوع مقدار (بولین، عدد، متن)."""
        if isinstance(self.value, bool):
            return "بولین"
        if isinstance(self.value, int):
            return "عدد"
        return "متن"

    @property
    def changed(self) -> bool:
        """آیا مقدار مؤثر با پیش‌فرض کد تفاوت دارد؟"""
        return bool(self.value != self.default)


def _coerce(default: Any, value: Any) -> Any:
    """هم‌نوع‌سازی مقدار داده‌ای با نوع پیش‌فرض (جلوگیری از خطای نوع در زمان اجرا)."""
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(default, int) and not isinstance(value, bool):
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            msg = f"مقدار سیاست «{value}» عدد صحیح نیست."
            raise DomainValidationError(msg, code="invalid_policy_value") from exc
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            msg = f"مقدار سیاست «{value}» عدد اعشاری نیست."
            raise DomainValidationError(msg, code="invalid_policy_value") from exc
    return value


def _load_overrides() -> dict[str, Any]:
    """خواندن همهٔ مقادیر بازنویسی‌شده از دیتابیس (یک کوئری، سپس کش)."""
    rows = Policy.objects.filter(is_active=True, effective_to__isnull=True).select_related(
        "changed_by"
    )
    return {
        row.key: {
            "value": row.value,
            "version": row.version,
            "updated_at": row.updated_at,
            "changed_by": (
                row.changed_by.display_name
                if row.changed_by_id and row.changed_by is not None
                else None
            ),
            "reason": row.change_reason,
        }
        for row in rows
    }


def load_policy_map() -> dict[str, Any]:
    """نقشهٔ کامل سیاست‌ها (پیش‌فرض + بازنویسی داده‌ای) با کش نسخه‌دار."""
    return get_or_set_versioned(
        POLICY_NAMESPACE,
        POLICY_SCOPE,
        "map",
        _load_overrides,
        timeout=POLICY_CACHE_TTL_SECONDS,
    )


def get_policy_value(key: str) -> PolicyValue:
    """خواندن یک سیاست با اولویت «داده بر پیش‌فرض»."""
    if key not in POLICY_KEYS:
        raise DomainValidationError(f"سیاست «{key}» شناخته‌شده نیست.", code="unknown_policy")
    label, help_text = LABELS.get(key, (key, ""))
    override = load_policy_map().get(key)
    if override is None:
        metrics.POLICY_READS.labels(key=key, source="default").inc()
        return PolicyValue(
            key=key,
            value=DEFAULTS[key],
            source="default",
            version=1,
            default=DEFAULTS[key],
            label=label,
            help_text=help_text,
        )
    metrics.POLICY_READS.labels(key=key, source="database").inc()
    return PolicyValue(
        key=key,
        value=_coerce(DEFAULTS[key], override["value"]),
        source="database",
        version=int(override["version"]),
        default=DEFAULTS[key],
        label=label,
        help_text=help_text,
        updated_at=override.get("updated_at"),
        changed_by=override.get("changed_by"),
        reason=str(override.get("reason") or ""),
    )


def get_policy(key: str) -> Any:
    """مقدار خام یک سیاست."""
    return get_policy_value(key).value


def policy_int(key: str) -> int:
    """مقدار عدد صحیح یک سیاست."""
    return int(get_policy(key))


def policy_bool(key: str) -> bool:
    """مقدار بولی یک سیاست."""
    return bool(get_policy(key))


def lattice_config() -> LatticeConfig:
    """ساخت پیکربندی شبکهٔ نیم‌روز از سیاست‌های جاری.

    اثر جانبی مهم: تغییر سیاست شبکه، فقط روی رزروهای **آینده** اثر دارد؛
    رزروهای موجود بازه‌های خود را حفظ می‌کنند (قاعدهٔ A4 — ثبات تاریخی).
    """
    return LatticeConfig(
        grid_start_hour=policy_int("lattice.grid_start_local"),
        cell_hours=policy_int("lattice.cell_hours"),
        cleaning_buffer_minutes=policy_int("lattice.default_cleaning_buffer_minutes"),
        checkin_hour=policy_int("lattice.checkin_cutoff"),
        checkout_hour=policy_int("lattice.checkout_cutoff"),
    )


def all_policies() -> list[PolicyValue]:
    """همهٔ سیاست‌ها با خاستگاه — برای پنل مدیریت و مستندات زنده."""
    return [get_policy_value(key) for key in sorted(POLICY_KEYS)]


def set_policy(
    *,
    key: str,
    value: Any,
    actor: User,
    reason: str,
    request: HttpRequest | None = None,
) -> Policy:
    """انتشار نسخهٔ تازهٔ یک سیاست (با دلیل، تاریخ‌نگاری و ابطال کش)."""
    if key not in POLICY_KEYS:
        raise DomainValidationError(f"سیاست «{key}» شناخته‌شده نیست.", code="unknown_policy")
    if len((reason or "").strip()) < REASON_MIN_LENGTH:
        raise DomainValidationError(
            "ثبت دلیل برای تغییر سیاست اجباری است (حداقل ۵ نویسه).", code="reason_required"
        )
    coerced = _coerce(DEFAULTS[key], value)

    with transaction.atomic():
        policy = Policy.objects.filter(key=key).first()
        if policy is None:
            policy = Policy(key=key, value=coerced, version=1, changed_by=actor)
            policy.change_reason = reason.strip()
            try:
                policy.save()
            except IntegrityError:  # مسابقهٔ ساخت هم‌زمان
                policy = Policy.objects.filter(key=key).first()
                if policy is None:  # pragma: no cover - حالت نظری
                    raise
        previous = policy.value
        policy.value = coerced
        policy.version += 1
        policy.changed_by = actor
        policy.change_reason = reason.strip()
        policy.effective_from = timezone.now()
        policy.save(
            update_fields=[
                "value",
                "version",
                "changed_by",
                "change_reason",
                "effective_from",
                "updated_at",
            ]
        )
        PolicyRevision.objects.create(
            policy=policy,
            version=policy.version,
            value=coerced,
            changed_by=actor,
            change_reason=reason.strip(),
        )
        outbox.publish(
            "policy.published",
            aggregate_type="policy",
            aggregate_id=policy.id,
            payload={"key": key, "version": policy.version, "changed_by": str(actor.id)},
        )
        audit_record(
            AuditEntry(
                action=AuditAction.POLICY_CHANGED,
                actor_id=actor.id,
                actor_label=actor.display_name,
                target_type="policy",
                target_id=str(policy.id),
                reason=reason.strip(),
                metadata={"key": key, "from": previous, "to": coerced, "version": policy.version},
                request=request,
            )
        )

    invalidate(POLICY_NAMESPACE, POLICY_SCOPE)
    logger.info("policy_published", extra={"key": key, "version": policy.version})
    return policy


def policy_snapshot() -> dict[str, Any]:
    """عکس فوری سیاست‌ها — در گزارش اکسل و تشخیص عیب‌یابی استفاده می‌شود."""
    return {key: get_policy(key) for key in sorted(POLICY_KEYS)}


def quiet_hours(now: datetime) -> bool:
    """آیا اکنون در ساعات سکوت پیامک هستیم؟ (پیش‌فرض ۲۳ تا ۷)"""
    start = policy_int("notification.quiet_hours_start")
    end = policy_int("notification.quiet_hours_end")
    hour = now.astimezone(timezone.get_current_timezone()).hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end
