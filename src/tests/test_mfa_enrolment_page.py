"""صفحهٔ فعال‌سازی عامل دوم کارکنان — QR باید روی پس‌زمینهٔ تیره هم دیده و اسکن شود.

درس واقعی (گام ۸ب): مدیر روی میزبان QR را ندید. سه علت هم‌زمان بود:
  ۱. QR شفاف (``light=None``) و تیره روی صفحهٔ سرمه‌ای ⇒ نامرئی؛
  ۲. استایل‌های درون‌خطی قالب زیر CSP «style-src 'self'» مسدود می‌شدند؛
  ۳. سربرگ چسبان گوشی ۳۶۲px بود و QR را می‌پوشاند (ظرف flex-wrap نداشت).
این آزمون‌ها قرارداد هر سه را نگه می‌دارند؛ تصویر واقعی با Chromium و zbar وارسی شد.
"""

from __future__ import annotations

from pathlib import Path
import re

from django.test import Client, override_settings
import pytest

from apps.identity.mfa import _qr_svg
from tests.factories import make_staff

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "src" / "apps" / "web" / "templates" / "web"
CSS = REPO_ROOT / "src" / "apps" / "web" / "static" / "web" / "css" / "mahdieh.css"
STYLE_ATTR = re.compile(r"\sstyle=\"")


def test_qr_is_scannable_on_any_background() -> None:
    svg = _qr_svg("otpauth://totp/Mahdieh:test?secret=JBSWY3DPEHPK3PXP&issuer=Mahdieh")
    assert svg.startswith("<svg ")
    assert "viewBox=" in svg  # اندازه با CSS، بی اعوجاج
    assert not re.search(r"<svg[^>]*\swidth=", svg)  # ابعاد ثابت CSS را بی‌اثر می‌کرد
    assert 'fill="#fff"' in svg  # پس‌زمینهٔ سفید درون خود QR
    assert "<title>" in svg  # برای صفحه‌خوان
    assert "<?xml" not in svg


@pytest.mark.parametrize("template", ["mfa.html", "base.html"])
def test_staff_critical_templates_have_no_inline_style(template: str) -> None:
    source = (TEMPLATES / template).read_text(encoding="utf-8")
    assert not STYLE_ATTR.search(source), "CSP ویژگی style را مسدود می‌کند؛ کلاس CSS بسازید."


def test_htmx_does_not_inject_inline_styles() -> None:
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert '"includeIndicatorStyles": false' in base
    css = CSS.read_text(encoding="utf-8")
    assert ".htmx-request .htmx-indicator" in css
    assert ".htmx-request.htmx-indicator" in css


def test_mobile_header_wraps_instead_of_squeezing_the_nav() -> None:
    css = CSS.read_text(encoding="utf-8")
    mobile = css[css.index("@media (max-width: 640px)") :]
    mobile = mobile[: mobile.index("\n}\n") + 3]
    assert re.search(r"\.site-header__inner\s*\{[^}]*flex-wrap:\s*wrap", mobile)
    assert re.search(r"\.site-header\s*\{[^}]*position:\s*static", mobile)
    assert re.search(r"\.site-nav\s*\{[^}]*overflow-x:\s*auto", mobile)


@pytest.mark.django_db
@override_settings(STAFF_MFA_REQUIRED=True)
def test_enrolment_page_renders_the_qr_and_manual_key() -> None:
    client = Client()
    client.force_login(make_staff())
    page = client.get("/board/mfa/")
    assert page.status_code == 200
    html = page.content.decode()
    qr = html[html.index('<div class="mfa__qr">') :]
    assert qr[: qr.index("</div>")].count("<svg") == 1
    assert 'class="ltr mfa__secret"' in html
    body = html[html.index("<main") :]
    assert not STYLE_ATTR.search(body[: body.index("</main>")])
