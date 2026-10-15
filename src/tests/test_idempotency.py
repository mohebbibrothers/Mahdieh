"""تست هم‌توانی — «دوبار ارسال، یک‌بار اثر»."""

from __future__ import annotations

import pytest

from apps.core.idempotency import (
    IdempotencyKey,
    IdempotencyOutcome,
    fingerprint,
    reserve_key,
    store_result,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_first_request_reserves_key() -> None:
    """درخواست نخست کلید را رزرو می‌کند."""
    outcome, record = reserve_key(
        key="req-1", scope="reservation.create", actor_ref="u-1", payload={"unit": "A1"}
    )
    assert outcome is IdempotencyOutcome.RESERVED
    assert record is not None
    assert record.locked_at is not None


def test_replay_returns_stored_response() -> None:
    """درخواست تکراری باید همان پاسخ قبلی را بازپخش کند."""
    _, record = reserve_key(key="req-2", scope="reservation.create", payload={"unit": "A1"})
    assert record is not None
    store_result(record, status=201, body={"id": "abc"})

    outcome, existing = reserve_key(key="req-2", scope="reservation.create", payload={"unit": "A1"})
    assert outcome is IdempotencyOutcome.REPLAY
    assert existing is not None
    assert existing.response_status == 201
    assert existing.response_body == {"id": "abc"}


def test_same_key_with_different_payload_conflicts() -> None:
    """همان کلید با بدنهٔ متفاوت ⇒ تصادم (سوءاستفاده یا باگ کلاینت)."""
    reserve_key(key="req-3", scope="reservation.create", payload={"unit": "A1"})
    outcome, _ = reserve_key(key="req-3", scope="reservation.create", payload={"unit": "B2"})
    assert outcome is IdempotencyOutcome.CONFLICT


def test_scope_isolates_keys() -> None:
    """کلید در دامنه‌های مختلف مستقل است."""
    first, _ = reserve_key(key="shared", scope="scope.a")
    second, _ = reserve_key(key="shared", scope="scope.b")
    assert first is IdempotencyOutcome.RESERVED
    assert second is IdempotencyOutcome.RESERVED
    assert IdempotencyKey.all_objects.filter(key="shared").count() == 2


def test_fingerprint_is_stable_and_order_independent() -> None:
    """اثر انگشت باید مستقل از ترتیب کلیدها و پایدار باشد."""
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})
