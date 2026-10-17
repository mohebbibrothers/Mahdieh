"""قرارداد سدِ ظرفیت WSGI — «زیر فشار، رفتارِ درست».

چرا این آزمون‌ها با **رشتهٔ واقعی** نوشته شده‌اند؟ چون سد یک شمارندهٔ مشترک است و
شمارندهٔ مشترک فقط وقتی معنا دارد که چند درخواست واقعاً هم‌زمان باشند. آزمونِ
تک‌رشته‌ای می‌تواند سدی را سبز نشان دهد که در تولید هیچ کاری نمی‌کند — همان
«سبزِ دروغ» که در این پروژه گران تمام شده است.

سه چیز سنجیده می‌شود: (۱) در فشار، درخواست مازاد ۵۰۳ می‌گیرد و هرگز به اپ
نمی‌رسد؛ (۲) پس از پایان هر درخواست، ظرفیت آزاد می‌شود (بدون نشت)؛ (۳) اگر کش
بمیرد، سامانه باز می‌ماند — «ردکردن همه» بدترین پاسخ ممکن است.
"""

from __future__ import annotations

import threading
from typing import Any

from django.core.cache import cache
import pytest

from apps.core.shedding import INFLIGHT_KEY, WSGIConcurrencyLimitMiddleware

pytestmark = pytest.mark.django_db


class _Recorder:
    """دستیار ثبت پاسخ WSGI: وضعیت، سرآیندها و بدنه."""

    def __init__(self) -> None:
        self.status: str = ""
        self.headers: dict[str, str] = {}
        self.body: bytes = b""

    def __call__(self, status: str, headers: list[tuple[str, str]]) -> None:
        self.status = status
        self.headers = {name.lower(): value for name, value in headers}

    def consume(self, iterable: Any) -> None:
        self.body = b"".join(bytes(chunk) for chunk in iterable)


@pytest.fixture(autouse=True)
def _clean_counter() -> None:
    """شمارندهٔ مشترک باید پیش از هر آزمون صفر باشد (کش آزمون درون‌حافظه‌ای است)."""
    cache.clear()


def _ok_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    """اپ ساده که ۲۰۰ با بدنهٔ «ok» برمی‌گرداند."""
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"ok"]


def _call(middleware: WSGIConcurrencyLimitMiddleware, path: str) -> _Recorder:
    """اجرای یک درخواست کامل (تا مصرف بدنه) و برگرداندن پاسخ ثبت‌شده."""
    recorder = _Recorder()
    iterable = middleware({"PATH_INFO": path, "REQUEST_METHOD": "GET"}, recorder)
    recorder.consume(iterable)
    if hasattr(iterable, "close"):
        iterable.close()
    return recorder


def test_under_the_limit_requests_reach_the_app() -> None:
    """زیر سقف، هر درخواست به اپ می‌رسد و شمارنده پس از پایان صفر می‌شود."""
    middleware = WSGIConcurrencyLimitMiddleware(_ok_app, limit=2)
    for _ in range(3):
        assert _call(middleware, "/units/").status == "200 OK"
    assert cache.get(INFLIGHT_KEY) == 0
    assert middleware.shed_total == 0


def test_over_the_limit_is_shed_and_never_reaches_the_app() -> None:
    """در فشار، درخواست مازاد ۵۰۳ می‌گیرد و بدنه‌اش هم خوانا است."""
    reached: list[str] = []

    def _app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        reached.append(str(environ["PATH_INFO"]))
        start_response("200 OK", [])
        return [b"ok"]

    middleware = WSGIConcurrencyLimitMiddleware(_app, limit=1)
    cache.set(INFLIGHT_KEY, 1, timeout=30)  # یک درخواست در جریان است

    recorder = _call(middleware, "/units/")
    assert recorder.status.startswith("503")
    assert recorder.headers["retry-after"] == "2"
    assert recorder.headers["cache-control"] == "no-store"
    assert "text/html" in recorder.headers["content-type"]
    assert "زائر" in recorder.body.decode("utf-8")
    assert reached == [], "درخواست سدشده هرگز نباید به اپ برسد"
    assert middleware.shed_total == 1


def test_api_paths_get_a_machine_readable_body() -> None:
    """مسیرهای API باید بدنهٔ Problem Details بگیرند، نه HTML."""
    middleware = WSGIConcurrencyLimitMiddleware(_ok_app, limit=1)
    cache.set(INFLIGHT_KEY, 1, timeout=30)
    recorder = _call(middleware, "/api/v1/units")
    assert recorder.status.startswith("503")
    assert recorder.headers["content-type"] == "application/problem+json"
    assert b"service_unavailable" in recorder.body


def test_monitoring_and_static_are_never_shed() -> None:
    """پایش و دارایی ایستا در فشار هم پاسخ می‌گیرند (و شمارنده را دست نمی‌زنند)."""
    middleware = WSGIConcurrencyLimitMiddleware(_ok_app, limit=1)
    cache.set(INFLIGHT_KEY, 5, timeout=30)

    for path in ("/health/live/", "/health/ready/", "/static/web/css/mahdieh.css"):
        assert _call(middleware, path).status == "200 OK"
    assert cache.get(INFLIGHT_KEY) == 5, "مسیر معاف نباید شمارنده را تغییر دهد"


def test_capacity_is_released_even_when_the_app_raises() -> None:
    """خطای اپ نباید ظرفیت را برای همیشه اشغال کند (نشت شمارنده = قطعی تدریجی)."""

    def _exploding(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        msg = "view exploded"
        raise RuntimeError(msg)

    middleware = WSGIConcurrencyLimitMiddleware(_exploding, limit=1)
    with pytest.raises(RuntimeError):
        _call(middleware, "/units/")
    assert cache.get(INFLIGHT_KEY) == 0


def test_capacity_is_released_after_streaming_body_is_consumed() -> None:
    """با بدنهٔ جریانی، ظرفیت تا **پایان ارسال** اشغال می‌ماند، نه تا قبلش."""
    observed: dict[str, Any] = {}

    def _streaming(environ: dict[str, Any], start_response: Any) -> Any:
        start_response("200 OK", [])
        try:
            yield b"first"
            observed["mid"] = cache.get(INFLIGHT_KEY)
            yield b"second"
        finally:
            observed["after"] = cache.get(INFLIGHT_KEY)

    middleware = WSGIConcurrencyLimitMiddleware(_streaming, limit=5)
    recorder = _Recorder()
    iterable = middleware({"PATH_INFO": "/export/"}, recorder)
    bodies = list(iterable)

    assert bodies == [b"first", b"second"]
    assert observed["mid"] == 1, "در میانهٔ ارسال باید هنوز در حال جریان شمرده شود"
    assert cache.get(INFLIGHT_KEY) == 0


def test_real_thread_contention_only_lets_the_limit_through() -> None:
    """با رشته‌های واقعی: حداکثر «سقف» درخواست هم‌زمان عبور می‌کند.

    این همان چیزی است که یک آزمون تک‌رشته‌ای نمی‌تواند ثابت کند: دو رشته هم‌زمان
    باید یکی عبور کند و دیگری ۵۰۳ بگیرد.
    """
    inside = threading.Event()
    release = threading.Event()
    outcomes: list[str] = []
    lock = threading.Lock()

    def _slow_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        inside.set()
        release.wait(timeout=5)
        start_response("200 OK", [])
        return [b"ok"]

    middleware = WSGIConcurrencyLimitMiddleware(_slow_app, limit=1)

    def _first() -> None:
        recorder = _call(middleware, "/units/")
        with lock:
            outcomes.append(recorder.status.split(" ")[0])

    worker = threading.Thread(target=_first)
    worker.start()
    assert inside.wait(timeout=5), "درخواست نخست باید وارد اپ شود"

    second = _call(middleware, "/units/")
    with lock:
        outcomes.append(second.status.split(" ")[0])

    release.set()
    worker.join(timeout=5)

    assert outcomes == ["503", "200"], f"پاسخ‌های غیرمنتظره: {outcomes}"
    assert cache.get(INFLIGHT_KEY) == 0


def test_cache_outage_keeps_the_site_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """قطعی کش ⇒ fail-open: سایت باز می‌ماند، نه اینکه همه ۵۰۳ بگیرند."""

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        msg = "cache is down"
        raise ConnectionError(msg)

    monkeypatch.setattr(cache, "add", _boom)
    monkeypatch.setattr(cache, "incr", _boom)
    monkeypatch.setattr(cache, "decr", _boom)

    middleware = WSGIConcurrencyLimitMiddleware(_ok_app, limit=1)
    assert _call(middleware, "/units/").status == "200 OK"
    assert middleware.shed_total == 0


def test_wsgi_entrypoint_wraps_only_when_both_switches_are_on(settings) -> None:
    """نقطهٔ ورود WSGI باید با دو کلید روشن شود و با یکی، دست‌نخورده بماند."""
    import importlib

    import config.wsgi as wsgi_module

    original = settings.LOAD_SHEDDING_WSGI_ENABLED
    try:
        settings.LOAD_SHEDDING_ENABLED = True
        settings.LOAD_SHEDDING_WSGI_ENABLED = False
        assert importlib.reload(wsgi_module).application is not None
        unwrapped = importlib.reload(wsgi_module).application
        assert not isinstance(unwrapped, WSGIConcurrencyLimitMiddleware)

        settings.LOAD_SHEDDING_WSGI_ENABLED = True
        wrapped = importlib.reload(wsgi_module).application
        assert isinstance(wrapped, WSGIConcurrencyLimitMiddleware)
        assert wrapped.limit == settings.LOAD_SHEDDING_MAX_CONCURRENCY
    finally:
        settings.LOAD_SHEDDING_WSGI_ENABLED = original
        importlib.reload(wsgi_module)
