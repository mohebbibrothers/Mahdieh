"""تست شناسهٔ UUIDv7 — نسخه، گونه، یکنوایی و یکتایی زیر همزمانی."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import uuid

from django.utils import timezone
import pytest
import time_machine

from apps.core.uuid7 import uuid7, uuid7_hex, uuid7_time_ms


@pytest.mark.unit
def test_version_and_variant() -> None:
    """بیت‌های نسخه (۷) و گونه (RFC 4122) درست تنظیم می‌شوند."""
    value = uuid7()
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


@pytest.mark.unit
def test_monotonic_within_same_millisecond() -> None:
    """چند شناسه در یک میلی‌ثانیه باید صعودی بمانند (ترتیب درج پایدار)."""
    generated = [uuid7() for _ in range(1000)]
    assert generated == sorted(generated), "UUIDv7 باید یکنوا باشد"
    assert len({str(value) for value in generated}) == 1000


@pytest.mark.unit
def test_uniqueness_under_threads() -> None:
    """تولید هم‌زمان در چند ریسمان نباید تصادم بدهد."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: uuid7(), range(4000)))
    assert len({str(value) for value in values}) == 4000


@pytest.mark.unit
def test_time_encoding_is_readable() -> None:
    """زمان استخراج‌شده باید به زمان حال نزدیک باشد."""
    import time

    now_ms = int(time.time() * 1000)
    value = uuid7()
    extracted = uuid7_time_ms(value)
    assert abs(extracted - now_ms) < 5000


@pytest.mark.unit
def test_clock_rewind_resyncs_to_wall_clock() -> None:
    """بازگشت بزرگ ساعت نباید شناسه‌ها را در آینده گیر بیندازد.

    سناریوی واقعی: همگام‌سازی NTP یا بازگردانی ماشین مجازی ساعت را ساعت‌ها به
    عقب می‌برد. اگر تولیدکننده «آخرین میلی‌ثانیه» را حفظ کند، هر شناسهٔ تازه
    برچسب زمانی آیندهٔ دروغین می‌گیرد و ایندکس‌ها/گزارش‌ها به‌هم می‌ریزند.
    """
    import time

    far_future = timezone.now() + timedelta(days=5)
    with time_machine.travel(far_future, tick=False):
        uuid7()  # ساعت به آیندهٔ دور می‌رود (مثل تست‌های دامنه‌ای)

    value = uuid7()  # ساعت برگشته است؛ باید با زمان واقعی هم‌گام شود
    extracted = uuid7_time_ms(value)
    assert abs(extracted - int(time.time() * 1000)) < 5000


@pytest.mark.unit
def test_small_clock_drift_keeps_monotonicity() -> None:
    """عقب‌رفتگی کوچک (زیر آستانه) ترتیب یکنوا را حفظ می‌کند."""
    from apps.core import uuid7 as module

    with time_machine.travel(timezone.now(), tick=False):
        first = uuid7()
        with module._LOCK:
            module._last_ms += 500  # ساعت ۵۰۰ میلی‌ثانیه «عقب» می‌رود
        second = uuid7()
    assert first < second, "یکنوایی در عقب‌رفتگی کوچک باید حفظ شود"


@pytest.mark.unit
def test_hex_helper_shape() -> None:
    """نمای هگز باید ۳۲ نویسه و بدون خط تیره باشد."""
    text = uuid7_hex()
    assert len(text) == 32
    assert "-" not in text
    assert text.isalnum()
