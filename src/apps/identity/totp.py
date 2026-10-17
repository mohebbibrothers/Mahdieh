"""TOTP (RFC 6238) و HOTP (RFC 4226) — پیاده‌سازی کوچک، خالص و آزموده‌شده.

چرا کتابخانهٔ بیرونی نه؟ الگوریتم چند خط است، وابستگی تازه سطح حمله و بار
به‌روزرسانی می‌افزاید، و این پیاده‌سازی با **بردارهای آزمون رسمی RFC** سنجیده
می‌شود (``tests/test_staff_mfa.py``). پارامترها همان پیش‌فرض همهٔ اپ‌های
احراز هویت (Google Authenticator، Microsoft Authenticator، Aegis، 1Password)
است: SHA-1، شش رقم، گام ۳۰ ثانیه.

این ماژول هیچ وابستگی به جنگو ندارد؛ زمان را از بیرون می‌گیرد تا قطعی و
آزمون‌پذیر بماند.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from typing import Final
from urllib.parse import quote, urlencode

DIGITS: Final[int] = 6
PERIOD_SECONDS: Final[int] = 30
SECRET_BYTES: Final[int] = 20  # ۱۶۰ بیت — توصیهٔ RFC 4226 بخش ۴
#: تحمل ساعت: یک گام پیش و پس (±۳۰ ثانیه) — تعادل امنیت و ساعت ناهماهنگ گوشی.
DEFAULT_WINDOW: Final[int] = 1


def generate_secret() -> str:
    """راز تصادفی رمزنگارانه در قالب Base32 بدون ``=`` (قالب استاندارد اپ‌ها)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _secret_bytes(secret_b32: str) -> bytes:
    """رمزگشایی Base32 با تحمل فاصله، حروف کوچک و نبود padding."""
    cleaned = secret_b32.replace(" ", "").upper()
    padding = "=" * (-len(cleaned) % 8)
    return base64.b32decode(cleaned + padding)


def hotp(key: bytes, counter: int, *, digits: int = DIGITS, digest: str = "sha1") -> str:
    """HOTP طبق RFC 4226 بخش ۵.۳ (کوتاه‌سازی پویا)."""
    mac = hmac.new(key, struct.pack(">Q", counter), getattr(hashlib, digest)).digest()
    offset = mac[-1] & 0x0F
    binary = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def time_step(unix_seconds: float, *, period: int = PERIOD_SECONDS) -> int:
    """شمارهٔ گام زمانی (T در RFC 6238) برای یک لحظهٔ یونیکس."""
    return int(unix_seconds // period)


def totp_at(secret_b32: str, unix_seconds: float, *, digits: int = DIGITS) -> str:
    """کد TOTP یک راز در یک لحظه."""
    return hotp(_secret_bytes(secret_b32), time_step(unix_seconds), digits=digits)


def match_step(
    secret_b32: str,
    code: str,
    *,
    unix_seconds: float,
    window: int = DEFAULT_WINDOW,
    last_used_step: int | None = None,
) -> int | None:
    """گامی که کد با آن می‌خواند — یا ``None``.

    • مقایسه در زمان ثابت (``hmac.compare_digest``) — بدون کانال جانبی زمانی.
    • گام‌های ``<= last_used_step`` رد می‌شوند: هر کد فقط یک بار (ضد بازپخش).
    """
    candidate = "".join(char for char in (code or "") if char.isdigit())
    if len(candidate) != DIGITS:
        return None
    key = _secret_bytes(secret_b32)
    current = time_step(unix_seconds)
    for step in range(current - window, current + window + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(hotp(key, step), candidate):
            return step
    return None


def provisioning_uri(secret_b32: str, *, account: str, issuer: str) -> str:
    """نشانی ``otpauth://`` برای QR — قالب Key URI گوگل (پذیرفتهٔ همهٔ اپ‌ها)."""
    label = quote(f"{issuer}:{account}", safe="")
    query = urlencode(
        {
            "secret": secret_b32,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": DIGITS,
            "period": PERIOD_SECONDS,
        },
        quote_via=quote,
    )
    return f"otpauth://totp/{label}?{query}"


def format_secret(secret_b32: str) -> str:
    """نمایش راز در گروه‌های چهارتایی برای ورود دستی (وقتی دوربین در دسترس نیست)."""
    return " ".join(secret_b32[index : index + 4] for index in range(0, len(secret_b32), 4))
