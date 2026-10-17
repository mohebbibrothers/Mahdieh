"""پاسخ شرطی (ETag / 304) برای خواندنی‌های پرتکرار — تقویم و پیکربندی.

چرا خودمان و نه ``django.views.decorators.http.condition``؟ چون ETag ما از
«نسخهٔ کش» (epoch) ساخته می‌شود، نه از بدنهٔ پاسخ: محاسبهٔ تگ هیچ کوئری‌ای
نمی‌زند و در حالت ۳۰۴ حتی ماتریس موجودی هم ساخته نمی‌شود. فرانت (Next) با
``If-None-Match`` می‌پرسد و در اکثر بازدیدها فقط چند بایت جواب می‌گیرد.

تگ‌ها «ضعیف» (``W/``) هستند: هم‌ارزی معنایی تضمین می‌شود نه بایت‌به‌بایت —
فشرده‌سازی nginx بدنه را عوض می‌کند و تگ قوی را بی‌اعتبار می‌کرد.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import hashlib
from typing import Any, Final

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

PUBLIC_REVALIDATE: Final[str] = "public, max-age=0, must-revalidate"
PRIVATE_REVALIDATE: Final[str] = "private, max-age=0, must-revalidate"


def weak_etag(*parts: Any) -> str:
    """ساخت ETag ضعیف و کوتاه از اجزای نسخه (ترتیب مهم است)."""
    raw = "|".join(str(part) for part in parts)
    digest = hashlib.blake2s(raw.encode("utf-8"), digest_size=12).hexdigest()
    return f'W/"{digest}"'


def _candidates(header: str) -> Iterable[str]:
    """تجزیهٔ ``If-None-Match`` به فهرست تگ‌ها (با پشتیبانی از ``*``)."""
    return (token.strip() for token in header.split(",") if token.strip())


def matches(request: Request, etag: str) -> bool:
    """آیا کلاینت همین نسخه را دارد؟ (مقایسهٔ ضعیف طبق RFC 9110 §13.1.2)."""
    header = request.headers.get("If-None-Match", "")
    if not header:
        return False
    bare = etag.removeprefix("W/")
    return any(tag == "*" or tag.removeprefix("W/") == bare for tag in _candidates(header))


def conditional_response(
    request: Request,
    *,
    etag: str,
    build: Callable[[], Any],
    cache_control: str = PUBLIC_REVALIDATE,
    vary: str = "Cookie",
) -> Response:
    """پاسخ ۳۰۴ اگر تگ هم‌خوان است، وگرنه ساخت بدنه با ``build`` و ۲۰۰."""
    if matches(request, etag):
        response = Response(status=status.HTTP_304_NOT_MODIFIED)
    else:
        response = Response(build())
    response["ETag"] = etag
    response["Cache-Control"] = cache_control
    if vary:
        response["Vary"] = vary
    return response
