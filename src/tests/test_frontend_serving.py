"""سرو فرانت ایستای Next از درون جنگو (ADR-0022) — قرارداد بستهٔ postbuild.

سناریوهای محوری:
    • HTML مسیرهای فرانت با nonce **همین** درخواست؛ همان nonce در سرآیند CSP.
    • دارایی‌های نسخه‌دار immutable، بقیه کش کوتاه؛ همزاد br/gz سرو می‌شود.
    • API، پنل، POST و مسیرهای ناشناخته دست‌نخورده به جنگو می‌رسند.
    • نبود/خرابی بسته ⇒ عبور بی‌صدا به صفحه‌های قدیمی (سایت هرگز نمی‌خوابد).
    • جابه‌جایی اتمی ``current`` بی‌نیاز از بازنشانی فرآیند دیده می‌شود.
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import re

from django.conf import settings
from django.core.checks.registry import registry
from django.test import Client, override_settings
import pytest

from apps.core import frontend
from apps.core.frontend import FrontBuildError, FrontRelease, check_frontend
from apps.inventory.models import Unit

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

PLACEHOLDER = "__CSP_NONCE__"
RELEASE_A = "20261020101500-abcdef0"
RELEASE_B = "20261021101500-1234567"
HOME_HTML = (
    "<!doctype html><html lang=fa dir=rtl><head>"
    f'<script nonce="{PLACEHOLDER}" src="/_next/static/chunks/app.js"></script>'
    f'<style nonce="{PLACEHOLDER}">body{{margin:0}}</style></head>'
    f'<body><main>{"مهدیه " * 400}</main><script nonce="{PLACEHOLDER}">self.__next_f=[]</script>'
    "</body></html>"
)


def _write_release(
    base: Path,
    release: str,
    *,
    routes: dict[str, str] | None = None,
    dynamic: dict[str, str] | None = None,
) -> Path:
    root = base / "releases" / release
    (root / "pages").mkdir(parents=True)
    (root / "public" / "_next" / "static" / "chunks").mkdir(parents=True)
    (root / "pages" / "index.html").write_text(
        HOME_HTML.replace("مهدیه", release), encoding="utf-8"
    )
    (root / "pages" / "404.html").write_text(
        f'<p>404</p><script nonce="{PLACEHOLDER}"></script>', encoding="utf-8"
    )
    (root / "pages" / "units" / "_").mkdir(parents=True)
    (root / "pages" / "units" / "_" / "index.html").write_text(
        f'<main>UNIT-TEMPLATE</main><script nonce="{PLACEHOLDER}"></script>', encoding="utf-8"
    )
    chunk = b"console.log('mahdieh');" * 80
    (root / "public" / "_next" / "static" / "chunks" / "app.js").write_bytes(chunk)
    (root / "public" / "_next" / "static" / "chunks" / "app.js.br").write_bytes(b"BROTLI")
    (root / "public" / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    (root / "routes.json").write_text(
        json.dumps(
            {
                "version": 1,
                "nonce_placeholder": PLACEHOLDER,
                "not_found": "404.html",
                "routes": routes if routes is not None else {"/": "index.html"},
                **({"dynamic": dynamic} if dynamic is not None else {}),
            }
        ),
        encoding="utf-8",
    )
    (root / "BUILD_INFO.json").write_text(
        json.dumps({"app": "mahdieh-front", "release": release, "commit": "abcdef0" * 5}),
        encoding="utf-8",
    )
    return root


def _activate(base: Path, release: str) -> Path:
    """همان کاری که front_deploy.sh می‌کند: پیوند موقت ⇒ rename اتمی روی ``current``."""
    current = base / "current"
    staged = base / ".current.next"
    staged.unlink(missing_ok=True)
    staged.symlink_to(Path("releases") / release)
    staged.replace(current)
    return current


@pytest.fixture
def front_dir(tmp_path: Path) -> Path:
    _write_release(tmp_path, RELEASE_A)
    return _activate(tmp_path, RELEASE_A)


@pytest.fixture
def front(front_dir: Path):
    enforced = {"SECURE_CSP": settings.CSP_POLICY_ENFORCED, "SECURE_CSP_REPORT_ONLY": {}}
    with override_settings(MAHDIEH_FRONT_DIR=str(front_dir), **enforced):
        yield Client()


def _nonce_in_header(response) -> str:
    match = re.search(
        r"script-src [^;]*'nonce-([^']+)'", response.headers["Content-Security-Policy"]
    )
    assert match, response.headers["Content-Security-Policy"]
    return match.group(1)


def test_home_carries_the_requests_own_nonce(front: Client) -> None:
    """نشانگر هیچ‌جا نمی‌ماند؛ nonce بدنه = nonce سرآیند؛ هر درخواست nonce تازه."""
    first = front.get("/")
    assert first.status_code == 200
    body = first.content.decode()
    assert PLACEHOLDER not in body
    nonce = _nonce_in_header(first)
    assert body.count(f'nonce="{nonce}"') == 3
    assert f"'nonce-{nonce}'" in first.headers["Content-Security-Policy"].split("style-src")[1]
    assert first.headers["Content-Type"] == "text/html; charset=utf-8"
    assert first.headers["Cache-Control"] == "no-cache"
    assert first.headers["X-Mahdieh-Front"] == RELEASE_A
    assert first.headers["X-Content-Type-Options"] == "nosniff"
    assert _nonce_in_header(front.get("/")) != nonce


def test_home_is_gzipped_when_accepted_and_head_has_no_body(front: Client) -> None:
    """فشرده‌سازی آنی برای HTML (nonce هر بار فرق دارد ⇒ پیش‌فشرده ممکن نیست)."""
    zipped = front.get("/", headers={"Accept-Encoding": "br;q=1, gzip;q=0.8"})
    assert zipped.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in zipped.headers["Vary"]
    html = gzip.decompress(zipped.content).decode()
    assert RELEASE_A in html
    assert int(zipped.headers["Content-Length"]) == len(zipped.content)
    head = front.head("/")
    assert head.status_code == 200
    assert head.content == b""
    assert int(head.headers["Content-Length"]) > 0


def test_hashed_assets_are_immutable_and_precompressed(front: Client) -> None:
    """‎/_next/static/ ⇒ کش یک‌سالهٔ immutable و همزاد br؛ بقیهٔ public کش کوتاه."""
    chunk = front.get("/_next/static/chunks/app.js", headers={"Accept-Encoding": "br"})
    assert chunk.status_code == 200
    assert "immutable" in chunk.headers["Cache-Control"]
    assert chunk.headers["Content-Encoding"] == "br"
    assert b"".join(chunk.streaming_content) == b"BROTLI"
    assert chunk.headers["X-Mahdieh-Front"] == RELEASE_A
    chunk.close()
    icon = front.get("/favicon.ico")
    assert icon.status_code == 200
    assert icon.headers["Cache-Control"] == f"max-age={frontend.PUBLIC_MAX_AGE}, public"
    assert "Access-Control-Allow-Origin" not in icon.headers
    assert b"".join(icon.streaming_content) == b"\x00\x00\x01\x00"
    icon.close()


def test_django_keeps_everything_else(front: Client) -> None:
    """API، POST روی «/»، پنل و مسیر ناشناخته از فرانت عبور می‌کنند."""
    api = front.get("/api/v1/meta")
    assert api.status_code == 200
    assert "X-Mahdieh-Front" not in api.headers
    assert "X-Mahdieh-Front" not in front.post("/").headers
    assert "X-Mahdieh-Front" not in front.get("/help/").headers
    assert front.get("/pages/index.html").status_code == 404  # pages/ هرگز مستقیم سرو نمی‌شود
    assert front.get("/routes.json").status_code == 404


def test_front_page_without_trailing_slash_redirects_with_its_query(tmp_path: Path) -> None:
    """«‎/booking‎» تایپ‌شده ⇒ ۳۰۱ به «‎/booking/‎» با همان پرس‌وجو؛ مسیر ناشناخته دست جنگو می‌ماند."""
    _write_release(tmp_path, RELEASE_A, routes={"/": "index.html", "/booking/": "index.html"})
    current = _activate(tmp_path, RELEASE_A)
    with override_settings(MAHDIEH_FRONT_DIR=str(current)):
        client = Client()
        response = client.get("/booking", {"unit": "A1", "day": "2026-10-23"})
        assert response.status_code == 301
        assert response["Location"] == "/booking/?unit=A1&day=2026-10-23"
        assert client.get("/booking/").status_code == 200
        assert client.get("/bookings").status_code == 404


def test_printed_certificate_link_opens_the_front_verifier(front: Client) -> None:
    """نشانی پایدار QR گواهی، با فرانت فعال به پنجرهٔ استعلام آن می‌رود (نه قالب قدیمی)."""
    response = front.get("/verify/abc.DEF-123_xyz:sig/")
    assert response.status_code == 302
    assert response["Location"] == "/?verify=abc.DEF-123_xyz%3Asig"


def test_printed_offline_check_link_opens_the_portal_form(tmp_path: Path) -> None:
    """نشانی چاپی بازرسی آفلاین (‎/verify/‎): با پرتال ⇒ فرم پرتال؛ بی‌پرتال ⇒ قالب قدیمی بی‌خطا."""
    _write_release(tmp_path, RELEASE_A, routes={"/": "index.html"})
    current = _activate(tmp_path, RELEASE_A)
    with override_settings(MAHDIEH_FRONT_DIR=str(current)):
        response = Client().get("/verify/")
        assert response.status_code == 200
        assert frontend.front_serves("/portal/") is False

    _write_release(tmp_path, RELEASE_B, routes={"/": "index.html", "/portal/": "index.html"})
    current = _activate(tmp_path, RELEASE_B)
    with override_settings(MAHDIEH_FRONT_DIR=str(current)):
        response = Client().get("/verify/")
        assert response.status_code == 302
        assert response["Location"] == "/portal/#verify"


def test_missing_bundle_falls_back_to_legacy_pages(tmp_path: Path) -> None:
    """پیش از نخستین انتشار فرانت: صفحهٔ اصلی قدیمی جنگو، بی‌خطا."""
    with override_settings(MAHDIEH_FRONT_DIR=str(tmp_path / "absent" / "current")):
        response = Client().get("/")
        assert response.status_code == 200
        assert "X-Mahdieh-Front" not in response.headers
        assert check_frontend() == []


def test_broken_bundle_is_refused_and_reported(tmp_path: Path) -> None:
    """مسیر صفحه با «..» ⇒ کل بسته رد می‌شود (نه نیمه‌بار) و نگهبان W020 گزارش می‌دهد."""
    _write_release(tmp_path, RELEASE_A, routes={"/": "../../../../etc/passwd"})
    current = _activate(tmp_path, RELEASE_A)
    with pytest.raises(FrontBuildError, match="pages/"):
        FrontRelease.load(current.resolve())
    with override_settings(MAHDIEH_FRONT_DIR=str(current)):
        assert "X-Mahdieh-Front" not in Client().get("/").headers
        assert [m.id for m in check_frontend()] == ["mahdieh.W020"]


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"app": "other"}, "BUILD_INFO"),
        ({"release": "latest"}, "BUILD_INFO"),
    ],
)
def test_build_info_contract_is_enforced(
    tmp_path: Path, patch: dict[str, str], reason: str
) -> None:
    root = _write_release(tmp_path, RELEASE_A)
    info = json.loads((root / "BUILD_INFO.json").read_text(encoding="utf-8"))
    (root / "BUILD_INFO.json").write_text(json.dumps({**info, **patch}), encoding="utf-8")
    with pytest.raises(FrontBuildError, match=reason):
        FrontRelease.load(root)


def test_atomic_switch_is_seen_without_restart(front: Client, front_dir: Path) -> None:
    """استقرار/بازگشت فقط پیوند را جابه‌جا می‌کند؛ درخواست بعدی انتشار تازه را می‌بیند."""
    assert front.get("/").headers["X-Mahdieh-Front"] == RELEASE_A
    _write_release(front_dir.parent, RELEASE_B)
    _activate(front_dir.parent, RELEASE_B)
    response = front.get("/")
    assert response.headers["X-Mahdieh-Front"] == RELEASE_B
    assert RELEASE_B in response.content.decode()
    live = front.get("/health/live/").json()
    assert live["front"] == RELEASE_B
    _activate(front_dir.parent, RELEASE_A)  # بازگشت
    assert front.get("/").headers["X-Mahdieh-Front"] == RELEASE_A


def test_in_place_rebuild_is_seen_without_restart(front: Client, front_dir: Path) -> None:
    """ساخت درجا روی همان پوشه (توسعه/``deploy local``): BUILD_INFO تازه ⇒ بارگذاری دوباره."""
    assert front.get("/").headers["X-Mahdieh-Front"] == RELEASE_A
    root = front_dir.resolve()
    (root / "pages" / "index.html").write_text(
        HOME_HTML.replace("مهدیه", RELEASE_B), encoding="utf-8"
    )
    info = root / "BUILD_INFO.json"
    info.write_text(
        json.dumps({"app": "mahdieh-front", "release": RELEASE_B, "commit": "1234567" * 5}),
        encoding="utf-8",
    )
    stamp = info.stat().st_mtime_ns + 1_000_000_000  # هم‌زمانیِ دانهٔ ساعت فایل‌سیستم نه
    os.utime(info, ns=(stamp, stamp))
    response = front.get("/")
    assert response.headers["X-Mahdieh-Front"] == RELEASE_B
    assert RELEASE_B in response.content.decode()


def test_liveness_reports_no_front_when_unconfigured(client: Client) -> None:
    with override_settings(MAHDIEH_FRONT_DIR=""):
        assert client.get("/health/live/").json()["front"] is None


def test_core_system_checks_are_registered() -> None:
    """بازگشت‌ناپذیری درس مرحلهٔ ۲: نگهبان‌های هسته باید در ``check`` اجرا شوند."""
    names = {
        f"{c.__module__}.{c.__name__}" for c in registry.get_checks(include_deployment_checks=True)
    }
    for expected in (
        "apps.core.checks.cors_configuration_check",
        "apps.core.checks.password_hasher_library_check",
        "apps.core.frontend.check_frontend",
    ):
        assert expected in names


# ── مسیرهای پویا (صفحهٔ هر واحد، P-03) ─────────────────────────────────────

UNIT_DYNAMIC = {"/units/[code]/": "units/_/index.html"}


@pytest.fixture
def unit_front(tmp_path: Path):
    _write_release(tmp_path, RELEASE_A, dynamic=UNIT_DYNAMIC)
    current = _activate(tmp_path, RELEASE_A)
    enforced = {"SECURE_CSP": settings.CSP_POLICY_ENFORCED, "SECURE_CSP_REPORT_ONLY": {}}
    with override_settings(MAHDIEH_FRONT_DIR=str(current), **enforced):
        yield Client()


def test_unit_page_serves_the_shared_template_with_the_requests_nonce(
    unit_front: Client, units: dict[str, Unit]
) -> None:
    """هر واحد فعال ⇒ همان قالب مشترک فرانت با nonce همین درخواست و ۲۰۰."""
    del units
    response = unit_front.get("/units/B2/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "UNIT-TEMPLATE" in body
    assert PLACEHOLDER not in body
    assert f'nonce="{_nonce_in_header(response)}"' in body
    assert response.headers["X-Mahdieh-Front"] == RELEASE_A
    assert frontend.front_serves("/units/A1/") is True


def test_unit_page_canonicalises_case_and_slash_keeping_the_query(
    unit_front: Client, units: dict[str, Unit]
) -> None:
    """«‎/units/a1/‎» و «‎/units/A1‎» ⇒ ۳۰۱ به نشانی استاندارد با همان پرس‌وجو."""
    del units
    lower = unit_front.get("/units/a1/", {"guests": "4"})
    assert lower.status_code == 301
    assert lower["Location"] == "/units/A1/?guests=4"
    bare = unit_front.get("/units/A1", {"cells": "2"})
    assert bare.status_code == 301
    assert bare["Location"] == "/units/A1/?cells=2"


def test_unknown_or_inactive_unit_gets_the_front_404_with_a_real_status(
    unit_front: Client, units: dict[str, Unit]
) -> None:
    """کد ناموجود یا واحد غیرفعال ⇒ صفحهٔ ۴۰۴ خود فرانت با وضعیت ۴۰۴ (نه «۴۰۴ نرم»)."""
    units["B3"].is_active = False
    units["B3"].save(update_fields=["is_active"])
    for path in ("/units/ZZ9/", "/units/B3/"):
        response = unit_front.get(path)
        assert response.status_code == 404
        body = response.content.decode()
        assert "<p>404</p>" in body
        assert PLACEHOLDER not in body
    # نویسه‌های بیرون از الگو اصلاً به قالب نمی‌رسند (دست جنگو)
    assert "X-Mahdieh-Front" not in unit_front.get("/units/a.b/").headers


def test_dynamic_pattern_without_guard_is_served_as_is(tmp_path: Path) -> None:
    """الگوی بی‌نگهبان هم سرو می‌شود؛ تشخیص «پیدا نشد» با خود فرانت است."""
    _write_release(tmp_path, RELEASE_A, dynamic={"/stays/[token]/": "units/_/index.html"})
    current = _activate(tmp_path, RELEASE_A)
    with override_settings(MAHDIEH_FRONT_DIR=str(current)):
        response = Client().get("/stays/x-1/")
        assert response.status_code == 200
        assert b"UNIT-TEMPLATE" in response.content


@pytest.mark.parametrize(
    "pattern",
    ["/units/", "units/[code]/", "/units/[Code]/", "/units/[code]/[code]/", "/un its/[code]/"],
)
def test_invalid_dynamic_patterns_refuse_the_bundle(tmp_path: Path, pattern: str) -> None:
    """الگوی بی‌پارامتر، بی‌اسلش، نام نامعتبر یا تکراری ⇒ کل بسته رد می‌شود."""
    root = _write_release(tmp_path, RELEASE_A, dynamic={pattern: "units/_/index.html"})
    with pytest.raises(FrontBuildError):
        FrontRelease.load(root)


def test_dynamic_page_outside_pages_dir_is_refused(tmp_path: Path) -> None:
    root = _write_release(tmp_path, RELEASE_A, dynamic={"/units/[code]/": "../routes.json"})
    with pytest.raises(FrontBuildError, match="pages/"):
        FrontRelease.load(root)
