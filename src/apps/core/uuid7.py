"""تولید شناسهٔ UUIDv7 (RFC 9562) — مرتب با زمان، امن، و یکنوا در فرآیند.

چرا UUIDv7؟ چرا نه UUIDv4؟
    • مرتب‌بودن زمانی، درج‌ها را در انتهای درخت ایندکس B-Tree می‌نشاند
      (locality) → ایندکس کوچک‌تر و کارآمدتر از UUIDv4 تصادفی.
    • غیرقابل‌حدس بودن → شمارش رزروها و شناسهٔ کاربران افشا نمی‌شود.
    • آمادهٔ آیندهٔ چند-منبعی (چند شعبه/ادغام سامانه‌ها).

پیاده‌سازی: ۴۸ بیت زمان (میلی‌ثانیه)، ۴ بیت نسخه، ۱۲ بیت شمارندهٔ یکنوا،
۲ بیت گونه، ۶۲ بیت تصادفی. اگر ساعت به عقب برگردد یا چند فراخوانی در یک
میلی‌ثانیه رخ دهد، شمارنده تضمین ترتیب می‌کند.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

_VERSION = 0x7
_VARIANT_RFC4122 = 0b10
_RAND_A_MAX = 0xFFF  # 12 بیت
_LOCK = threading.Lock()
_last_ms: int = -1
_counter: int = 0


def _now_ms() -> int:
    """زمان یونیکس به میلی‌ثانیه (عدد صحیح)."""
    return int(time.time() * 1000)


def uuid7() -> uuid.UUID:
    """ساخت یک UUIDv7 یکنوا و ایمن برای استفاده به‌عنوان کلید اصلی."""
    global _last_ms, _counter  # noqa: PLW0603 - وضعیت مشترک عمدی برای یکنوایی

    with _LOCK:
        now_ms = _now_ms()
        if now_ms > _last_ms:
            _last_ms = now_ms
            _counter = int.from_bytes(os.urandom(2), "big") & 0x0FFF
        else:
            # ساعت ثابت مانده یا به عقب رفته ⇒ شمارنده ترتیب را حفظ می‌کند
            now_ms = _last_ms
            _counter += 1
            if _counter > _RAND_A_MAX:
                # سرریز شمارنده در یک میلی‌ثانیه: یک میلی‌ثانیه جلو می‌رویم
                _last_ms += 1
                now_ms = _last_ms
                _counter = 0

        rand_b = int.from_bytes(os.urandom(8), "big") >> 2  # ۶۲ بیت

        value = (now_ms & 0xFFFFFFFFFFFF) << 80
        value |= _VERSION << 76
        value |= (_counter & _RAND_A_MAX) << 64
        value |= _VARIANT_RFC4122 << 62
        value |= rand_b

        return uuid.UUID(int=value)


def uuid7_hex() -> str:
    """نمای رشته‌ای بدون خط تیره — مناسب کلید کش و شناسهٔ درخواست."""
    return uuid7().hex


def uuid7_time_ms(value: uuid.UUID) -> int:
    """استخراج زمان (میلی‌ثانیهٔ یونیکس) از یک UUIDv7 — برای مرتب‌سازی و ممیزی."""
    return value.int >> 80
