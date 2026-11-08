"""پین نشانی دامنهٔ خود سایت به یک IP — برای پایش «از درون همان میزبان».

چرا؟ در برخی میزبان‌های اشتراکی، DNS درونی سرور دامنهٔ سایت را به IP داخلیِ بسته‌ای
می‌برد (یافتهٔ واقعی پس از انتقال به rv8.ir: ‎10.10.34.35‎، بی پاسخ روی ۸۰/۴۴۳)، در حالی
که بازدیدکننده‌ها از IP عمومی می‌رسند. پایشگر سلامت در این حالت «وقت تمام شد» می‌بیند و
هشدار دروغ می‌دهد. راه استاندارد همان ``curl --resolve`` است: فقط **برای همان دامنه**
اتصال به IP عمومی برود؛ نام میزبان، SNI و راستی‌آزمایی گواهی TLS دست‌نخورده می‌مانند
(پس امنیت TLS کم نمی‌شود و دامنه‌های دیگر هم اثر نمی‌پذیرند).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import ipaddress
import socket
from typing import Any, cast


def normalize_pin(raw: str) -> str:
    """IP پین (بی کروشه) یا رشتهٔ تهی؛ مقدار نامعتبر ⇒ ``ValueError`` (نه حدس)."""
    value = (raw or "").strip().strip("[]")
    if not value:
        return ""
    return str(ipaddress.ip_address(value))


@contextmanager
def pinned_host(host: str | None, ip: str) -> Iterator[None]:
    """درون این بلوک، ``getaddrinfo`` برای ``host`` (و فقط آن) به ``ip`` پاسخ می‌دهد."""
    target = (host or "").strip().lower().rstrip(".")
    pin = normalize_pin(ip)
    if not target or not pin:
        yield
        return
    original = socket.getaddrinfo

    def resolve(name: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        """همان ``getaddrinfo``؛ فقط نام پین‌شده به IP عمومی می‌رود."""
        if isinstance(name, str) and name.lower().rstrip(".") == target:
            return original(pin, port, *args, **kwargs)
        return original(name, port, *args, **kwargs)

    socket.getaddrinfo = cast("Any", resolve)
    try:
        yield
    finally:
        socket.getaddrinfo = original
