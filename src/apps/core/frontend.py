"""سرو فرانت ایستای Next (مخزن Mahdieh-Front) از درون همین فرآیند جنگو — ADR-0022.

چرا درون جنگو و نه Apache/.htaccess؟
    • میزبان cPanel است و قاعدهٔ پروژه «بی‌htaccess» است؛ Passenger همه‌چیز را به WSGI می‌دهد.
    • صفحه‌های فرانت اسکریپت درون‌خطی (جریان RSC) دارند؛ CSP سخت‌گیر فقط با nonce
      «هر درخواست» آن‌ها را می‌پذیرد. nonce را فقط جنگو می‌سازد ⇒ HTML باید از جنگو بگذرد.
    • یک مبدأ (same-origin): کوکی نشست، CSRF و API بی‌CORS کار می‌کنند.

قرارداد بستهٔ فرانت (خروجی ``scripts/postbuild.mjs``)::

    <MAHDIEH_FRONT_DIR>/            (پیوند نمادین ``current`` ⇒ ``releases/<id>``)
      BUILD_INFO.json               {"app": "mahdieh-front", "release": "...", ...}
      routes.json                   {"version": 1, "nonce_placeholder": "...", "routes": {"/": "index.html"}}
      pages/                        HTML با نشانگر nonce (هرگز مستقیم سرو نمی‌شود)
      public/                       دارایی‌ها + همزادهای ‎.br/.gz (‎/_next/static/ نسخه‌دار ⇒ immutable)

رفتار:
    • فقط GET/HEAD. مسیرهای routes.json ⇒ HTML با nonce همین درخواست، ``no-cache``.
    • پرونده‌های public ⇒ WhiteNoise جداگانه (فشرده‌سازی آماده، ETag، Range).
    • هر چیز دیگر ⇒ جنگو (API، پنل، صفحه‌های قدیمی که هنوز به فرانت نیامده‌اند).
    • پوشه نیست/خراب است ⇒ عبور بی‌صدا به جنگو + یک لاگ خطا؛ سایت هرگز به‌خاطر فرانت نمی‌خوابد.
    • جابه‌جایی ``current`` (استقرار/بازگشت) بی‌نیاز از بازنشانی فرآیند دیده می‌شود.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import gzip
import json
import logging
import os
from pathlib import Path
import re
import threading
from typing import Any, Final

from django.conf import settings
from django.core.checks import CheckMessage, Tags, Warning, register
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpRequest, HttpResponse
from django.middleware.csp import get_nonce
from django.utils.cache import patch_vary_headers
from whitenoise.base import WhiteNoise
from whitenoise.middleware import WhiteNoiseMiddleware

logger = logging.getLogger(__name__)

ROUTES_VERSION: Final[int] = 1
RELEASE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{14}-[0-9a-f]{7}$")
PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^__[A-Z_]{4,40}__$")
#: پرونده‌های بی‌نسخه (favicon، manifest، جریان RSC) کوتاه کش شوند تا استقرار تازه زود دیده شود.
PUBLIC_MAX_AGE: Final[int] = 300
#: نام پرونده‌های ‎/_next/static/ هش محتوا دارند ⇒ کش یک‌سالهٔ immutable بی‌خطر است.
IMMUTABLE_PREFIX: Final[str] = "/_next/static/"
#: زیر این اندازه فشرده‌سازی صرف ندارد.
GZIP_MIN_BYTES: Final[int] = 1024
HEADER_RELEASE: Final[str] = "X-Mahdieh-Front"
SAFE_METHODS: Final[frozenset[str]] = frozenset({"GET", "HEAD"})


BUILD_INFO = "BUILD_INFO.json"


class FrontBuildError(ValueError):
    """بستهٔ فرانت با قرارداد ``routes.json``/``BUILD_INFO.json`` نمی‌خواند."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise FrontBuildError(f"{path.name} خوانده نشد: {error}") from error
    if not isinstance(data, dict):
        raise FrontBuildError(f"{path.name} باید شیء JSON باشد")
    return data


@dataclass(frozen=True, slots=True)
class FrontRelease:
    """یک انتشارِ بارگذاری‌شده و اعتبارسنجی‌شده — تغییرناپذیر، امن برای چند نخ."""

    root: Path
    release: str
    commit: str
    placeholder: bytes
    pages: dict[str, bytes]
    assets: WhiteNoise

    @classmethod
    def load(cls, root: Path) -> FrontRelease:
        """خواندن کامل انتشار در حافظه؛ هر ناسازگاری ⇒ ``FrontBuildError`` (نه نیمه‌بار)."""
        info = _read_json(root / BUILD_INFO)
        manifest = _read_json(root / "routes.json")
        release = str(info.get("release", ""))
        if info.get("app") != "mahdieh-front" or not RELEASE_PATTERN.match(release):
            raise FrontBuildError(f"BUILD_INFO.json نامعتبر (app/release): {release!r}")
        if manifest.get("version") != ROUTES_VERSION:
            raise FrontBuildError(f"نسخهٔ routes.json پشتیبانی نمی‌شود: {manifest.get('version')!r}")
        placeholder = str(manifest.get("nonce_placeholder", ""))
        if not PLACEHOLDER_PATTERN.match(placeholder):
            raise FrontBuildError(f"نشانگر nonce نامعتبر: {placeholder!r}")
        routes = manifest.get("routes")
        if not isinstance(routes, dict) or "/" not in routes:
            raise FrontBuildError("routes.json باید دست‌کم مسیر «/» را داشته باشد")

        pages_dir = (root / "pages").resolve()
        pages: dict[str, bytes] = {}
        for url, rel in routes.items():
            if not (isinstance(url, str) and url.startswith("/") and isinstance(rel, str)):
                raise FrontBuildError(f"مدخل مسیر نامعتبر: {url!r} ⇒ {rel!r}")
            page = (pages_dir / rel).resolve()
            if not page.is_relative_to(pages_dir):  # هرگز بیرون از pages/ (ضد «..»)
                raise FrontBuildError(f"مسیر صفحه از pages/ بیرون می‌زند: {rel!r}")
            try:
                pages[url] = page.read_bytes()
            except OSError as error:
                raise FrontBuildError(f"صفحهٔ {rel} خوانده نشد: {error}") from error

        public = root / "public"
        if not public.is_dir():
            raise FrontBuildError("پوشهٔ public/ نیست")
        assets = WhiteNoise(
            None,
            root=str(public),
            prefix="/",
            max_age=PUBLIC_MAX_AGE,
            allow_all_origins=False,
            immutable_file_test=lambda _path, url: url.startswith(IMMUTABLE_PREFIX),
        )
        return cls(
            root=root,
            release=release,
            commit=str(info.get("commit", "")),
            placeholder=placeholder.encode("ascii"),
            pages=pages,
            assets=assets,
        )

    def render(self, url: str, nonce: str) -> bytes:
        """HTML مسیر با nonce همین درخواست (``KeyError`` اگر مسیر از آنِ فرانت نیست)."""
        return self.pages[url].replace(self.placeholder, nonce.encode("ascii"))


class FrontResolver:
    """انتشار فعال را با هزینهٔ یک ``realpath`` و یک ``stat`` در هر درخواست، به‌روز نگه می‌دارد.

    استقرار، پیوند ``current`` را اتمی جابه‌جا می‌کند؛ مقصد تازه ⇒ بارگذاری دوباره.
    کلید، زمان تغییر ``BUILD_INFO.json`` را هم دربر دارد (این فایل همیشه آخرین فایلِ
    نوشته‌شدهٔ یک ساخت است)؛ پس ساختِ درجا روی همان پوشه (محیط توسعه یا ``deploy local``)
    هم بی‌راه‌اندازی دوبارهٔ فرایند دیده می‌شود.
    شکست بارگذاری فقط یک بار برای همان کلید لاگ می‌شود (نه سیل لاگ در هر درخواست).
    """

    def __init__(self, directory: str) -> None:
        self.directory = directory
        self._lock = threading.Lock()
        self._key: tuple[str, int | None] | None = None
        self._release: FrontRelease | None = None

    def _current_key(self) -> tuple[str, int | None]:
        target = os.path.realpath(self.directory)
        try:
            stamp: int | None = (Path(target) / BUILD_INFO).stat().st_mtime_ns
        except OSError:
            stamp = None
        return target, stamp

    def current(self) -> FrontRelease | None:
        """انتشار فعال یا ``None``؛ بارگذاری دوباره فقط وقتی مقصد یا ساخت عوض شده باشد."""
        key = self._current_key()
        if key == self._key:
            return self._release
        with self._lock:
            if key != self._key:
                self._release = self._load(Path(key[0]))
                self._key = key
        return self._release

    @staticmethod
    def _load(target: Path) -> FrontRelease | None:
        if not target.is_dir():
            logger.warning("frontend.missing", extra={"front_dir": str(target)})
            return None
        try:
            release = FrontRelease.load(target)
        except FrontBuildError as error:
            logger.exception(
                "frontend.invalid", extra={"front_dir": str(target), "error": str(error)}
            )
            return None
        logger.info(
            "frontend.loaded",
            extra={"release": release.release, "pages": len(release.pages)},
        )
        return release


_resolvers: dict[str, FrontResolver] = {}
_resolvers_lock = threading.Lock()


def resolver_for(directory: str) -> FrontResolver:
    """یک حل‌کننده برای هر پوشه — در فرآیند مشترک، تا بارگذاری تکرار نشود."""
    with _resolvers_lock:
        return _resolvers.setdefault(directory, FrontResolver(directory))


def active_release() -> str | None:
    """شناسهٔ انتشار فرانتِ در حال سرو (برای ‎/health/live/ و doctor)؛ ``None`` اگر نیست."""
    directory = getattr(settings, "MAHDIEH_FRONT_DIR", "")
    if not directory:
        return None
    release = resolver_for(directory).current()
    return release.release if release else None


def _accepts_gzip(request: HttpRequest) -> bool:
    return any(
        part.split(";", 1)[0].strip() == "gzip"
        for part in request.headers.get("Accept-Encoding", "").split(",")
    )


class FrontendMiddleware:
    """آخرین میان‌افزار: پیش از مسیریاب جنگو، مسیرهای فرانت را پاسخ می‌دهد.

    «آخرین» یعنی نزدیک‌ترین به نما: نشست، احراز هویت، CSP، سرآیندهای امنیتی و لاگ دسترسی
    همه روی پاسخ فرانت هم اعمال می‌شوند — فرانت از هیچ سد امنیتی معاف نیست.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        directory = getattr(settings, "MAHDIEH_FRONT_DIR", "")
        if not directory:
            raise MiddlewareNotUsed  # توسعه/آزمون بی‌فرانت: جنگو همه‌چیز را خودش پاسخ می‌دهد
        self.get_response = get_response
        self.resolver = resolver_for(directory)

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method not in SAFE_METHODS:
            return self.get_response(request)
        release = self.resolver.current()
        if release is None:
            return self.get_response(request)
        path = request.path_info
        if path in release.pages:
            return self._page(request, release, path)
        static_file = release.assets.files.get(path)
        if static_file is not None:
            response: HttpResponse = WhiteNoiseMiddleware.serve(static_file, request)
            response[HEADER_RELEASE] = release.release
            return response
        return self.get_response(request)

    @staticmethod
    def _page(request: HttpRequest, release: FrontRelease, path: str) -> HttpResponse:
        nonce = get_nonce(request)
        # خواندن nonce آن را «مصرف‌شده» می‌کند ⇒ میان‌افزار CSP جنگو همان را در سرآیند می‌گذارد.
        body = release.render(path, str(nonce) if nonce is not None else "")
        response = HttpResponse(content_type="text/html; charset=utf-8")
        # هر پاسخ nonce تازه دارد ⇒ ذخیرهٔ مشترک بی‌معناست؛ مرورگر هر بار بازبینی کند.
        response["Cache-Control"] = "no-cache"
        response[HEADER_RELEASE] = release.release
        patch_vary_headers(response, ("Accept-Encoding",))
        if len(body) >= GZIP_MIN_BYTES and _accepts_gzip(request):
            # HTML ایستاست و راز ندارد (توکن CSRF در کوکی است، نه صفحه) ⇒ BREACH موضوعیت ندارد.
            body = gzip.compress(body, compresslevel=6, mtime=0)
            response["Content-Encoding"] = "gzip"
        response["Content-Length"] = str(len(body))
        if request.method != "HEAD":
            response.content = body
        return response


@register(Tags.compatibility, deploy=True)
def check_frontend(**_kwargs: Any) -> list[CheckMessage]:
    """در ``check --deploy``: پوشهٔ پیکربندی‌شدهٔ فرانت هست ولی بسته‌اش خراب است ⇒ هشدار.

    نبودِ پوشه هشدار نیست: پیش از نخستین انتشار فرانت، جنگو صفحه‌های قدیمی را سرو می‌کند.
    """
    directory = getattr(settings, "MAHDIEH_FRONT_DIR", "")
    if not directory or not Path(directory).exists():
        return []
    try:
        FrontRelease.load(Path(os.path.realpath(directory)))
    except FrontBuildError as error:
        return [
            Warning(
                f"بستهٔ فرانت در {directory} نامعتبر است: {error}",
                hint="mahdieh rollback front  یا  mahdieh update front --force",
                id="mahdieh.W020",
            )
        ]
    return []
