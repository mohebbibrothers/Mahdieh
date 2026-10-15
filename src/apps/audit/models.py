"""مدل‌های حسابرسی — زنجیرهٔ هش برای اثبات عدم‌دستکاری.

ایدهٔ اصلی: هر رکورد حسابرسی، هش رکورد قبلی را در محاسبهٔ هش خودش می‌آورد:
    ``hash_n = sha256(hash_{n-1} || canonical_payload_n)``
بنابراین حذف یا تغییر هر رکورد میانی، همهٔ هش‌های بعدی را می‌شکند و در
بررسی زنجیره لو می‌رود. لنگر دوره‌ای (AuditAnchor) برای اثبات بیرونی است.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.choices import AuditAction, AuditOutcome
from apps.core.models import BaseModel

GENESIS_HASH = "0" * 64


def canonical_payload(payload: dict[str, Any]) -> str:
    """نمای متعارف بدنه — ترتیب کلیدها قطعی و بدون فاصلهٔ اضافه."""
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


def compute_hash(*, previous_hash: str, payload: dict[str, Any]) -> str:
    """محاسبهٔ هش زنجیره‌ای یک رکورد."""
    material = f"{previous_hash}:{canonical_payload(payload)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class AuditRecord(BaseModel):
    """یک رکورد حسابرسی تغییرناپذیر در زنجیره."""

    sequence = models.BigIntegerField(unique=True, db_index=True, verbose_name=_("شمارهٔ ترتیبی"))
    action = models.CharField(
        max_length=64, choices=AuditAction.choices, db_index=True, verbose_name=_("کنش")
    )
    outcome = models.CharField(
        max_length=16,
        choices=AuditOutcome.choices,
        default=AuditOutcome.SUCCESS,
        db_index=True,
        verbose_name=_("نتیجه"),
    )
    actor_id = models.UUIDField(null=True, blank=True, db_index=True, verbose_name=_("شناسهٔ کنش‌گر"))
    actor_label = models.CharField(
        max_length=80, blank=True, default="", verbose_name=_("نمای کنش‌گر")
    )
    target_type = models.CharField(
        max_length=64, blank=True, default="", db_index=True, verbose_name=_("نوع هدف")
    )
    target_id = models.CharField(
        max_length=64, blank=True, default="", db_index=True, verbose_name=_("شناسهٔ هدف")
    )
    reason = models.TextField(blank=True, default="", verbose_name=_("دلیل (برای عملیات مدیریتی)"))
    metadata = models.JSONField(
        default=dict, blank=True, verbose_name=_("فراداده (بدون دادهٔ حساس)")
    )
    request_id = models.CharField(
        max_length=64, blank=True, default="", verbose_name=_("شناسهٔ درخواست")
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name=_("آی‌پی"))
    user_agent = models.CharField(
        max_length=300, blank=True, default="", verbose_name=_("عامل کاربر")
    )
    previous_hash = models.CharField(
        max_length=64, default=GENESIS_HASH, verbose_name=_("هش رکورد قبلی")
    )
    record_hash = models.CharField(
        max_length=64, unique=True, db_index=True, verbose_name=_("هش این رکورد")
    )

    class Meta:
        verbose_name = _("رکورد حسابرسی")
        verbose_name_plural = _("رکوردهای حسابرسی")
        ordering = ("sequence",)
        indexes = [
            models.Index(fields=["action", "created_at"], name="audit_action_time_idx"),
            models.Index(fields=["target_type", "target_id"], name="audit_target_idx"),
        ]

    def __str__(self) -> str:
        """نمای خوانا."""
        return f"#{self.sequence} {self.action} → {self.outcome}"

    def payload_for_hash(self) -> dict[str, Any]:
        """دادهٔ پایدارِ ورودی هش (بدون فیلدهای فنی که ممکن است تغییر کنند)."""
        return {
            "sequence": self.sequence,
            "action": self.action,
            "outcome": self.outcome,
            "actor_id": str(self.actor_id) if self.actor_id else None,
            "actor_label": self.actor_label,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "reason": self.reason,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def verify(self) -> bool:
        """بررسی درستی هش این رکورد."""
        return self.record_hash == compute_hash(
            previous_hash=self.previous_hash, payload=self.payload_for_hash()
        )


class AuditAnchor(BaseModel):
    """لنگر دوره‌ای زنجیره — برای اثبات بیرونی و کشف دستکاری گذشته.

    هر لنگر: شمارهٔ ترتیبی آخرین رکورد، هش آن رکورد، و امضای HMAC.
    نگه‌داری لنگرها در محل جدا (WORM/object storage) بازیابی گذشته را ممکن
    می‌کند حتی اگر کل جدول حسابرسی بازنویسی شود.
    """

    last_sequence = models.BigIntegerField(verbose_name=_("آخرین شمارهٔ ترتیبی"))
    last_hash = models.CharField(max_length=64, verbose_name=_("هش آخرین رکورد"))
    record_count = models.BigIntegerField(verbose_name=_("تعداد رکوردها"))
    signature = models.CharField(max_length=64, verbose_name=_("امضای HMAC"))
    anchored_at = models.DateTimeField(default=timezone.now, verbose_name=_("زمان لنگر"))

    class Meta:
        verbose_name = _("لنگر زنجیرهٔ حسابرسی")
        verbose_name_plural = _("لنگرهای زنجیره")
        ordering = ("-anchored_at",)
        get_latest_by = "anchored_at"

    def __str__(self) -> str:
        """نمای خوانا."""
        return f"anchor@{self.last_sequence} ({self.anchored_at:%Y-%m-%d %H:%M})"

    def payload_for_signature(self) -> dict[str, Any]:
        """دادهٔ ورودی امضا."""
        return {
            "last_sequence": self.last_sequence,
            "last_hash": self.last_hash,
            "record_count": self.record_count,
            "anchored_at": self.anchored_at.isoformat(),
        }
