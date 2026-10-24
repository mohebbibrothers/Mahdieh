"""تست هم‌توانی — «دوبار ارسال، یک‌بار اثر»."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
import pytest

from apps.core.errors import ConflictError
from apps.core.idempotency import (
    STALE_LOCK_SECONDS,
    IdempotencyKey,
    IdempotencyOutcome,
    execute_idempotent,
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


def test_failed_action_releases_key_so_retry_runs_again() -> None:
    """خطای دامنه بازپخش نمی‌شود: تلاش دوباره با همان کلید، تازه اجرا می‌شود (نه «در حال پردازش»)."""
    calls: list[int] = []

    def failing() -> tuple[int, object]:
        calls.append(1)
        raise ConflictError("سانس پر شد.", code="slot_conflict")

    with pytest.raises(ConflictError):
        execute_idempotent(
            key="req-fail",
            scope="reservation.create",
            actor_ref="u-1",
            payload={"u": 1},
            action=failing,
        )
    assert not IdempotencyKey.all_objects.filter(key="req-fail").exists()

    outcome = execute_idempotent(
        key="req-fail",
        scope="reservation.create",
        actor_ref="u-1",
        payload={"u": 1},
        action=lambda: (201, {"code": "M-1"}),
    )
    assert (outcome.status, outcome.replayed, len(calls)) == (201, False, 1)


def test_key_of_another_actor_never_replays_their_response() -> None:
    """همان کلید و همان بدنه از کاربر دیگر ⇒ تعارض؛ پاسخ ذخیره‌شدهٔ کاربر نخست نشت نمی‌کند."""
    _, record = reserve_key(
        key="req-own", scope="reservation.create", actor_ref="u-1", payload={"u": 1}
    )
    assert record is not None
    store_result(record, status=201, body={"code": "M-SECRET"})
    outcome, _ = reserve_key(
        key="req-own", scope="reservation.create", actor_ref="u-2", payload={"u": 1}
    )
    assert outcome is IdempotencyOutcome.CONFLICT


def test_abandoned_lock_is_reclaimed_but_fresh_lock_is_respected() -> None:
    """قفل تازه ⇒ «در حال پردازش»؛ قفل رهاشده (فرایند مُرده) ⇒ دوباره قابل‌تصاحب."""
    _, record = reserve_key(
        key="req-lock", scope="reservation.create", actor_ref="u-1", payload={"u": 1}
    )
    assert record is not None
    outcome, _ = reserve_key(
        key="req-lock", scope="reservation.create", actor_ref="u-1", payload={"u": 1}
    )
    assert outcome is IdempotencyOutcome.IN_PROGRESS

    IdempotencyKey.all_objects.filter(pk=record.pk).update(
        locked_at=timezone.now() - timedelta(seconds=STALE_LOCK_SECONDS + 5)
    )
    outcome, again = reserve_key(
        key="req-lock", scope="reservation.create", actor_ref="u-1", payload={"u": 1}
    )
    assert outcome is IdempotencyOutcome.RESERVED
    assert again is not None
    assert again.pk == record.pk
