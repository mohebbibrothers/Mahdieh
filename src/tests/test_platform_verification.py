"""احراز مالکیت ساماندهی (پیش‌نیاز رفع فیلتر) — تگ meta همهٔ صفحه‌ها + ‎/verify-platform.txt‎.

قفل‌های رفتاری:

۱. تگ ``platform-verification`` روی **هر** صفحهٔ فرانت (ایستا، پویا، ۴۰۴) و قالب‌های جنگو، درست پس
   از ‎<meta charset>‎ و پیش از فشرده‌سازی می‌نشیند؛ nonce و CSP دست نمی‌خورند.
۲. ``/verify-platform.txt`` فقط کدِ همان نام را بی سطر پایانی می‌دهد؛ بی‌کد ⇒ ۴۰۴.
۳. بازرس احراز روی دامنهٔ ثبت‌شده پاسخ می‌گیرد: «‎/‎» و پرونده از هدایت دامنهٔ یکتا معاف‌اند،
   ولی بقیهٔ مسیرها همچنان ۳۰۱ می‌شوند؛ پرونده از هدایت HTTPS تولید هم معاف است.
۴. مدخل نادرست هرگز به HTML نمی‌رسد و سیستم‌چک ``mahdieh.E022`` آن را لو می‌دهد.
"""

from __future__ import annotations

import gzip
from pathlib import Path
import re
from typing import Any

from django.conf import settings
from django.test import Client, override_settings
import pytest

from apps.core import verification
from apps.core.checks import PLATFORM_VERIFICATION_ERROR, platform_verification_check
from apps.inventory.models import Unit
from tests.test_frontend_serving import PLACEHOLDER, RELEASE_A, _activate, _write_release

pytestmark = pytest.mark.django_db

CODE = "yqknjaxgib"
META = f'<meta name="platform-verification" content="{CODE}">'
HOSTS = ["gam-khedmat.ir", "www.gam-khedmat.ir", "mahdei.n-cpanel.xyz", "testserver", "127.0.0.1"]


# ── ۱) تجزیهٔ پیکربندی و انتخاب کد ───────────────────────────────────────────────


@pytest.mark.unit
def test_parse_accepts_global_and_per_domain_codes_and_reports_the_rest() -> None:
    entries, invalid = verification.parse(
        [
            CODE,
            " GAM-KHEDMAT.ir = abc123 ",
            "",
            "bad code",
            "x=ab",
            "<script>=abcdef",
            "a..b=abcdef",
        ]
    )
    assert entries == (
        verification.Entry(host="", code=CODE),
        verification.Entry(host="gam-khedmat.ir", code="abc123"),
    )
    assert invalid == ("bad code", "x=ab", "<script>=abcdef", "a..b=abcdef")


@pytest.mark.unit
def test_domain_codes_come_first_cover_www_and_never_leak_to_other_domains(settings: Any) -> None:
    settings.PLATFORM_VERIFICATION = ["gam-khedmat.ir=aaaa11", "www.new.ir=bbbb22", CODE]
    assert verification.codes_for("gam-khedmat.ir") == ("aaaa11", CODE)
    assert verification.codes_for("WWW.GAM-KHEDMAT.IR") == ("aaaa11", CODE)
    assert verification.codes_for("new.ir") == ("bbbb22", CODE)
    assert verification.codes_for("mahdei.n-cpanel.xyz") == (CODE,)
    settings.PLATFORM_VERIFICATION = ["gam-khedmat.ir=aaaa11"]
    assert verification.codes_for("mahdei.n-cpanel.xyz") == ()


@pytest.mark.unit
def test_injection_lands_right_after_the_charset_declaration(settings: Any) -> None:
    settings.PLATFORM_VERIFICATION = [CODE]
    page = b'<!doctype html><html><head><meta charSet="utf-8"/><title>t</title></head></html>'
    out = verification.inject(page, "gam-khedmat.ir")
    assert out == page.replace(b'"utf-8"/>', b'"utf-8"/>' + META.encode())
    # بی charset ⇒ پس از ‎<head>‎؛ بی head ⇒ دست‌نخورده؛ بی کد ⇒ دست‌نخورده.
    assert verification.inject(b"<head lang=fa><title>", "gam-khedmat.ir").startswith(
        b"<head lang=fa>" + META.encode()
    )
    assert verification.inject(b"<p>fragment</p>", "gam-khedmat.ir") == b"<p>fragment</p>"
    settings.PLATFORM_VERIFICATION = []
    assert verification.inject(page, "gam-khedmat.ir") == page


# ── ۲) پروندهٔ احراز ─────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_verification_file_returns_only_the_code(settings: Any) -> None:
    settings.PLATFORM_VERIFICATION = [CODE, "gam-khedmat.ir=domain1"]
    settings.ALLOWED_HOSTS = HOSTS
    response = Client().get("/verify-platform.txt", HTTP_HOST="gam-khedmat.ir")
    assert response.status_code == 200
    assert response.content == b"domain1"  # کد ویژهٔ دامنه بر کد سراسری مقدم است
    assert response["Content-Type"] == "text/plain; charset=utf-8"
    assert response["Cache-Control"] == "no-cache"
    other = Client().get("/verify-platform.txt", HTTP_HOST="mahdei.n-cpanel.xyz")
    assert other.content == CODE.encode()
    assert Client().head("/verify-platform.txt", HTTP_HOST="gam-khedmat.ir").status_code == 200
    assert Client().post("/verify-platform.txt", HTTP_HOST="gam-khedmat.ir").status_code == 405


@pytest.mark.integration
def test_verification_file_is_404_without_a_code(settings: Any) -> None:
    settings.PLATFORM_VERIFICATION = []
    assert Client().get("/verify-platform.txt").status_code == 404


# ── ۳) دامنهٔ یکتا و HTTPS ───────────────────────────────────────────────────────


@pytest.fixture
def canonical(settings: Any) -> Any:
    settings.SITE_URL = "https://mahdei.n-cpanel.xyz"
    settings.ALLOWED_HOSTS = HOSTS
    settings.CANONICAL_HOST_REDIRECT = True
    settings.PLATFORM_VERIFICATION = ["gam-khedmat.ir=domain1"]
    return settings


@pytest.mark.integration
@pytest.mark.parametrize("host", ["gam-khedmat.ir", "www.gam-khedmat.ir"])
def test_the_inspector_reads_the_registered_domain_without_a_redirect(
    canonical: Any, host: str
) -> None:
    assert Client().get("/verify-platform.txt", HTTP_HOST=host).status_code == 200
    home = Client().get("/", HTTP_HOST=host)
    assert home.status_code == 200
    assert b'content="domain1"' in home.content
    # بقیهٔ مسیرها همچنان به دامنهٔ یکتا می‌روند (معافیت فقط برای بازرس است).
    other = Client().get("/units/A1/", HTTP_HOST=host)
    assert other.status_code == 301
    assert other["Location"] == "https://mahdei.n-cpanel.xyz/units/A1/"


@pytest.mark.integration
def test_names_without_a_code_keep_the_canonical_redirect(canonical: Any) -> None:
    canonical.SITE_URL = "https://gam-khedmat.ir"
    response = Client().get("/", HTTP_HOST="mahdei.n-cpanel.xyz")
    assert response.status_code == 301
    assert response["Location"] == "https://gam-khedmat.ir/"


@pytest.mark.integration
def test_production_serves_the_file_over_plain_http_but_redirects_pages(settings: Any) -> None:
    settings.SECURE_SSL_REDIRECT = True
    settings.SECURE_REDIRECT_EXEMPT = [r"^verify-platform\.txt$"]
    settings.PLATFORM_VERIFICATION = [CODE]
    assert Client().get("/verify-platform.txt").content == CODE.encode()
    assert Client().get("/").status_code == 301


@pytest.mark.unit
def test_production_settings_carry_the_owner_code_and_the_https_exemption() -> None:
    source = (Path(settings.BASE_DIR) / "config" / "settings" / "production.py").read_text(
        encoding="utf-8"
    )
    assert f'PRODUCTION_PLATFORM_VERIFICATION = ("{CODE}",)' in source
    assert 'SECURE_REDIRECT_EXEMPT = [r"^verify-platform\\.txt$"]' in source
    # مقدار تهی در فایل محیط هم پیش‌فرض را خاموش نمی‌کند.
    assert 'env.list("MAHDIEH_PLATFORM_VERIFICATION", default=[]) or list(' in source


# ── ۴) صفحه‌های فرانت و قالب‌های جنگو ─────────────────────────────────────────────


@pytest.fixture
def front(tmp_path: Path):
    _write_release(tmp_path, RELEASE_A, dynamic={"/units/[code]/": "units/_/index.html"})
    current = _activate(tmp_path, RELEASE_A)
    enforced = {"SECURE_CSP": settings.CSP_POLICY_ENFORCED, "SECURE_CSP_REPORT_ONLY": {}}
    with override_settings(
        MAHDIEH_FRONT_DIR=str(current), PLATFORM_VERIFICATION=[CODE], **enforced
    ):
        yield Client()


@pytest.mark.integration
def test_every_front_page_carries_the_tag_and_keeps_its_nonce(front: Client) -> None:
    home = front.get("/")
    body = home.content.decode()
    assert body.count(META) == 1
    assert body.index(META) == body.index("<head>") + len("<head>")
    assert PLACEHOLDER not in body
    nonce = re.search(r"'nonce-([^']+)'", home.headers["Content-Security-Policy"])
    assert nonce
    assert f'nonce="{nonce.group(1)}"' in body
    assert int(home["Content-Length"]) == len(home.content)


@pytest.mark.integration
def test_gzipped_pages_carry_the_tag_too(front: Client) -> None:
    response = front.get("/", HTTP_ACCEPT_ENCODING="gzip")
    assert response["Content-Encoding"] == "gzip"
    assert META.encode() in gzip.decompress(response.content)


@pytest.mark.integration
def test_dynamic_unit_pages_carry_the_tag(
    tmp_path: Path, settings: Any, units: dict[str, Unit]
) -> None:
    del units
    root = _write_release(tmp_path, RELEASE_A, dynamic={"/units/[code]/": "units/_/index.html"})
    (root / "pages" / "units" / "_" / "index.html").write_text(
        f'<html><head><meta charset="utf-8"></head><main>UNIT</main>'
        f'<script nonce="{PLACEHOLDER}"></script>',
        encoding="utf-8",
    )
    settings.PLATFORM_VERIFICATION = [CODE]
    with override_settings(MAHDIEH_FRONT_DIR=str(_activate(tmp_path, RELEASE_A))):
        unit = Client().get("/units/A1/")
    assert unit.status_code == 200
    assert META.encode() in unit.content


@pytest.mark.integration
def test_django_templates_carry_the_tag(settings: Any) -> None:
    settings.PLATFORM_VERIFICATION = [CODE]
    settings.MAHDIEH_FRONT_DIR = ""
    home = Client().get("/")
    assert home.status_code == 200
    assert META.encode() in home.content


# ── ۵) سیستم‌چک ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_bad_entries_are_reported_and_never_injected(settings: Any) -> None:
    settings.PLATFORM_VERIFICATION = [CODE, 'evil"><script>']
    issues = platform_verification_check()
    assert [issue.id for issue in issues] == [PLATFORM_VERIFICATION_ERROR]
    assert verification.meta_tags("gam-khedmat.ir") == META
    settings.PLATFORM_VERIFICATION = [CODE]
    assert platform_verification_check() == []
