"""مبدأهای بی‌دامنه‌ای (CORS) — نرمال‌سازی و اعتبارسنجی، بی‌وابستگی به جنگو.

چرا جدا از تنظیمات؟ دو دلیل دقیق:
    1. تنظیمات جنگو **اجرا نمی‌شود مگر با بوت کامل**؛ منطق نرمال‌سازی که داخلش
       باشد، فقط با آزمون یکپارچه سنجیده می‌شود. این‌جا یک تابع خالص است و
       آزمون واحد مستقیم دارد.
    2. همین توابع در ``apps/core/checks.py`` هم استفاده می‌شوند؛ یک منبع حقیقت
       برای «مبدأ معتبر چیست» — نه دو تعریف که روزی از هم جدا می‌افتند.

مبدأ معتبر = ``scheme://host[:port]`` — بدون مسیر، بدون پرس‌وجو، بدون اسلش پایانی.
مرورگر مبدأ را «scheme + host + port» می‌فرستد؛ اگر ما با اسلش یا مسیر مقایسه
کنیم، هیچ‌وقت برابر نمی‌شود و «دکمه‌ای که کار نمی‌کند» می‌سازیم.
"""

from __future__ import annotations

from collections.abc import Iterable
import re

ORIGIN_PATTERN = re.compile(r"^https?://[a-zA-Z0-9.\-]+(?::\d+)?$")


def parse_origins(raw: Iterable[str]) -> list[str]:
    """پاک‌سازی فهرست مبدأها: حذف فاصله، اسلش پایانی، خالی‌ها و تکرارها.

    ترتیب ورودی حفظ می‌شود تا لاگ/سند با آنچه مدیر نوشته یکی بماند.
    """
    cleaned: list[str] = []
    for item in raw:
        origin = str(item).strip().rstrip("/")
        if origin and origin not in cleaned:
            cleaned.append(origin)
    return cleaned


def is_valid_origin(origin: str) -> bool:
    """آیا قالب مبدأ درست است؟ (``*`` نامعتبر است — نه امن، نه لازم)"""
    return bool(ORIGIN_PATTERN.match(origin))
