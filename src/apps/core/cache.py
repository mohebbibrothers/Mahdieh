"""کش نسخه‌دار بر پایهٔ Epoch — ابطال اتمیک بدون پیمایش کلید.

مسئله: تقویم موجودی باید تازه باشد، ولی پرترافیک است. حذف کلیدبه‌کلید
شکننده است (یک کلید جا می‌ماند ⇒ دادهٔ کهنه به کاربر نشان داده می‌شود).

راه‌حل: هر نام‌فضا یک شمارندهٔ نسخه دارد که در کلید کش می‌آید:
    ``availability:unit:<id>:v<epoch>:2026-10-20``
هر تغییر ⇒ ``INCR epoch`` ⇒ همهٔ کلیدهای قدیمی بی‌اثر می‌شوند، بدون حذف.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any, Final

from django.core.cache import cache

logger = logging.getLogger(__name__)

EPOCH_TTL_SECONDS: Final[int] = 60 * 60 * 24 * 30  # نسخه‌ها ۳۰ روز زنده‌اند


def epoch_key(namespace: str, scope: str) -> str:
    """کلید شمارندهٔ نسخه یک نام‌فضا/دامنه."""
    return f"epoch:{namespace}:{scope}"


def _safe_get(key: str) -> Any | None:
    """خواندن ایمن از کش — قطعی کش هرگز درخواست کاربر را نمی‌شکند.

    قاعدهٔ پایداری: کش یک **بهینه‌سازی** است، نه منبع حقیقت. اگر Valkey/Redis
    بخوابد، سایت باید کندتر اما سالم بماند؛ پس خطای کش فقط لاگ می‌شود و
    ``None`` برمی‌گردد تا مسیر ساخت داده از دیتابیس اجرا شود.
    """
    try:
        return cache.get(key)
    except Exception:
        logger.warning("cache_read_failed", extra={"key": key})
        return None


def _safe_set(key: str, value: Any, timeout: int | None) -> bool:
    """نوشتن ایمن در کش — نبودِ کش، نتیجهٔ درخواست را عوض نمی‌کند."""
    try:
        cache.set(key, value, timeout)
    except Exception:
        logger.warning("cache_write_failed", extra={"key": key})
        return False
    return True


def get_epoch(namespace: str, scope: str) -> int:
    """خواندن شمارندهٔ نسخه (پیش‌فرض ۱ اگر وجود نداشته باشد)."""
    value = _safe_get(epoch_key(namespace, scope))
    return int(value) if value is not None else 1


def bump_epoch(namespace: str, scope: str) -> int:
    """افزایش اتمیک نسخه — همهٔ کلیدهای کش قبلی بی‌اثر می‌شوند."""
    key = epoch_key(namespace, scope)
    try:
        client = _raw_client()
        if client is not None:
            new_value = client.incr(key)
            client.expire(key, EPOCH_TTL_SECONDS)
            return int(new_value)
    except Exception:  # pragma: no cover - کش هرگز نباید جریان اصلی را بشکند
        logger.warning("epoch_increment_failed", extra={"namespace": namespace, "scope": scope})

    current = get_epoch(namespace, scope) + 1
    _safe_set(key, current, EPOCH_TTL_SECONDS)
    return current


def _raw_client() -> Any | None:
    """دسترسی به کلاینت خام Valkey/Redis برای عملیات اتمیک."""
    try:
        from django_redis import get_redis_connection

        return get_redis_connection("default")
    except Exception:  # pragma: no cover
        return None


def versioned_key(namespace: str, scope: str, suffix: str) -> str:
    """ساخت کلید کش نسخه‌دار."""
    return f"{namespace}:{scope}:v{get_epoch(namespace, scope)}:{suffix}"


def get_or_set_versioned[T](
    namespace: str,
    scope: str,
    suffix: str,
    producer: Callable[[], T],
    *,
    timeout: int | None = None,
) -> T:
    """خواندن از کش با نسخه، و در صورت نبود، ساخت و ذخیره."""
    key = versioned_key(namespace, scope, suffix)
    cached = _safe_get(key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    # کش خالی یا کش نخوابیده: داده از منبع حقیقت ساخته می‌شود.
    value = producer()
    _safe_set(key, value, timeout)
    return value


def invalidate(namespace: str, scope: str) -> int:
    """ابطال همهٔ داده‌های کش یک دامنه با یک عمل."""
    return bump_epoch(namespace, scope)


def reset_epochs_for_tests() -> None:
    """پاک‌سازی نسخه‌ها — فقط برای تست."""
    client = _raw_client()
    if client is None:
        return
    for key in client.scan_iter(match="*epoch:*", count=500):
        client.delete(key)
