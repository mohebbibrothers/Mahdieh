"""احراز مالکیت سایت برای سامانه‌های رسمی بیرونی (ساماندهی؛ پیش‌نیاز درخواست رفع فیلتر).

سامانهٔ ساماندهی (samandehi.ir) مالکیت دامنه را به دو روش می‌سنجد و این ماژول **هر دو** را
از یک منبع حقیقت فراهم می‌کند:

۱. تگ ``<meta name="platform-verification" content="…">`` در ``<head>`` صفحه — در لحظهٔ
   سرو به **همهٔ** صفحه‌های فرانت (``FrontendMiddleware``، شامل صفحه‌های پویا مثل
   ``/units/A1/``) و قالب‌های جنگو (``web/base.html``، خانهٔ جایگزین) تزریق می‌شود؛ پس
   تغییر کد به ساخت دوبارهٔ فرانت نیاز ندارد.
۲. پروندهٔ ``/verify-platform.txt`` که فقط خودِ کد را برمی‌گرداند.

پیکربندی (``MAHDIEH_PLATFORM_VERIFICATION``، فهرست جداشده با ویرگول)::

    yqknjaxgib  # کد سراسری: روی همهٔ نام‌های مجاز
    gam-khedmat.ir = yqknjaxgib, new.ir = abcdef  # کد ویژهٔ هر دامنه (www همان دامنه را هم می‌گیرد)

نکته‌های طراحی:

* **ایمنی تزریق**: فقط کدهایی با الگوی ``CODE_PATTERN`` پذیرفته می‌شوند و باز هم escape
  می‌شوند؛ مدخل نادرست هرگز به HTML نمی‌رسد و سیستم‌چک ``mahdieh.E022`` آن را لو می‌دهد.
* **دامنهٔ یکتا**: بازرس ساماندهی نشانیِ همان دامنهٔ ثبت‌شده را می‌خواند؛ اگر با ۳۰۱ به دامنهٔ
  دیگری برود، احراز شکست می‌خورد. پس «‎/‎» و «‎/verify-platform.txt‎» روی نامی که کد دارد
  از هدایت دامنهٔ یکتا معاف‌اند (``is_verification_request``)، و پرونده از هدایت اجباری
  HTTPS هم (``SECURE_REDIRECT_EXEMPT`` در تولید).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import html
import re

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound
from django.http.request import split_domain_port
from django.views.decorators.http import require_safe

META_NAME = "platform-verification"
FILE_PATH = "/verify-platform.txt"
CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{4,128}$")
HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$"
)
# نخستین ‎<meta charset>‎ (یا در نبودش ‎<head>‎)؛ تگ پس از آن می‌نشیند تا اعلام نویسه‌گذاری
# همچنان در ۱۰۲۴ بایت نخست بماند (قاعدهٔ HTML).
_CHARSET = re.compile(rb"<meta\s+charset=[\"']?[A-Za-z0-9_-]+[\"']?\s*/?>", re.IGNORECASE)
_HEAD = re.compile(rb"<head(?:\s[^>]*)?>", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Entry:
    """یک مدخل پیکربندی: ``host`` تهی ⇒ کد سراسری."""

    host: str
    code: str


def parse(raw: Iterable[str]) -> tuple[tuple[Entry, ...], tuple[str, ...]]:
    """مدخل‌های معتبر و فهرست مدخل‌های نادرست (برای سیستم‌چک)."""
    entries: list[Entry] = []
    invalid: list[str] = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        host, _, code = text.rpartition("=")
        host, code = host.strip().lower(), code.strip()
        if CODE_PATTERN.match(code) and (not host or HOST_PATTERN.match(host)):
            entries.append(Entry(host=host, code=code))
        else:
            invalid.append(text)
    return tuple(entries), tuple(invalid)


def configured() -> tuple[Entry, ...]:
    """مدخل‌های معتبر تنظیمات فعلی (هر بار خوانده می‌شود؛ چند مدخل، بی‌هزینه)."""
    return parse(getattr(settings, "PLATFORM_VERIFICATION", ()) or ())[0]


def request_host(request: HttpRequest) -> str:
    """نام میزبان درخواست، بی‌درگاه و کوچک‌شده (پس از دروازهٔ ``ALLOWED_HOSTS``)."""
    return split_domain_port(request.get_host())[0].lower()


def codes_for(host: str) -> tuple[str, ...]:
    """کدهای این نام: ویژهٔ دامنه (و www آن) نخست، سپس سراسری؛ بی‌تکرار."""
    host = host.lower()
    bare = host.removeprefix("www.")
    specific = [e.code for e in configured() if e.host and e.host.removeprefix("www.") == bare]
    general = [e.code for e in configured() if not e.host]
    return tuple(dict.fromkeys([*specific, *general]))


def meta_tags(host: str) -> str:
    """تگ‌های ‎<meta>‎ این نام (escape‌شده)؛ تهی اگر کدی نیست."""
    return "".join(
        f'<meta name="{META_NAME}" content="{html.escape(code, quote=True)}">'
        for code in codes_for(host)
    )


def inject(page: bytes, host: str) -> bytes:
    """افزودن تگ‌ها پس از ‎<meta charset>‎ (یا ‎<head>‎)؛ صفحهٔ بی‌head دست‌نخورده می‌ماند."""
    tags = meta_tags(host).encode("utf-8")
    if not tags:
        return page
    anchor = _CHARSET.search(page) or _HEAD.search(page)
    if anchor is None:
        return page
    return page[: anchor.end()] + tags + page[anchor.end() :]


def is_verification_request(path: str, host: str) -> bool:
    """آیا این درخواست را بازرس احراز می‌خواند و باید روی همین نام پاسخ گیرد؟"""
    return path in {"/", FILE_PATH} and bool(codes_for(host))


@require_safe
def verify_platform_txt(request: HttpRequest) -> HttpResponse:
    """``/verify-platform.txt`` — فقط کدِ این نام، بی سطر پایانی (قالب ساماندهی)."""
    codes = codes_for(request_host(request))
    if not codes:
        return HttpResponseNotFound("", content_type="text/plain; charset=utf-8")
    response = HttpResponse(codes[0], content_type="text/plain; charset=utf-8")
    # بازرس باید همیشه مقدار فعلی را ببیند (تعویض کد یا دامنه)، نه نسخهٔ کش‌شده.
    response["Cache-Control"] = "no-cache"
    response["X-Robots-Tag"] = "noindex"
    return response
