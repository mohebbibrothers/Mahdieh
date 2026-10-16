"""اطلاعات تماس پذیرش + فرادادهٔ سرویس — پرسش «اینها به کار بک‌اند چه می‌آید؟».

این فایل، پاسخ **اجراشدنی** آن پرسش است. سه چیز را قفل می‌کند:

۱. **نشانی و تلفن دادهٔ کسب‌وکار نیستند** ⇒ در پایگاه‌داده، مهاجرت و مدل نیستند؛
   فقط تنظیمات استقرارند. (این تست، نبودشان در لایهٔ داده را هم می‌سنجد تا روزی
   کسی «برای راحتی» آن‌ها را به مدل اضافه نکند.)
۲. **اگر تنظیم نشده باشند، هیچ شماره/نشانی ساختگی چاپ نمی‌شود**: صفحهٔ راهنما
   بخش تماس را پنهان می‌کند و ``GET /api/v1/meta`` مقدار ``null`` می‌دهد — نه
   رشتهٔ خالی، که می‌تواند به دکمهٔ تماس بی‌شماره تبدیل شود.
۳. **یک منبع حقیقت**: از ``/api/v1/meta`` منتشر می‌شوند تا صفحهٔ راهنما،
   نمونه‌های SSR و فرانت واگرا نشوند؛ ولی تیم فرانت مختار است نسخهٔ خودش را
   داشته باشد و این کلید را نادیده بگیرد.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Warning as CheckWarning
from django.test import override_settings
import pytest
from rest_framework.test import APIClient

from apps.core.checks import CONTACT_WARNING, support_contact_check
from apps.notifications import pattern_texts

PHONE = "05138000000"
ADDRESS = "مشهد، نوغان، کوچه ۱۱ شهید قائمی، درب سوم"

pytestmark = pytest.mark.django_db


# ───────────────────────── لایهٔ داده: هیچ‌جا ذخیره نمی‌شود ─────────────────────
def test_contact_is_not_persisted_data() -> None:
    """هیچ مدلی فیلد نشانی/تلفن پذیرش ندارد — این‌ها تنظیمات‌اند، نه داده."""
    from django.apps import apps

    forbidden = ("support_address", "support_phone", "property_address", "contact_phone")
    offenders: list[str] = []
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if getattr(field, "name", "").lower() in forbidden:
                offenders.append(f"{model._meta.label}.{field.name}")
    assert offenders == [], f"نشانی/تلفن پذیرش نباید در مدل باشد: {offenders}"

    # و متن الگوها هم به‌جای پایگاه‌داده، در `pattern_texts` زندگی می‌کند.
    assert ADDRESS not in str(settings.DATABASES)


# ───────────────────────────── سیستم‌چک (هشدار عملیاتی) ─────────────────────────
def _contact_warnings() -> list[CheckWarning]:
    """هشدارهای مربوط به اطلاعات تماس از اجرای مستقیم چک."""
    return [issue for issue in support_contact_check() if issue.id == CONTACT_WARNING]


@override_settings(ENVIRONMENT="production", SUPPORT_PHONE="", SUPPORT_ADDRESS="")
def test_production_without_contact_warns_but_does_not_fail() -> None:
    """در تولید، خالی‌بودن تماس «هشدار» است نه «خطا»: سامانه بدون آن سالم است."""
    issues = _contact_warnings()
    assert len(issues) == 1
    assert isinstance(issues[0], CheckWarning)
    assert "MAHDIEH_SUPPORT_PHONE" in issues[0].msg
    assert "MAHDIEH_SUPPORT_ADDRESS" in issues[0].msg


@override_settings(ENVIRONMENT="production", SUPPORT_PHONE=PHONE, SUPPORT_ADDRESS=ADDRESS)
def test_production_with_contact_is_silent() -> None:
    """وقتی تنظیم شده باشد، چک هیچ صدایی نمی‌دهد (هشدار بی‌مورد = بی‌اعتباری هشدار)."""
    assert _contact_warnings() == []


@override_settings(ENVIRONMENT="development", SUPPORT_PHONE="", SUPPORT_ADDRESS="")
def test_development_never_warns_about_contact() -> None:
    """در توسعه، خالی‌بودن طبیعی است و نباید سر‌و‌صدا ایجاد کند."""
    assert _contact_warnings() == []


# ─────────────────────── انتشار یک‌باره در /api/v1/meta ────────────────────────
def test_meta_publishes_contact_when_configured() -> None:
    """فرانت با یک درخواست، نسخهٔ رسمی نشانی/تلفن را می‌گیرد (یک منبع حقیقت)."""
    with override_settings(SUPPORT_PHONE=PHONE, SUPPORT_ADDRESS=ADDRESS):
        response = APIClient().get("/api/v1/meta")
    assert response.status_code == 200
    assert response.json()["contact"] == {"phone": PHONE, "address": ADDRESS}


def test_meta_returns_null_contact_when_unset() -> None:
    """تنظیم‌نشده ⇒ ``null``؛ فرانت می‌فهمد «بک‌اند نسخه‌ای منتشر نکرده»."""
    with override_settings(SUPPORT_PHONE="", SUPPORT_ADDRESS=""):
        response = APIClient().get("/api/v1/meta")
    assert response.status_code == 200
    assert response.json()["contact"] is None


def test_meta_never_returns_blank_phone_string() -> None:
    """نشانی بی‌تلفن: فقط نشانی می‌آید و کلید تلفن رشتهٔ خالی است، نه ``None``."""
    with override_settings(SUPPORT_PHONE="", SUPPORT_ADDRESS=ADDRESS):
        payload = APIClient().get("/api/v1/meta").json()
    assert payload["contact"] == {"phone": "", "address": ADDRESS}


# ────────────── پیوند با پیامک: نشانی داخل متن الگو، نه در داده ───────────────
def test_sms_texts_embed_the_address_only_where_it_is_needed() -> None:
    """نشانی رسمی در همان الگوهایی می‌آید که زائر باید بداند کجا برود."""
    from apps.notifications.choices import SmsTemplateKey

    carrying = {draft.key for draft in pattern_texts.DRAFTS if ADDRESS in draft.body}
    assert carrying == {
        # متن مرجعی که کارفرما تأیید کرد، نشانی را در دلِ پیامِ تأیید رزرو دارد.
        SmsTemplateKey.BOOKING_CONFIRMED,
        SmsTemplateKey.BOOKING_REMINDER,
        SmsTemplateKey.OFFER_ACCEPTED,
    }, "نشانی باید فقط در «تأیید رزرو»، «یادآور پیش از پذیرش» و «تأیید پذیرش پیشنهاد» باشد."


def test_address_line_matches_the_official_address() -> None:
    """نشانی متن الگوها، همان نشانی‌ای است که کارفرما اعلام کرده است."""
    assert f"نشانی: {ADDRESS}" == pattern_texts.ADDRESS_LINE
