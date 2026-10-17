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

from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
import contextlib
from functools import lru_cache
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


# ══════════════════════════════════════════════════════════════════════════════
# سدِ ظرفیت در WSGI — همان تصمیم، برای مدلِ کارگرِ هم‌زمان (sync)
# ══════════════════════════════════════════════════════════════════════════════
# چرا دو پیاده‌سازی؟ چون مدل هم‌زمانی WSGI و ASGI یکی نیست و «یک کد برای هر دو»
# یعنی یکی از آن دو، بی‌آنکه کسی بفهمد، کار نکند:
#
#   • ASGI: یک حلقهٔ رویداد، ده‌ها درخواست هم‌زمان در یک فرآیند ⇒ شمارندهٔ
#     درون‌فرآیندی درست و بی‌هزینه است (همان کلاس بالا).
#   • WSGI با کارگر ``sync``: هر فرآیند **فقط یک** درخواست را هم‌زمان اجرا
#     می‌کند؛ پس شمارندهٔ درون‌فرآیندی هرگز از ۱ بالاتر نمی‌رود و سد هیچ‌وقت
#     فعال نمی‌شود. صفِ واقعی، پشت backlog سوکت و در هستهٔ سیستم می‌نشیند —
#     جایی که پایتون آن را نمی‌بیند. بنابراین شمارنده باید **مشترک** باشد تا
#     مجموع درخواست‌های در جریانِ همهٔ کارگرها دیده شود.
#
# قرارداد صریح دربارهٔ این لایه (تا کسی به آن امید بی‌جا نبندد):
#
#   ۱. با ``sync``، سدِ اصلی **Nginx** است (``limit_conn``/``limit_req``)؛ این
#      لایه با ``LOAD_SHEDDING_WSGI_ENABLED=false`` خاموش می‌ماند، چون سقفِ
#      مؤثر آن برابر شمارِ کارگرهاست و فعال‌کردنش ظرفیتِ سالم را هم دور
#      می‌ریزد (ADR-0016).
#   ۲. اگر روزی به کارگرِ رشته‌ای (``gthread``/``uvicorn-worker``) کوچیدیم،
#      همین لایه با یک متغیر محیطی روشن می‌شود و رفتارِ درست می‌دهد.
#   ۳. اگر کش در دسترس نباشد، لایه **باز** می‌ماند (fail-open): در قطعیِ کش،
#      «ردکردن همه» بدترین پاسخ ممکن است.

#: کلید شمارندهٔ مشترک. یک کلید، همهٔ کارگرها، همهٔ میزبان‌ها.
INFLIGHT_KEY = "mahdieh:shed:inflight"
#: عمر کلید: اگر فرآیندی در میانهٔ کار بمیرد، شمارنده خودش صفر می‌شود.
INFLIGHT_TTL_SECONDS = 30


def _problem_body() -> bytes:
    """بدنهٔ ۵۰۳ برای مسیرهای API — همان قرارداد Problem Details (RFC 9457)."""
    return (
        '{"type":"https://docs.mahdieh.ir/problems/service_unavailable",'
        '"title":"ترافیک بالا","status":503,"code":"service_unavailable",'
        '"detail":"همین حالا درخواست‌ها زیاد است؛ چند لحظه بعد دوباره تلاش کنید."}'
    ).encode()


@lru_cache(maxsize=1)
def _html_body() -> bytes:
    """صفحهٔ ۵۰۳ فارسی — **یک بار** رندر و کش می‌شود.

    چرا یک بار؟ چون این صفحه دقیقاً وقتی لازم می‌شود که سامانه زیر فشار است؛
    رندرِ قالب در آن لحظه، هدیه‌ای است به همان فشاری که می‌خواهیم مهار کنیم.
    چرا nonce خالی؟ چون این پاسخ مستقیماً از لایهٔ WSGI می‌آید و هدر CSP ندارد؛
    پس استایل درون‌خطی‌اش آزاد است.
    """
    from django.template.loader import render_to_string

    return render_to_string("503.html", {"request_id": "", "csp_nonce": ""}).encode()


class _ReleasingIterable:
    """پوشش بدنهٔ پاسخ تا ظرفیت **پس از ارسال کامل** آزاد شود، نه پیش از آن.

    تلهٔ واقعی: اگر در ``finally`` بلافاصله پس از صدا زدن اپ آزاد کنیم، بدنهٔ
    جریانی هنوز ارسال نشده و شمارنده زودتر از موعد کم می‌شود؛ نتیجه، سدِ
    بی‌دندان در پاسخ‌های بزرگ (مثلاً خروجی اکسل) است.
    """

    def __init__(self, iterable: Iterable[bytes], release: Callable[[], None]) -> None:
        """بسته‌بندی بدنهٔ پاسخ با آزادسازی یک‌باره."""
        self._iterable = iterable
        self._release = release
        self._released = False

    def __iter__(self) -> Iterator[bytes]:
        """ارسال بدنه و آزادسازی ظرفیت در پایان (حتی در خطا)."""
        try:
            yield from self._iterable
        finally:
            self._release_once()

    def close(self) -> None:
        """بستن بدنهٔ درونی (قرارداد WSGI) و آزادسازی ظرفیت."""
        close = getattr(self._iterable, "close", None)
        if callable(close):
            close()
        self._release_once()

    def _release_once(self) -> None:
        """آزادسازی، دقیقاً یک بار."""
        if not self._released:
            self._released = True
            self._release()


class WSGIConcurrencyLimitMiddleware:
    """سقف درخواست‌های هم‌زمان با شمارندهٔ مشترک؛ فراتر از آن ⇒ ۵۰۳ سریع."""

    def __init__(
        self,
        app: Callable[..., Iterable[bytes]],
        *,
        limit: int = 240,
        exempt_prefixes: Iterable[str] = DEFAULT_EXEMPT_PREFIXES,
        key: str = INFLIGHT_KEY,
        window_seconds: int = INFLIGHT_TTL_SECONDS,
    ) -> None:
        """آماده‌سازی سد با سقف، استثناها و کلید شمارندهٔ مشترک."""
        if limit < 1:
            msg = "limit باید دست‌کم ۱ باشد."
            raise ValueError(msg)
        self.app = app
        self.limit = limit
        self.exempt_prefixes = tuple(exempt_prefixes)
        self.key = key
        self.window_seconds = window_seconds
        self.shed_total = 0

    # ── شمارندهٔ مشترک ──────────────────────────────────────────────────────
    def _acquire(self) -> int | None:
        """افزودن یک به شمارندهٔ مشترک؛ ``None`` یعنی «کش در دسترس نیست»."""
        from django.core.cache import cache

        try:
            cache.add(self.key, 0, timeout=self.window_seconds)
            return int(cache.incr(self.key))
        except ValueError:
            # کلید بین add و incr منقضی شد (پنجرهٔ ۳۰ ثانیه‌ای) ⇒ یک بار دیگر.
            try:
                return int(cache.incr(self.key))
            except Exception:  # هر خطای کش ⇒ fail-open (آگاهانه)
                return None
        except Exception:  # هر خطای کش ⇒ fail-open (آگاهانه)
            return None

    def _release(self) -> None:
        """کاستن یک از شمارنده؛ خطا هرگز نباید پاسخ را بشکند."""
        from django.core.cache import cache

        # nosec B110 — «نبودِ آزادسازی» فقط یک شمارندهٔ محافظه‌کارانه‌تر می‌سازد؛
        # برخلاف پرتاب خطا، هرگز درخواستِ سالم را نمی‌شکند.
        with contextlib.suppress(Exception):
            if cache.decr(self.key) < 0:
                cache.set(self.key, 0, timeout=self.window_seconds)

    # ── اجرا ────────────────────────────────────────────────────────────────
    def __call__(
        self,
        environ: Mapping[str, Any],
        start_response: Callable[..., Any],
    ) -> Iterable[bytes]:
        """اجرای درخواست زیر سقف ظرفیت یا پاسخ ۵۰۳ سریع."""
        path = str(environ.get("PATH_INFO", "") or "/")
        if path.startswith(self.exempt_prefixes):
            return self.app(environ, start_response)

        in_flight = self._acquire()
        if in_flight is None:  # کش قطع ⇒ باز بمان
            return self.app(environ, start_response)

        if in_flight > self.limit:
            self._release()
            return self._shed(path, start_response)

        try:
            iterable = self.app(environ, start_response)
        except BaseException:
            self._release()
            raise
        return _ReleasingIterable(iterable, self._release)

    def _shed(self, path: str, start_response: Callable[..., Any]) -> Iterable[bytes]:
        """پاسخ ۵۰۳ کوتاه و قابل‌تشخیص (JSON برای API، صفحهٔ فارسی برای بقیه)."""
        self.shed_total += 1
        is_api = path.startswith("/api/")
        body = _problem_body() if is_api else _html_body()
        content_type = "application/problem+json" if is_api else "text/html; charset=utf-8"
        logger.warning(
            "load_shed",
            extra={
                "path": path,
                "limit": self.limit,
                "shed_total": self.shed_total,
                "layer": "wsgi",
            },
        )
        start_response(
            "503 Service Unavailable",
            [
                ("Content-Type", content_type),
                ("Content-Length", str(len(body))),
                ("Retry-After", str(RETRY_AFTER_SECONDS)),
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )
        return [body]
