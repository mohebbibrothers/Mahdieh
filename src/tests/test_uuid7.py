"""تست شناسهٔ UUIDv7 — نسخه، گونه، یکنوایی و یکتایی زیر همزمانی."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest

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
def test_hex_helper_shape() -> None:
    """نمای هگز باید ۳۲ نویسه و بدون خط تیره باشد."""
    text = uuid7_hex()
    assert len(text) == 32
    assert "-" not in text
    assert text.isalnum()
