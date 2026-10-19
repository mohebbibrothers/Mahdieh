"""سریال‌سازی زیر بار هم‌زمان واقعی — زنجیرهٔ حسابرسی و شمارندهٔ کد رزرو.

هر دو مسیر «مقدار بعدی» می‌سازند. روی MariaDB، قفلِ ``SELECT … FOR UPDATE`` روی
آخرین ردیف (یا جدول خالی) سریال‌سازی را تضمین نمی‌کند: پرس‌وجوی منتظر پس از
آزادشدن قفل، ردیف تازه‌درج‌شده را نمی‌بیند و همان شماره را دوباره می‌سازد. این
تست‌ها با چند اتصال مستقل و یک خط شروع مشترک، همین را می‌آزمایند.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import threading
from typing import Any

from django.db import connections, transaction
from django.db.transaction import TransactionManagementError
from django.utils import timezone
import pytest

from apps.audit.choices import AuditAction
from apps.audit.models import AuditRecord
from apps.audit.services import AuditEntry, record, verify_chain
from apps.core.db.serial import serialize_on
from apps.core.models import SerialLock
from apps.reservation.codes import next_reservation_code
from apps.reservation.models import CodeSequence

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]

WORKERS = 8
ROUNDS = 4
TIMEOUT_SECONDS = 60


def _hammer(target: Callable[[], Any]) -> list[tuple[str, Any]]:
    """``WORKERS`` ریسمان، هر کدام ``ROUNDS`` بار، با شروع هم‌زمان."""
    barrier = threading.Barrier(WORKERS)
    results: list[tuple[str, Any]] = []
    guard = threading.Lock()

    def worker() -> None:
        barrier.wait(timeout=TIMEOUT_SECONDS)
        try:
            for _ in range(ROUNDS):
                try:
                    with transaction.atomic():
                        value = target()
                    outcome: tuple[str, Any] = ("ok", value)
                except Exception as exc:
                    outcome = ("error", repr(exc))
                with guard:
                    results.append(outcome)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=TIMEOUT_SECONDS)
    return results


def test_audit_chain_stays_gapless_under_concurrent_writers() -> None:
    """۳۲ رکورد هم‌زمان از جدول خالی: همه ثبت، شماره‌ها ۱…۳۲ و زنجیره سالم."""
    results = _hammer(
        lambda: (
            record(AuditEntry(action=AuditAction.LOGIN_SUCCEEDED, actor_label="stress")).sequence
        )
    )

    errors = [value for kind, value in results if kind == "error"]
    assert not errors, errors
    total = WORKERS * ROUNDS
    assert sorted(value for _, value in results) == list(range(1, total + 1))
    assert AuditRecord.all_objects.count() == total
    assert verify_chain() == (True, None)


def test_reservation_codes_stay_unique_under_concurrent_first_use() -> None:
    """نخستین کدهای سال، هم‌زمان: بدون بن‌بست، بدون تکرار، بدون شکاف."""
    results = _hammer(next_reservation_code)

    errors = [value for kind, value in results if kind == "error"]
    assert not errors, errors
    numbers = sorted(int(str(value).rsplit("-", 1)[1]) for _, value in results)
    assert numbers == list(range(1, WORKERS * ROUNDS + 1))
    sequence = CodeSequence.objects.get()
    assert sequence.last_value == WORKERS * ROUNDS
    assert abs(sequence.updated_at - timezone.now()) < timedelta(minutes=1)


def test_serialize_on_refuses_to_run_outside_a_transaction() -> None:
    """بیرون از تراکنش، قفل بلافاصله آزاد می‌شد؛ پس خطای صریح، نه امنیت کاذب."""
    with pytest.raises(TransactionManagementError):
        serialize_on("audit.chain")


@pytest.mark.parametrize("name", ["", "x" * 65])
def test_serialize_on_validates_the_lock_name(name: str) -> None:
    with transaction.atomic(), pytest.raises(ValueError, match="نام قفل"):
        serialize_on(name)


def test_serialize_on_creates_the_row_once_and_reuses_it() -> None:
    for _ in range(3):
        with transaction.atomic():
            serialize_on("stress.reuse")
    assert list(SerialLock.objects.values_list("name", flat=True)) == ["stress.reuse"]
