"""سدِ ظرفیت (Load shedding) در لایهٔ ASGI — پاسخ سریع به جای صفِ بی‌کران.

مسئله: وقتی ترافیک از ظرفیت پردازش بیشتر شود، صف‌کردنِ بی‌حد بدتر از ردکردنِ
محترمانه است. صفِ بی‌کران یعنی تأخیر فزاینده برای همه، اشغال حافظه، و در
نهایت اتمام مهلتِ همهٔ درخواست‌ها («آبشاری»). سدِ ظرفیت، بارِ مازاد را در
**درِ ورودی** با ۵۰۳ و ``Retry-After`` برمی‌گرداند تا کاربران داخل سامانه
سریع بمانند و بیرون، پیام روشن بگیرند.

طراحی:
    • شمارندهٔ درخواست‌های در جریان با ``asyncio`` (بدون قفل؛ تک‌رشته‌ای است).
    • درِ ورودی، مسیرهای سلامت و دارایی ایستا **هرگز** سد نمی‌شوند؛ اگر
      سامانه در فشار باشد، نباید پایشگر و CSS هم بیفتند.
    • پیش‌فرض سخاوتمندانه (۲۴۰ درخواست هم‌زمان در هر کارگر) تا در حالت عادی
      هرگز فعال نشود؛ این یک شیر ایمنی است، نه دروازهٔ روزمره.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
import logging
from typing import Any

logger = logging.getLogger(__name__)

ASGIScope = dict[str, Any]
ASGIReceive = Callable[[], Awaitable[Mapping[str, Any]]]
ASGISend = Callable[[Mapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[ASGIScope, ASGIReceive, ASGISend], Awaitable[None]]

#: مسیرهایی که هرگز سد نمی‌شوند (پایش و دارایی ایستا باید همیشه پاسخ بگیرند).
DEFAULT_EXEMPT_PREFIXES: tuple[str, ...] = ("/health/", "/static/", "/media/", "/favicon.ico")

RETRY_AFTER_SECONDS = 2


class ConcurrencyLimitMiddleware:
    """سقف تعداد درخواست‌های هم‌زمان در هر فرآیند؛ فراتر از آن ⇒ ۵۰۳ سریع."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_concurrency: int = 240,
        exempt_prefixes: Iterable[str] = DEFAULT_EXEMPT_PREFIXES,
    ) -> None:
        """آماده‌سازی سد با سقف و استثناها."""
        if max_concurrency < 1:
            msg = "max_concurrency باید دست‌کم ۱ باشد."
            raise ValueError(msg)
        self.app = app
        self.max_concurrency = max_concurrency
        self.exempt_prefixes = tuple(exempt_prefixes)
        self.in_flight = 0
        self.shed_total = 0  # شمارندهٔ سنجش‌پذیر: چند درخواست را خودمان برگرداندیم

    def _path(self, scope: ASGIScope) -> str:
        """مسیر درخواست از دامنهٔ ASGI."""
        return str(scope.get("path", ""))

    def _is_exempt(self, path: str) -> bool:
        """آیا این مسیر از سد معاف است؟"""
        return path.startswith(self.exempt_prefixes)

    async def __call__(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """اجرای درخواست زیر سقف ظرفیت یا پاسخ ۵۰۳ سریع."""
        if scope.get("type") != "http" or self._is_exempt(self._path(scope)):
            await self.app(scope, receive, send)
            return

        if self.in_flight >= self.max_concurrency:
            self.shed_total += 1
            logger.warning(
                "load_shed",
                extra={
                    "path": self._path(scope),
                    "in_flight": self.in_flight,
                    "max_concurrency": self.max_concurrency,
                    "shed_total": self.shed_total,
                },
            )
            await self._service_unavailable(send)
            return

        self.in_flight += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.in_flight -= 1

    async def _service_unavailable(self, send: ASGISend) -> None:
        """پاسخ ۵۰۳ با بدنهٔ کوتاه و هدر ``Retry-After``."""
        body = (
            '{"type":"https://docs.mahdieh.ir/problems/service_unavailable",'
            '"title":"ترافیک بالا","status":503,"code":"service_unavailable",'
            '"detail":"همین حالا درخواست‌ها زیاد است؛ چند لحظه بعد دوباره تلاش کنید."}'
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/problem+json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"retry-after", str(RETRY_AFTER_SECONDS).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
