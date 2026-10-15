"""تست زنجیرهٔ حسابرسی — عدم‌انکار و کشف دستکاری."""

from __future__ import annotations

import pytest

from apps.audit.choices import AuditAction
from apps.audit.models import AuditAnchor, AuditRecord
from apps.audit.services import (
    AuditEntry,
    create_anchor,
    latest_sequence,
    record,
    verify_anchor,
    verify_chain,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _entry(action: str, **kwargs: object) -> AuditEntry:
    """ساخت ورودی حسابرسی با مقادیر پیش‌فرض تست."""
    return AuditEntry(action=action, **kwargs)  # type: ignore[arg-type]


def test_chain_is_built_and_verified() -> None:
    """زنجیره پس از چند رکورد سالم و پیوسته است."""
    record(_entry(AuditAction.USER_REGISTERED, target_type="user", target_id="u-1"))
    record(_entry(AuditAction.PROFILE_COMPLETED, target_type="user", target_id="u-1"))
    record(_entry(AuditAction.LOGIN_SUCCEEDED, actor_label="زائر نمونه"))

    assert AuditRecord.all_objects.count() == 3
    assert latest_sequence() == 3
    valid, broken = verify_chain()
    assert valid is True
    assert broken is None


def test_sequence_and_hash_are_chained() -> None:
    """هر رکورد هش رکورد قبلی را در خود دارد."""
    first = record(_entry(AuditAction.USER_REGISTERED))
    second = record(_entry(AuditAction.LOGIN_SUCCEEDED))
    assert second.sequence == first.sequence + 1
    assert second.previous_hash == first.record_hash
    assert first.verify() and second.verify()


def test_tampering_is_detected() -> None:
    """دستکاری یک رکورد میانی باید زنجیره را بشکند."""
    record(_entry(AuditAction.USER_REGISTERED, metadata={"city": "مشهد"}))
    target = record(_entry(AuditAction.LOGIN_SUCCEEDED, metadata={"city": "تهران"}))
    record(_entry(AuditAction.SESSION_REVOKED))

    # دستکاری خاموش: تغییر فراداده بدون بازمحاسبهٔ هش
    AuditRecord.all_objects.filter(pk=target.pk).update(metadata={"city": "اهر"})

    valid, broken = verify_chain()
    assert valid is False
    assert broken == target.sequence


def test_management_actions_require_reason() -> None:
    """عملیات مدیریتی بدون دلیل ثبت نمی‌شود (قاعدهٔ I14)."""
    entry = _entry(AuditAction.RESERVATION_MOVED, target_type="reservation", target_id="r-1")
    with pytest.raises(ValueError, match="دلیل"):
        record(entry)

    # با دلیل، ثبت می‌شود
    ok = _entry(
        AuditAction.RESERVATION_MOVED,
        target_type="reservation",
        target_id="r-1",
        reason="درخواست تلفنی مهمان برای واحد طبقهٔ پایین",
    )
    assert record(ok).reason


def test_anchor_signature_and_verification() -> None:
    """لنگر باید امضاشده و امضایش قابل‌بررسی باشد."""
    record(_entry(AuditAction.USER_REGISTERED))
    anchor = create_anchor()
    assert anchor.last_sequence == 1
    assert anchor.record_count == 1
    assert verify_anchor(anchor) is True

    AuditAnchor.all_objects.filter(pk=anchor.pk).update(last_hash="0" * 64)
    tampered = AuditAnchor.all_objects.get(pk=anchor.pk)
    assert verify_anchor(tampered) is False
