"""سرویس حسابرسی — افزودن امن رکورد به زنجیره و بررسی یکپارچگی.

قواعد:
    • افزودن رکورد **اتمی** است: شمارهٔ ترتیبی و هش، داخل یک تراکنش قفل‌شده
      محاسبه می‌شوند تا هیچ رکوردی هم‌زمان از زنجیره جدا نشود.
    • این سرویس هرگز دادهٔ حساس نمی‌گیرد؛ فراداده باید از پیش پاک‌سازی شده باشد.
    • عملیات مدیریتی **بدون دلیل** ثبت نمی‌شوند (قاعدهٔ I14 در مدل دامنه).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import logging
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.http import HttpRequest
from django.utils import timezone

from apps.audit.choices import AuditAction, AuditOutcome
from apps.audit.models import GENESIS_HASH, AuditAnchor, AuditRecord, compute_hash
from apps.core.middleware import request_metadata
from apps.core.uuid7 import uuid7

logger = logging.getLogger(__name__)

# کنش‌هایی که ثبت دلیل برای آن‌ها اجباری است
ANCHOR_SCAN_LIMIT: Final[int] = 500
REQUIRE_REASON: frozenset[str] = frozenset(
    {
        AuditAction.POLICY_CHANGED,
        AuditAction.RESERVATION_MOVED,
        AuditAction.RESERVATION_CANCELLED,
        AuditAction.BLOCK_CREATED,
        AuditAction.ROLE_CHANGED,
        AuditAction.MFA_RESET,
    }
)


@dataclass(slots=True)
class AuditEntry:
    """ورودی ثبت یک رکورد حسابرسی."""

    action: str
    outcome: str = AuditOutcome.SUCCESS
    actor_id: UUID | None = None
    actor_label: str = ""
    target_type: str = ""
    target_id: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    request: HttpRequest | None = None


def record(entry: AuditEntry) -> AuditRecord:
    """افزودن یک رکورد به زنجیرهٔ حسابرسی (اتمی)."""
    if entry.action in REQUIRE_REASON and not entry.reason.strip():
        msg = f"ثبت دلیل برای کنش «{entry.action}» اجباری است."
        raise ValueError(msg)

    with transaction.atomic():
        # سریال‌سازی زنجیره: آخرین رکورد قفل می‌شود تا شماره/هش یکتا بماند
        last = (
            AuditRecord.all_objects.select_for_update()
            .order_by("-sequence")
            .values("sequence", "record_hash")
            .first()
        )
        sequence = (last["sequence"] + 1) if last else 1
        previous_hash = last["record_hash"] if last else GENESIS_HASH

        meta = request_metadata(entry.request) if entry.request is not None else {}
        record_obj = AuditRecord.all_objects.create(
            sequence=sequence,
            action=entry.action,
            outcome=entry.outcome,
            actor_id=entry.actor_id,
            actor_label=entry.actor_label[:80],
            target_type=entry.target_type[:64],
            target_id=str(entry.target_id)[:64],
            reason=entry.reason,
            metadata=entry.metadata,
            request_id=str(meta.get("request_id") or ""),
            ip_address=meta.get("ip") or None,
            user_agent=str(meta.get("user_agent") or "")[:300],
            previous_hash=previous_hash,
            record_hash=uuid7().hex,  # مقدار موقت یکتا؛ پایین‌تر با هش واقعی جایگزین می‌شود
        )
        # نکتهٔ مهندسی: هش باید ``created_at`` **واقعیِ ذخیره‌شده** را پوشش دهد
        # (خودِ دیتابیس/جنگو مهر زمانی را می‌گذارد)، بنابراین محاسبهٔ نهایی پس
        # از درج و در همان تراکنش انجام می‌شود. مقدار موقت باید یکتا باشد تا با
        # قید یکتایی ``record_hash`` (که تضمین‌کنندهٔ نبود تکرار در زنجیره است)
        # تصادم نکند.
        record_obj.record_hash = compute_hash(
            previous_hash=previous_hash, payload=record_obj.payload_for_hash()
        )
        record_obj.save(update_fields=["record_hash", "updated_at"])

    logger.info(
        "audit_recorded",
        extra={"action": entry.action, "sequence": sequence, "outcome": entry.outcome},
    )
    return record_obj


def verify_chain(
    *, from_sequence: int = 1, to_sequence: int | None = None
) -> tuple[bool, int | None]:
    """بررسی پیوستگی و صحت زنجیرهٔ حسابرسی.

    بازگشت ``(is_valid, first_broken_sequence)`` — اگر زنجیره سالم باشد، مقدار
    دوم ``None`` است.
    """
    queryset = AuditRecord.all_objects.order_by("sequence")
    if from_sequence > 1:
        queryset = queryset.filter(sequence__gte=from_sequence)
    if to_sequence is not None:
        queryset = queryset.filter(sequence__lte=to_sequence)

    expected_previous = GENESIS_HASH
    for record_obj in queryset.iterator(chunk_size=500):
        if record_obj.previous_hash != expected_previous:
            return False, record_obj.sequence
        if not record_obj.verify():
            return False, record_obj.sequence
        expected_previous = record_obj.record_hash
    return True, None


def create_anchor() -> AuditAnchor:
    """ساخت لنگر امضاشده از وضعیت فعلی زنجیره (کار روزانه)."""
    last = AuditRecord.all_objects.order_by("-sequence").first()
    if last is None:
        msg = "زنجیرهٔ حسابرسی خالی است؛ لنگر بی‌معناست."
        raise ValueError(msg)

    count = AuditRecord.all_objects.count()
    anchor = AuditAnchor(
        last_sequence=last.sequence,
        last_hash=last.record_hash,
        record_count=count,
        anchored_at=timezone.now(),
        signature="",
    )
    anchor.signature = _sign(anchor.payload_for_signature())
    anchor.save()
    return anchor


def _sign(payload: dict[str, Any]) -> str:
    """امضای HMAC لنگر با کلید محیط."""
    from apps.audit.models import canonical_payload

    key = settings.FIELD_HASH_KEY or settings.SECRET_KEY
    return hmac.new(
        key.encode("utf-8"),
        canonical_payload(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_anchor(anchor: AuditAnchor) -> bool:
    """بررسی امضای لنگر — کشف دستکاری رکورد لنگر."""
    return hmac.compare_digest(anchor.signature, _sign(anchor.payload_for_signature()))


@dataclass(frozen=True, slots=True)
class AnchorReport:
    """گزارش بررسی لنگرها — خروجی ماشین‌خوان برای دستور ``audit_verify``."""

    checked: int
    invalid_sequences: tuple[int, ...]
    latest_anchor_sequence: int

    @property
    def valid(self) -> bool:
        """آیا همهٔ لنگرهای بررسی‌شده سالم و هم‌خوان با زنجیره بودند؟"""
        return not self.invalid_sequences


def verify_anchors(*, limit: int = ANCHOR_SCAN_LIMIT) -> AnchorReport:
    """بررسی همهٔ لنگرها: امضا، هم‌خوانی با رکورد هم‌شماره و شمار رکوردها.

    چرا «هم‌خوانی با رکورد هم‌شماره» هم لازم است؟ امضای درست فقط می‌گوید لنگر
    از سمت ما صادر شده؛ اما اگر کسی کل رکوردهای بعد از لنگر را حذف کند، امضا
    سالم می‌ماند و زنجیره کوتاه‌تر می‌شود. مقایسهٔ ``last_hash`` لنگر با رکوردِ
    همان شماره، حذف و جای‌گشت را آشکار می‌کند.
    """
    anchors = list(AuditAnchor.objects.order_by("-last_sequence")[:limit])
    invalid: list[int] = []
    latest = 0
    for anchor in anchors:
        latest = max(latest, anchor.last_sequence)
        if not verify_anchor(anchor):
            invalid.append(anchor.last_sequence)
            continue
        if anchor.record_count > latest_sequence():
            invalid.append(anchor.last_sequence)
            continue
        record_obj = AuditRecord.all_objects.filter(sequence=anchor.last_sequence).first()
        if record_obj is None or record_obj.record_hash != anchor.last_hash:
            invalid.append(anchor.last_sequence)
    return AnchorReport(
        checked=len(anchors),
        invalid_sequences=tuple(invalid),
        latest_anchor_sequence=latest,
    )


def latest_sequence() -> int:
    """شمارهٔ آخرین رکورد ثبت‌شده."""
    return AuditRecord.all_objects.aggregate(value=Max("sequence"))["value"] or 0
