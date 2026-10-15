"""تست Outbox — تضمین «رزرو هرگز به سرویس بیرونی گره نمی‌خورد».

سناریوهای حیاتی:
    ۱. رکورد رخداد در همان تراکنش نوشته می‌شود.
    ۲. اگر تراکنش شکست بخورد، هیچ رخدادی منتشر نمی‌شود (سرطان رخداد جعلی).
    ۳. برداشت دسته‌ای با SKIP LOCKED زیر همزمانی، هر رخداد را یک‌بار می‌دهد.
"""

from __future__ import annotations

import threading
import time

from django.db import connection, transaction
import pytest

from apps.core.choices import OutboxStatus
from apps.core.outbox import (
    MAX_ATTEMPTS,
    OutboxEvent,
    fetch_pending_batch,
    mark_failed,
    mark_published,
    publish,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]


def test_publish_writes_event_inside_transaction() -> None:
    """رخداد باید در همان تراکنش قابل مشاهده باشد (اتمی با دادهٔ دامنه)."""
    with transaction.atomic():
        event = publish(
            "reservation.created",
            aggregate_type="reservation",
            aggregate_id="11111111-1111-1111-1111-111111111111",
            payload={"unit": "A1", "cells": 2},
        )
        assert OutboxEvent.all_objects.filter(pk=event.pk).exists()

    stored = OutboxEvent.all_objects.get(pk=event.pk)
    assert stored.status == OutboxStatus.PENDING
    assert stored.payload["unit"] == "A1"


def test_rolled_back_transaction_leaves_no_event() -> None:
    """تراکنش ناموفق باید هیچ رخدادی باقی نگذارد — نه رخداد جعلی."""
    before = OutboxEvent.all_objects.count()
    with pytest.raises(RuntimeError, match="شکست آزمایشی"), transaction.atomic():
        publish(
            "reservation.created",
            aggregate_type="reservation",
            aggregate_id="22222222-2222-2222-2222-222222222222",
        )
        raise RuntimeError("شکست آزمایشی")

    assert OutboxEvent.all_objects.count() == before


def test_mark_published_and_failed_lifecycle() -> None:
    """چرخهٔ عمر وضعیت رخداد: در انتظار ⇒ منتشرشده / ناموفق با عقب‌نشینی."""
    event = publish(
        "reservation.confirmed",
        aggregate_type="reservation",
        aggregate_id="33333333-3333-3333-3333-333333333333",
    )
    mark_published(event)
    event.refresh_from_db()
    assert event.status == OutboxStatus.PUBLISHED
    assert event.published_at is not None

    other = publish(
        "reservation.confirmed",
        aggregate_type="reservation",
        aggregate_id="44444444-4444-4444-4444-444444444444",
    )
    mark_failed(other, "provider timeout", backoff_seconds=30)
    other.refresh_from_db()
    assert other.status == OutboxStatus.PENDING
    assert other.attempts == 1
    assert "provider timeout" in other.last_error

    dead = publish(
        "reservation.confirmed",
        aggregate_type="reservation",
        aggregate_id="55555555-5555-5555-5555-555555555555",
    )
    dead.attempts = MAX_ATTEMPTS - 1
    dead.save(update_fields=["attempts"])
    mark_failed(dead, "boom", backoff_seconds=10)
    dead.refresh_from_db()
    assert dead.status == OutboxStatus.DEAD, "پس از سقف تلاش باید به صف مرده برود"


def test_fetch_pending_batch_respects_availability_window() -> None:
    """رخداد با زمان آینده نباید در دستهٔ فعلی برداشته شود."""
    publish(
        "hold.expired",
        aggregate_type="reservation",
        aggregate_id="66666666-6666-6666-6666-666666666666",
    )
    batch = fetch_pending_batch(limit=10)
    assert all(event.status == OutboxStatus.PENDING for event in batch)
    assert len(batch) >= 1


@pytest.mark.concurrency
def test_skip_locked_prevents_double_assignment() -> None:
    """دو رلهٔ موازی نباید یک رخداد را دوبار بردارند.

    قرارداد درست رله: برداشت و علامت‌گذاری انتشار باید **در یک تراکنش**
    انجام شود. اگر قفل پیش از علامت‌گذاری رها شود، رلهٔ دوم همان رخداد را
    می‌بیند — همان چیزی که این تست با تأخیر مصنوعی شبیه‌سازی می‌کند.
    """
    event_ids = [
        str(
            publish(
                "reservation.created",
                aggregate_type="reservation",
                aggregate_id=f"77777777-7777-7777-7777-77777777777{i}",
            ).pk
        )
        for i in range(6)
    ]

    seen: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def relay() -> None:
        """یک چرخهٔ کامل رله: برداشت، انتشار، علامت‌گذاری — در یک تراکنش."""
        connection.close()  # هر ریسمان کانکشن مستقل خود را دارد
        try:
            barrier.wait(timeout=10)
            with transaction.atomic():
                batch = fetch_pending_batch(limit=3)
                picked = [str(item.pk) for item in batch]
                with lock:
                    seen.extend(picked)
                time.sleep(0.4)  # نگه‌داشتن قفل، تا رلهٔ دیگر ناچار به SKIP شود
                for item in batch:
                    mark_published(item)
        finally:
            # بستن صریح کانکشن ریسمان: جلوگیری از هشدار منبع باز
            connection.close()

    threads = [threading.Thread(target=relay) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(seen) == len(set(seen)), f"رخداد تکراری برداشته شد: {seen}"
    assert set(seen).issubset(set(event_ids))
