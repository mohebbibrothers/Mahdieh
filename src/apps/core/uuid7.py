"""تولید شناسهٔ UUIDv7 (RFC 9562) — مرتب با زمان، امن، و یکنوا در فرآیند.

چرا UUIDv7؟ چرا نه UUIDv4؟
    • مرتب‌بودن زمانی، درج‌ها را در انتهای درخت ایندکس B-Tree می‌نشاند
      (locality) → ایندکس کوچک‌تر و کارآمدتر از UUIDv4 تصادفی.
    • غیرقابل‌حدس بودن → شمارش رزروها و شناسهٔ کاربران افشا نمی‌شود.
    • آمادهٔ آیندهٔ چند-منبعی (چند شعبه/ادغام سامانه‌ها).

پیاده‌سازی: ۴۸ بیت زمان (میلی‌ثانیه)، ۴ بیت نسخه، ۱۲ بیت شمارندهٔ یکنوا،
۲ بیت گونه، ۶۲ بیت تصادفی. اگر چند فراخوانی در یک میلی‌ثانیه رخ دهد یا ساعت
کمی به عقب برود، شمارنده تضمین ترتیب می‌کند.

بازگشت بزرگ ساعت (همگام‌سازی NTP، بازگردانی ماشین مجازی، سفر زمانی در تست):
اگر ساعت بیش از ``_RESYNC_THRESHOLD_MS`` به عقب بپرد، تولیدکننده **به ساعت
دیوار هم‌گام می‌شود** تا شناسه‌ها برای همیشه در آینده گیر نکنند. در این حالت
یکنوایی مطلق قربانی می‌شود، ولی یکتایی (۶۲ بیت تصادفی + شمارندهٔ تازه) و
«نزدیکی به زمان حال» حفظ می‌شود — معاملهٔ درست برای کلید اصلی.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

_VERSION = 0x7
_VARIANT_RFC4122 = 0b10
_RAND_A_MAX = 0xFFF  # 12 بیت
_RESYNC_THRESHOLD_MS = 2_000  # بیش از این مقدار عقب‌رفتگی ⇒ هم‌گام‌سازی با ساعت دیوار
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
        elif _last_ms - now_ms > _RESYNC_THRESHOLD_MS:
            # ساعت واقعاً به عقب برگشته است؛ دنبال‌کردنِ گذشته یعنی گیرافتادن
            # همیشگی در برچسب زمانی آینده. به ساعت دیوار برمی‌گردیم.
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
