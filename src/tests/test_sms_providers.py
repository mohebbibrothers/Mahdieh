"""تست‌های آداپتورهای پیامک — بدون یک بایت ترافیک شبکه.

سه خطر واقعی که این‌جا بسته می‌شود:
    ۱) **ارسال ناخواسته در محیط آزمایشی:** اگر ارائه‌دهندهٔ واقعی «محلی» شمرده
       شود، با ``SMS_ENABLED=False`` هم پیامک واقعی می‌رود (هزینه و آبرو).
    ۲) **شکستن جریان اصلی:** خطای شبکه یا کلید نامعتبر نباید هیچ کنشی در سایت
       را زمین بزند؛ ``send_sms`` همیشه یک ردیف تحویل برمی‌گرداند.
    ۳) **قالب شماره:** درگاه‌های ایرانی شمارهٔ محلی (``0912…``) می‌خواهند، سامانه
       E.164 (``+98912…``) نگه می‌دارد؛ تبدیل باید یک‌جا و آزموده باشد.
"""

from __future__ import annotations

from typing import Any
from urllib.error import URLError

from django.test import override_settings
import pytest

from apps.notifications import services as sms_services
from apps.notifications.choices import SmsStatus, SmsTemplateKey
from apps.notifications.models import SmsDelivery
from apps.notifications.ports import SmsMessage, SmsReceipt
from apps.notifications.providers import (
    LOCAL_PROVIDERS,
    NETWORK_PROVIDERS,
    GhasedakSmsProvider,
    KavenegarSmsProvider,
    MockSmsProvider,
    get_sms_provider,
    is_local_provider,
    provider_catalog,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

PHONE = "+989121234567"


class _FakeResponse:
    """پاسخ شبیه‌سازی‌شدهٔ HTTP — همان چیزی که ``urlopen`` برمی‌گرداند."""

    def __init__(self, payload: str) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._payload.encode("utf-8")


def _capture_urlopen(monkeypatch, payload: str) -> list[Any]:
    """جایگزینی ``urlopen`` و ثبت درخواست‌ها (تضمین: هیچ تماس واقعی)."""
    calls: list[Any] = []

    def fake_urlopen(request: Any, timeout: Any = None) -> _FakeResponse:
        calls.append((request, timeout))
        return _FakeResponse(payload)

    monkeypatch.setattr("apps.notifications.providers.urlopen", fake_urlopen)
    return calls


def test_local_and_network_provider_sets_are_disjoint() -> None:
    """مجموعهٔ آداپتورهای محلی و شبکه‌ای تفکیک‌شده و ناهم‌پوشان است."""
    assert {"mock", "console"} == LOCAL_PROVIDERS
    # ایران‌پیامک با «ارسال الگومحور» اضافه شد (ADR-0013) — همچنان آداپتور واقعی.
    assert {"kavenegar", "ghasedak", "iranpayamak"} == NETWORK_PROVIDERS
    assert not (LOCAL_PROVIDERS & NETWORK_PROVIDERS)


def test_real_provider_is_not_local() -> None:
    """ارائه‌دهندهٔ واقعی هرگز «محلی» شمرده نمی‌شود — سد ارسال ناخواسته."""
    assert is_local_provider("mock") is True
    assert is_local_provider("console") is True
    assert is_local_provider("kavenegar") is False
    assert is_local_provider("ghasedak") is False
    assert is_local_provider("iranpayamak") is False


def test_unknown_provider_falls_back_to_mock() -> None:
    """نام ناشناخته، سامانه را نمی‌شکند؛ ماک جایگزین می‌شود."""
    provider = get_sms_provider("no-such-provider")
    assert isinstance(provider, MockSmsProvider)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+989121234567", "09121234567"),
        ("00989121234567", "09121234567"),
        ("09121234567", "09121234567"),
        ("9121234567", "09121234567"),
    ],
)
def test_gateway_phone_conversion(raw: str, expected: str) -> None:
    """تبدیل شماره به قالب درگاه ایرانی، برای همهٔ قالب‌های ورودی."""
    assert KavenegarSmsProvider.gateway_phone(raw) == expected


def test_real_provider_without_key_never_calls_network(monkeypatch) -> None:
    """نبود کلید API ⇒ رسید ناموفق بدون هیچ تماسی (سد هزینه)."""
    calls: list[Any] = []
    monkeypatch.setattr(
        "apps.notifications.providers.urlopen",
        lambda *a, **k: calls.append(a) or _FakeResponse("{}"),
    )
    provider = KavenegarSmsProvider()
    receipt = provider.send(SmsMessage(phone=PHONE, body="سلام", template_key="dry_run"))
    assert receipt.ok is False
    assert "کلید" in receipt.error
    assert calls == []


@override_settings(SMS_API_KEY="test-key", SMS_SENDER_NUMBER="10004346")
def test_kavenegar_builds_expected_request(monkeypatch) -> None:
    """درخواست کاوه‌نگار: نشانی، receptor محلی، message و sender."""
    calls = _capture_urlopen(
        monkeypatch,
        '{"return": {"status": 200, "message": "ok"}, "entries": [{"messageid": 98765}]}',
    )
    provider = KavenegarSmsProvider()
    receipt = provider.send(
        SmsMessage(phone=PHONE, body="مهدیه — کد شما: ۱۲۳۴", template_key="otp_login")
    )
    assert receipt.ok is True
    assert receipt.provider == "kavenegar"
    assert receipt.provider_ref == "98765"
    request, timeout = calls[0]
    assert request.full_url == "https://api.kavenegar.com/v1/test-key/sms/send.json"
    body = request.data.decode()
    assert "receptor=09121234567" in body
    assert "sender=10004346" in body
    assert timeout == provider.timeout_seconds


@override_settings(SMS_API_KEY="test-key", SMS_SENDER_NUMBER="")
def test_kavenegar_provider_error_is_reported(monkeypatch) -> None:
    """پاسخ ناموفق ارائه‌دهنده ⇒ رسید ناموفق با پیام همان ارائه‌دهنده."""
    _capture_urlopen(monkeypatch, '{"return": {"status": 424, "message": "Invalid data"}}')
    receipt = KavenegarSmsProvider().send(SmsMessage(phone=PHONE, body="x", template_key="dry_run"))
    assert receipt.ok is False
    assert "Invalid data" in receipt.error


@override_settings(SMS_API_KEY="test-key")
def test_network_failure_returns_receipt_not_exception(monkeypatch) -> None:
    """قطع شبکه ⇒ رسید ناموفق؛ هیچ استثنایی به لایهٔ دامنه نمی‌رسد."""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise URLError("network down")

    monkeypatch.setattr("apps.notifications.providers.urlopen", boom)
    receipt = KavenegarSmsProvider().send(SmsMessage(phone=PHONE, body="x", template_key="dry_run"))
    assert receipt.ok is False
    assert receipt.provider == "kavenegar"


@override_settings(SMS_API_KEY="test-key")
def test_invalid_json_is_handled(monkeypatch) -> None:
    """پاسخ غیر JSON (مثلاً صفحهٔ خطای HTML) رسید ناموفق می‌دهد."""
    _capture_urlopen(monkeypatch, "<html>502 Bad Gateway</html>")
    receipt = KavenegarSmsProvider().send(SmsMessage(phone=PHONE, body="x", template_key="dry_run"))
    assert receipt.ok is False


@override_settings(SMS_API_KEY="test-key")
def test_ghasedak_uses_header_key(monkeypatch) -> None:
    """قاصدک کلید را در سرصفحهٔ ``apikey`` می‌فرستد و ``result.code`` را می‌خواند."""
    calls = _capture_urlopen(
        monkeypatch, '{"result": {"code": 200, "message": "ok"}, "items": [{"messageid": 55}]}'
    )
    receipt = GhasedakSmsProvider().send(SmsMessage(phone=PHONE, body="x", template_key="dry_run"))
    assert receipt.ok is True and receipt.provider_ref == "55"
    request, _timeout = calls[0]
    assert request.get_header("Apikey") == "test-key"


@override_settings(SMS_ENABLED=True, SMS_PROVIDER="kavenegar", SMS_API_KEY="test-key")
def test_send_sms_records_successful_delivery(monkeypatch) -> None:
    """مسیر کامل ``send_sms`` با ارائه‌دهندهٔ واقعی: ردیف تحویل «ارسال‌شده»."""
    _capture_urlopen(
        monkeypatch,
        '{"return": {"status": 200}, "entries": [{"messageid": 1}]}',
    )
    delivery = sms_services.send_sms(
        phone=PHONE, template_key=SmsTemplateKey.DRY_RUN, context={"message": "آزمون"}
    )
    delivery.refresh_from_db()
    assert delivery.status == SmsStatus.SENT
    assert delivery.provider == "kavenegar"
    assert delivery.sent_at is not None


@override_settings(SMS_ENABLED=False, SMS_PROVIDER="kavenegar", SMS_API_KEY="test-key")
def test_disabled_sms_blocks_real_provider_before_network(monkeypatch) -> None:
    """با ``SMS_ENABLED=False`` حتی ارائه‌دهندهٔ واقعی هم فرستاده نمی‌شود."""

    def boom(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - نباید صدا زده شود
        raise AssertionError("شبکه نباید صدا زده شود")

    monkeypatch.setattr("apps.notifications.providers.urlopen", boom)
    delivery = sms_services.send_sms(
        phone=PHONE, template_key=SmsTemplateKey.DRY_RUN, context={"message": "آزمون"}
    )
    delivery.refresh_from_db()
    assert delivery.status == SmsStatus.BLOCKED
    assert "خاموش" in delivery.error


@override_settings(SMS_ENABLED=True, SMS_PROVIDER="mock")
def test_mock_provider_dry_run_marks_suppressed() -> None:
    """ماک در محیط توسعه: ردیف تحویل «ارسال‌نشده (غیرتولیدی)» با رسید آزمایشی."""
    delivery = sms_services.send_sms(
        phone=PHONE, template_key=SmsTemplateKey.DRY_RUN, context={"message": "آزمون"}
    )
    delivery.refresh_from_db()
    assert delivery.status == SmsStatus.SUPPRESSED
    assert delivery.provider == "mock"
    assert MockSmsProvider.last_for(PHONE) is not None


@override_settings(
    SMS_ENABLED=True,
    SMS_PROVIDER="kavenegar",
    SMS_API_KEY="test-key",
    SMS_FALLBACK_PROVIDER="console",
)
def test_fallback_provider_is_used_once(monkeypatch) -> None:
    """شکست درگاه اصلی ⇒ یک تلاش با درگاه پشتیبان و ثبت دو تلاش."""
    _capture_urlopen(monkeypatch, '{"return": {"status": 500, "message": "provider down"}}')
    delivery = sms_services.send_sms(
        phone=PHONE, template_key=SmsTemplateKey.DRY_RUN, context={"message": "آزمون"}
    )
    delivery.refresh_from_db()
    assert delivery.attempts == 2
    assert delivery.status == SmsStatus.SUPPRESSED  # کنسول، ارسال آزمایشی
    assert delivery.provider == "console"


@override_settings(SMS_ENABLED=True, SMS_PROVIDER="mock")
def test_send_sms_survives_crashing_provider(monkeypatch) -> None:
    """آداپتور خراب هم جریان را نمی‌شکند: ردیف تحویل «ناموفق» ثبت می‌شود."""

    class ExplodingProvider:
        """آداپتور بدرفتار — برای آزمودن سد ایمنی ``send_sms``."""

        name = "exploding"

        def send(self, message: SmsMessage) -> SmsReceipt:  # pragma: no cover
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "apps.notifications.services.get_sms_provider", lambda name=None: ExplodingProvider()
    )
    delivery = sms_services.send_sms(
        phone=PHONE, template_key=SmsTemplateKey.DRY_RUN, context={"message": "آزمون"}
    )
    delivery.refresh_from_db()
    assert delivery.status == SmsStatus.FAILED
    assert "boom" in delivery.error


def test_provider_catalog_reports_key_status() -> None:
    """فهرست آداپتورها برای صفحهٔ سلامت: نام، محلی‌بودن و وضعیت کلید."""
    catalog = {row["name"]: row for row in provider_catalog()}
    assert set(catalog) == {"mock", "console", "kavenegar", "ghasedak", "iranpayamak"}
    assert catalog["mock"]["local"] is True
    assert catalog["kavenegar"]["local"] is False
    assert catalog["kavenegar"]["has_api_key"] is False


def test_dedup_key_prevents_duplicate_rows() -> None:
    """کلید تکراری، ارسال دوباره نمی‌سازد (یک ردیف، همان ردیف)."""
    first = sms_services.send_sms(
        phone=PHONE,
        template_key=SmsTemplateKey.DRY_RUN,
        context={"message": "آزمون"},
        dedup_key="dup-1",
    )
    second = sms_services.send_sms(
        phone=PHONE,
        template_key=SmsTemplateKey.DRY_RUN,
        context={"message": "آزمون"},
        dedup_key="dup-1",
    )
    assert first.pk == second.pk
    assert SmsDelivery.objects.filter(dedup_key="dup-1").count() == 1
