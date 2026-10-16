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
    "lattice.grid_start_local": 8,
    "lattice.cell_hours": 12,
    "lattice.checkin_cutoff": 14,
    "lattice.checkout_cutoff": 12,
    "lattice.default_cleaning_buffer_minutes": 60,
    "notification.quiet_hours_start": 23,
    "notification.quiet_hours_end": 7,
    "notification.reminder_lead_hours": 24,
}

POLICY_KEYS = frozenset(DEFAULTS)


@dataclass(frozen=True, slots=True)
class PolicyValue:
    """یک مقدار سیاست همراه با خاستگاه آن (پیش‌فرض کد یا مقدار داده‌ای)."""

    key: str
    value: Any
    source: str
    version: int


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
    rows = Policy.objects.filter(is_active=True, effective_to__isnull=True).values_list(
        "key", "value", "version"
    )
    return {key: {"value": value, "version": version} for key, value, version in rows}


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
    override = load_policy_map().get(key)
    if override is None:
        metrics.POLICY_READS.labels(key=key, source="default").inc()
        return PolicyValue(key=key, value=DEFAULTS[key], source="default", version=1)
    metrics.POLICY_READS.labels(key=key, source="database").inc()
    return PolicyValue(
        key=key,
        value=_coerce(DEFAULTS[key], override["value"]),
        source="database",
        version=int(override["version"]),
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
