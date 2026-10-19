"""کش جدولی تولید (``AtomicDatabaseCache``) روی MariaDB واقعی — ADR-0021.

آزمون‌های عمومی با کش لوکال‌مموری اجرا می‌شوند (ر.ک. ``config.settings.test``)؛
این فایل همان پیکربندی تولید را روشن می‌کند و سه قول اصلی را می‌سنجد:

1. ``incr``/``decr`` اتمیک‌اند — هشت ریسمان هم‌زمان حتی یک افزایش را گم نمی‌کنند؛
2. قرارداد کش جنگو حفظ شده است: کلید ناموجود یا منقضی ⇒ ``ValueError``؛
3. نوشتن کش روی اتصال جداگانه است و با برگشت تراکنش کسب‌وکار **برنمی‌گردد**
   (شمارندهٔ تلاش ناموفق MFA نباید با خطای درخواست پاک شود).

جدول کش با ``createcachetable`` ساخته می‌شود؛ DDL در MariaDB تراکنش را ضمنی
commit می‌کند، پس این آزمون‌ها تراکنشی (``transaction=True``) هستند.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
import threading

from django.core.cache import cache, caches
from django.core.management import call_command
from django.db import connections, transaction
from django.test import override_settings
from django.utils import timezone
import pytest
import time_machine

from apps.core.cache import bump_epoch, get_epoch
from apps.core.db.cache import AtomicDatabaseCache

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "cache"]),
    pytest.mark.integration,
]

TABLE = "test_atomic_cache"
DB_CACHES = {
    "default": {
        "BACKEND": "apps.core.db.cache.AtomicDatabaseCache",
        "LOCATION": TABLE,
        "KEY_PREFIX": "t",
        "TIMEOUT": 300,
    }
}
THREADS = 8
INCREMENTS_PER_THREAD = 25


@pytest.fixture
def db_cache() -> Iterator[AtomicDatabaseCache]:
    """پیکربندی تولیدی کش + جدول تازه؛ در پایان جدول حذف می‌شود."""
    with override_settings(CACHES=DB_CACHES):
        call_command("createcachetable", database="default", verbosity=0)
        backend = caches["default"]
        assert isinstance(backend, AtomicDatabaseCache)
        try:
            yield backend
        finally:
            with connections["default"].cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {TABLE}")


def test_cache_traffic_is_routed_to_the_cache_alias(db_cache: AtomicDatabaseCache) -> None:
    """مسیریاب: خواندن/نوشتن کش ⇒ اتصال ``cache``؛ مدل‌های برنامه دست نمی‌خورند."""
    from django.db import router

    from apps.identity.models import User

    assert router.db_for_write(db_cache.cache_model_class) == "cache"
    assert router.db_for_read(db_cache.cache_model_class) == "cache"
    assert router.db_for_write(User) == "default"
    assert router.allow_migrate("cache", "identity") is False


def test_set_get_and_atomic_counters(db_cache: AtomicDatabaseCache) -> None:
    """رفت‌وبرگشت مقدار و شمارندهٔ صحیح (مثبت و منفی)."""
    db_cache.set("greeting", {"fa": "سلام"})
    assert db_cache.get("greeting") == {"fa": "سلام"}

    assert db_cache.add("counter", 10) is True
    assert db_cache.add("counter", 99) is False
    assert db_cache.incr("counter") == 11
    assert db_cache.incr("counter", 5) == 16
    assert db_cache.decr("counter", 6) == 10
    assert db_cache.get("counter") == 10


def test_missing_or_expired_key_raises_value_error(db_cache: AtomicDatabaseCache) -> None:
    """قرارداد جنگو: ``incr`` روی کلید ناموجود/منقضی ⇒ ``ValueError``."""
    with pytest.raises(ValueError, match="not found"):
        db_cache.incr("absent")

    db_cache.set("short-lived", 1, timeout=10)
    later = timezone.now() + timedelta(seconds=60)
    with (
        time_machine.travel(later, tick=False),
        pytest.raises(ValueError, match="not found"),
    ):
        db_cache.incr("short-lived")


def test_concurrent_increments_never_lose_updates(db_cache: AtomicDatabaseCache) -> None:
    """۸ ریسمان × ۲۵ افزایش هم‌زمان ⇒ دقیقاً ۲۰۰ (قفل ردیفی، بی‌مسابقه)."""
    db_cache.set("hits", 0)
    barrier = threading.Barrier(THREADS)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            for _ in range(INCREMENTS_PER_THREAD):
                caches["default"].incr("hits")
        except BaseException as exc:  # pragma: no cover - فقط برای گزارش شکست
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, errors
    assert db_cache.get("hits") == THREADS * INCREMENTS_PER_THREAD


def test_cache_writes_survive_business_rollback(db_cache: AtomicDatabaseCache) -> None:
    """برگشت تراکنش ``default`` نوشتهٔ کش را پاک نمی‌کند (همان معنای Redis)."""
    db_cache.set("attempts", 0)
    with pytest.raises(RuntimeError), transaction.atomic(using="default"):
        db_cache.incr("attempts")
        db_cache.set("marker", "kept")
        msg = "شکست درخواست پس از نوشتن کش"
        raise RuntimeError(msg)
    assert db_cache.get("attempts") == 1
    assert db_cache.get("marker") == "kept"


def test_epoch_bump_is_monotonic_on_the_database_cache(db_cache: AtomicDatabaseCache) -> None:
    """نسخهٔ کش تقویم روی کش جدولی هم یکنوا بالا می‌رود (ابطال قطعی)."""
    assert cache is not None
    before = get_epoch("availability", "public")
    first = bump_epoch("availability", "public")
    second = bump_epoch("availability", "public")
    assert first > before
    assert second == first + 1
    assert get_epoch("availability", "public") == second
