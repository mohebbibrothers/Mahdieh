"""تست‌های پیامک ایران‌پیامک — «ارسال الگومحور» بدون یک بایت ترافیک شبکه.

سه خطری که این‌جا بسته می‌شود:
    ۱) **پیام درهم**: الگوی پنل جای‌خالی دارد (``{name}``)؛ اگر متغیرش نرسد،
       زائر پیامک بی‌معنا می‌گیرد. نگاشت باید ارسال را *مسدود* کند، نه ناقص بفرستد.
    ۲) **موفقِ اشتباه**: اگر پاسخ ناشناختهٔ پنل «موفق» تفسیر شود، مدیر خیال
       می‌کند پیامک رفته و کسی پیگیری نمی‌کند.
    ۳) **ارسال ناخواسته**: آداپتور واقعی هرگز نباید در محیط آزمایشی پیامک بفرستد؛
       تست‌ها با monkeypatch ثابت می‌کنند هیچ تماسی رخ نمی‌دهد.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
from typing import Any

from django.core.checks import Error, Warning
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.utils import timezone
import pytest

from apps.notifications import patterns
from apps.notifications.checks import sms_readiness
from apps.notifications.choices import SmsStatus, SmsTemplateKey
from apps.notifications.management.commands.sms_patterns import Command as SmsPatternsCommand
from apps.notifications.models import SmsDelivery
from apps.notifications.ports import SmsMessage
from apps.notifications.providers import (
    NETWORK_PROVIDERS,
    IranPayamakSmsProvider,
    is_local_provider,
    provider_catalog,
)
from apps.notifications.services import can_render, send_sms

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

PHONE = "+989121234567"
LINE = "50002178584000"

#: «پنل آزمون»: کدهایی که همهٔ الگوها را تأییدشده فرض می‌کنند.
#: چرا؟ این فایل **سازوکار** ارسال را می‌سنجد، نه وضعیت تأیید پنل. وضعیت واقعی
#: امروز (هیچ الگویی تأیید نشده) در دو آزمون صریح با کدهای تهی سنجیده می‌شود.
TEST_PATTERN_CODES: dict[str, str] = {
    key: f"Test{index:02d}Code" for index, key in enumerate(SmsTemplateKey.values, start=1)
}


@pytest.fixture(autouse=True)
def _approved_panel_codes() -> Iterator[None]:
    """برای همهٔ آزمون‌های این فایل، پنل را «همه‌چیز تأییدشده» فرض می‌کند."""
    with override_settings(SMS_PATTERN_CODES=json.dumps(TEST_PATTERN_CODES)):
        yield


class _FakeResponse:
    """پاسخ شبیه‌سازی‌شدهٔ HTTP برای ``urlopen``."""

    def __init__(self, payload: str) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._payload.encode("utf-8")


def _capture_urlopen(monkeypatch: Any, payload: str) -> list[Any]:
    """جایگزینی ``urlopen`` و ثبت درخواست‌ها — تضمین «هیچ تماس واقعی»."""
    calls: list[Any] = []

    def fake_urlopen(request: Any, timeout: Any = None) -> _FakeResponse:
        calls.append((request, timeout))
        return _FakeResponse(payload)

    monkeypatch.setattr("apps.notifications.providers.urlopen", fake_urlopen)
    return calls


def _forbid_network(monkeypatch: Any) -> None:
    """هر تماس شبکه‌ای را شکست می‌دهد — برای مسیرهایی که باید محلی بمانند."""

    def boom(*args: Any, **kwargs: Any) -> None:
        msg = "شبکه فراخوانی شد، در حالی که نباید"
        raise AssertionError(msg)

    monkeypatch.setattr("apps.notifications.providers.urlopen", boom)


@pytest.fixture
def iranpayamak_settings(settings: Any) -> Any:
    """تنظیمات فعال ایران‌پیامک (کلید و خط) برای تست‌های ارسال.

    نکتهٔ فنی: ``override_settings`` وقتی از فیکسچر *بازگردانده* شود فعال نمی‌شود؛
    فیکسچر ``settings`` خودِ pytest-django تنها راه امن (و خودکار پاک‌شونده) است.
    """
    settings.SMS_PROVIDER = "iranpayamak"
    settings.SMS_ENABLED = True
    settings.SMS_API_KEY = "test-api-key"
    settings.SMS_SENDER_NUMBER = LINE
    return settings


# ── نگاشت الگوها ────────────────────────────────────────────────────────────


@override_settings(SMS_PATTERN_CODES="")
def test_no_pattern_is_wired_before_the_domain_is_live() -> None:
    """واقعیت امروز: پنل، الگوها را پیش از بالا آمدن دامنه تأیید نمی‌کند.

    پس همهٔ کدها تهی‌اند و سامانه باید **صادقانه** بگوید «پیامک آماده نیست» —
    نه این‌که با کد قدیمیِ ردشده، ارسال را تلاش کند و خطای پنل بخورد.
    """
    codes = patterns.pattern_codes()
    assert codes[SmsTemplateKey.BOOKING_CONFIRMED] == ""
    assert codes[SmsTemplateKey.OTP_LOGIN] == ""
    assert codes[SmsTemplateKey.WELCOME] == ""
    assert set(patterns.pattern_readiness()["bound"]) == set()


@override_settings(
    SMS_PATTERN_CODES=json.dumps({"booking_confirmed": "Zz9Yy8Xx7", "otp_login": "AbC123xyz"})
)
def test_delivered_codes_wire_the_pattern_without_code_changes() -> None:
    """کدی که از پنل می‌رسد، بی‌تغییرِ کد برنامه به قالب وصل می‌شود."""
    resolved = patterns.resolve_binding(SmsTemplateKey.BOOKING_CONFIRMED)
    assert resolved is not None
    key, binding = resolved
    assert key == SmsTemplateKey.BOOKING_CONFIRMED
    assert binding.code == "Zz9Yy8Xx7"
    assert binding.attributes["full_name"] == "name"
    assert binding.attributes["rooz"] == "rooz"


@override_settings(SMS_PATTERN_CODES=json.dumps({"booking_confirmed": "Zz9Yy8Xx7"}))
def test_missing_pattern_moves_to_fallback_template() -> None:
    """«تغییر تاریخ» کد ندارد ⇒ به «تأیید رزرو» افت می‌کند، نه ارسال ناموفق."""
    resolved = patterns.resolve_binding(SmsTemplateKey.BOOKING_MOVED)
    assert resolved is not None
    key, binding = resolved
    assert key == SmsTemplateKey.BOOKING_CONFIRMED
    assert binding.code == "Zz9Yy8Xx7"


@override_settings(SMS_PATTERN_CODES="")
def test_offer_created_has_no_pattern_at_all() -> None:
    """پیشنهاد سانس آزادشده بی‌کد است ⇒ نگاشت باید ``None`` بدهد (مسدود، نه درهم)."""
    assert patterns.resolve_binding(SmsTemplateKey.OFFER_CREATED) is None


def test_pattern_codes_can_be_overridden_by_environment() -> None:
    """مالک پنل کد تازه می‌گیرد ⇒ فقط متغیر محیطی عوض می‌شود، نه کد سامانه."""
    with override_settings(SMS_PATTERN_CODES=json.dumps({"otp_login": "NewCode99"})):
        resolved = patterns.resolve_binding(SmsTemplateKey.OTP_LOGIN)
        assert resolved is not None
        assert resolved[1].code == "NewCode99"


def test_malformed_pattern_codes_fail_fast() -> None:
    """JSON خراب باید در زمان راه‌اندازی فریاد بزند، نه در زمان ارسال."""
    with override_settings(SMS_PATTERN_CODES="{not-json"), pytest.raises(ImproperlyConfigured):
        patterns.pattern_codes()


def test_build_attributes_maps_and_persianizes() -> None:
    """متغیرهای داخلی به نام‌های پنل نگاشت و ارقام فارسی می‌شوند."""
    _key, binding = patterns.resolve_binding(SmsTemplateKey.BOOKING_CONFIRMED)  # type: ignore[misc]
    attributes, missing = patterns.build_attributes(
        binding, {"full_name": "زائر آزمون", "rooz": "2.5"}
    )
    assert attributes == {"name": "زائر آزمون", "rooz": "۲٫۵"}
    assert missing == ()


def test_build_attributes_reports_missing_required() -> None:
    """نبود متغیر الزامی، «غایب» گزارش می‌شود تا ارسال مسدود شود."""
    _key, binding = patterns.resolve_binding(SmsTemplateKey.BOOKING_CONFIRMED)  # type: ignore[misc]
    attributes, missing = patterns.build_attributes(binding, {"full_name": "زائر"})
    assert attributes == {"name": "زائر"}
    assert missing == ("rooz",)


def test_latin_digit_mode_is_configurable() -> None:
    """اگر پنل ارقام لاتین بخواهد، یک متغیر محیطی کافی است."""
    with override_settings(SMS_ATTRIBUTE_DIGITS="latin"):
        _key, binding = patterns.resolve_binding(SmsTemplateKey.BOOKING_CONFIRMED)  # type: ignore[misc]
        attributes, _missing = patterns.build_attributes(
            binding, {"full_name": "زائر", "rooz": "۲٫۵"}
        )
    assert attributes["rooz"] == "۲٫۵"  # ورودی فارسی دست‌نخورده می‌ماند


# ── آداپتور ─────────────────────────────────────────────────────────────────


def test_iranpayamak_is_registered_as_real_provider() -> None:
    """ایران‌پیامک آداپتور واقعی است: نه محلی، و در فهرست ارائه‌دهنده‌ها."""
    assert "iranpayamak" in NETWORK_PROVIDERS
    assert is_local_provider("iranpayamak") is False
    assert any(item["name"] == "iranpayamak" for item in provider_catalog())


@override_settings(SMS_PATTERN_CODES=json.dumps({"booking_confirmed": "AbC123xyz"}))
def test_supports_template_reflects_registry() -> None:
    """آداپتور می‌داند کدام قالب را می‌تواند برساند و کدام را نه (پنل جزئی)."""
    provider = IranPayamakSmsProvider()
    assert provider.supports_template(SmsTemplateKey.BOOKING_CONFIRMED) is True
    assert provider.supports_template(SmsTemplateKey.OFFER_CREATED) is False


def test_build_request_shape_matches_panel_contract(iranpayamak_settings: Any) -> None:
    """بدنهٔ درخواست دقیقاً همان قرارداد پنل است (code/attributes/recipient/…)."""
    provider = IranPayamakSmsProvider()
    message = SmsMessage(
        phone=PHONE,
        body="بدنهٔ متنی که نباید ارسال شود",
        template_key=SmsTemplateKey.BOOKING_CONFIRMED,
        context={"full_name": "زائر آزمون", "rooz": "2"},
    )
    url, body, headers = provider.build_request(message)
    payload = json.loads(body.decode("utf-8"))
    assert url.endswith("/ws/v1/sms/pattern")
    assert payload["code"] == TEST_PATTERN_CODES[SmsTemplateKey.BOOKING_CONFIRMED]
    assert payload["attributes"] == {"name": "زائر آزمون", "rooz": "۲"}
    assert payload["recipient"] == "09121234567"
    assert payload["line_number"] == LINE
    assert payload["number_format"] == "persian"
    assert "body" not in payload  # متن آزاد هرگز فرستاده نمی‌شود
    assert headers["Api-Key"] == "test-api-key"


def test_send_unbound_template_never_touches_network(
    monkeypatch: Any, iranpayamak_settings: Any
) -> None:
    """قالب بی‌الگو ⇒ رسید ناموفق بدون هیچ تماس شبکه‌ای."""
    _forbid_network(monkeypatch)
    receipt = IranPayamakSmsProvider().send(
        SmsMessage(
            phone=PHONE,
            body="x",
            template_key=SmsTemplateKey.OFFER_CREATED,
            context={},
        )
    )
    assert receipt.ok is False
    assert "الگو" in receipt.error


def test_send_with_missing_attribute_is_refused(
    monkeypatch: Any, iranpayamak_settings: Any
) -> None:
    """متغیر غایب ⇒ ارسال نمی‌شود (قاعدهٔ «هرگز پیام درهم»)."""
    _forbid_network(monkeypatch)
    receipt = IranPayamakSmsProvider().send(
        SmsMessage(
            phone=PHONE,
            body="x",
            template_key=SmsTemplateKey.BOOKING_CONFIRMED,
            context={"full_name": "زائر"},
        )
    )
    assert receipt.ok is False
    assert "rooz" in receipt.error


@pytest.mark.parametrize(
    ("payload", "expected_ok", "expected_ref"),
    [
        ({"status": 200, "data": {"messageId": 991}}, True, "991"),
        ({"code": 200}, True, ""),
        ({"success": True, "messageid": "abc"}, True, "abc"),
        ({"message_id": 7}, True, "7"),
        ({"status": "موفق"}, True, ""),
        ({"status": 400, "message": "invalid line"}, False, ""),
        ({"status": 401, "message": "کلید نامعتبر"}, False, ""),
        ({"status": "خطا"}, False, ""),
    ],
)
def test_response_parsing_known_shapes(
    payload: dict[str, Any], expected_ok: bool, expected_ref: str
) -> None:
    """شکل‌های شناخته‌شدهٔ پاسخ پنل، درست تفسیر می‌شوند."""
    receipt = IranPayamakSmsProvider().parse_response(payload)
    assert receipt.ok is expected_ok
    if expected_ok:
        assert receipt.provider_ref == expected_ref


def test_unknown_response_shape_is_failure_not_success() -> None:
    """پاسخ ناشناخته «موفق» فرض نمی‌شود — خطای همراه بدنه، برای اضافه‌کردن شکل تازه."""
    receipt = IranPayamakSmsProvider().parse_response({"weird": "shape"})
    assert receipt.ok is False
    assert "ناشناخته" in receipt.error


def test_empty_response_is_failure() -> None:
    """بدنهٔ خالی ⇒ شکست (نه سکوت)."""
    assert IranPayamakSmsProvider().parse_response({}).ok is False


def test_send_success_records_provider_reference(
    monkeypatch: Any, iranpayamak_settings: Any
) -> None:
    """ارسال موفق: رکورد تحویل با ``provider_ref`` پنل ثبت می‌شود."""
    calls = _capture_urlopen(monkeypatch, '{"status": 200, "data": {"messageId": 4242}}')
    delivery = send_sms(
        phone=PHONE,
        template_key=SmsTemplateKey.BOOKING_CONFIRMED,
        context={"full_name": "زائر آزمون", "rooz": "2"},
        dedup_key="test:iranpayamak:ok",
    )
    assert len(calls) == 1
    assert delivery.status == SmsStatus.SENT
    assert delivery.provider == "iranpayamak"
    assert delivery.provider_ref == "4242"
    assert delivery.phone_masked.endswith("4567")


@override_settings(SMS_PATTERN_CODES="")
def test_send_without_pattern_is_blocked_and_auditable(iranpayamak_settings: Any) -> None:
    """قالب بی‌الگو ⇒ وضعیت «مسدود» با دلیل روشن (نه «ناموفق» مبهم)."""
    delivery = send_sms(
        phone=PHONE,
        template_key=SmsTemplateKey.OFFER_CREATED,
        context={"unit": "A1", "window": "۲۳ مهر"},
        dedup_key="test:iranpayamak:blocked",
    )
    assert delivery.status == SmsStatus.BLOCKED
    assert "sms_patterns" in delivery.error


@override_settings(SMS_PATTERN_CODES=json.dumps({"welcome": "Ww12Ee34"}))
def test_can_render_reflects_provider_capability(iranpayamak_settings: Any) -> None:
    """پرسش «این قالب رسیدنی است؟» پیش از ساخت رکورد، پاسخ درست می‌دهد."""
    assert can_render("iranpayamak", SmsTemplateKey.WELCOME) is True
    assert can_render("iranpayamak", SmsTemplateKey.FEEDBACK_REQUEST) is False
    assert can_render("mock", SmsTemplateKey.FEEDBACK_REQUEST) is True


def test_dedup_key_prevents_duplicate_send(monkeypatch: Any, iranpayamak_settings: Any) -> None:
    """کلید ضدتکرار: یک رکورد، یک پیامک — حتی اگر سرویس دوباره صدا زده شود."""
    calls = _capture_urlopen(monkeypatch, '{"status": 200}')
    for _ in range(3):
        send_sms(
            phone=PHONE,
            template_key=SmsTemplateKey.OTP_LOGIN,
            context={"code": "12345"},
            dedup_key="test:iranpayamak:dedup",
        )
    assert SmsDelivery.objects.filter(dedup_key="test:iranpayamak:dedup").count() == 1
    assert len(calls) == 1


# ── سیستم‌چک و دستور ────────────────────────────────────────────────────────


def test_no_checks_when_sms_disabled() -> None:
    """در محیط توسعه (پیامک خاموش) هیچ هشداری تولید نمی‌شود — بدون نویز."""
    assert sms_readiness() == []


def test_enabled_network_provider_without_key_is_error() -> None:
    """پیامک روشن + آداپتور واقعی + کلید نبود ⇒ خطا (نه هشدار)."""
    with override_settings(SMS_ENABLED=True, SMS_PROVIDER="iranpayamak", SMS_API_KEY=""):
        messages = sms_readiness()
    assert any(isinstance(item, Error) and item.id == "mahdieh.E012" for item in messages)


@override_settings(SMS_PATTERN_CODES=json.dumps({"welcome": "Ww12Ee34"}))
def test_enabled_provider_flags_critical_missing_pattern() -> None:
    """الگوی حیاتی غایب (پیشنهاد سانس) با پیامک روشن ⇒ خطای راه‌اندازی."""
    with override_settings(
        SMS_ENABLED=True,
        SMS_PROVIDER="iranpayamak",
        SMS_API_KEY="k",
        SMS_SENDER_NUMBER=LINE,
    ):
        messages = sms_readiness()
    ids = {item.id for item in messages}
    assert "mahdieh.W010" in ids
    assert any(isinstance(item, Warning) and item.id == "mahdieh.W011" for item in messages)


def test_sms_patterns_command_reports_and_exits() -> None:
    """دستور ``sms_patterns`` هم گزارش می‌دهد و هم با ``--request`` متن درخواست."""
    command = SmsPatternsCommand()
    report = command.handle(json=False, request=False, check=False)
    assert report is None  # فقط می‌نویسد
    text = patterns.request_text()
    assert "لغو رزرو" in text
    # نامهٔ تازه متن **کامل** هر الگو را دارد (نه یک خط پیشنهاد)
    assert text.count("متن الگو:") >= 11
    assert text.count("نمونهٔ پیامی که زائر می‌گیرد:") >= 11

    # نامه باید «چرا این الگوها دوباره درخواست می‌شوند» را هم بگوید
    assert "پس از بالا آمدن دامنهٔ سایت" in text
    assert "تموم زندگیم مال حسینه..." in text


@override_settings(SMS_PATTERN_CODES="")
def test_pattern_readiness_counts_are_consistent() -> None:
    """گزارش آمادگی: هر قالب یا در «آماده» است یا در «بدون الگو» — نه هر دو."""
    readiness = patterns.pattern_readiness()
    assert set(readiness["bound"]) & set(readiness["unbound"]) == set()
    assert set(readiness["guest_unbound"]).issubset(set(readiness["unbound"]))
    # امروز هیچ الگویی تأیید نشده ⇒ سه قالب حیاتی قرمزند و باید قرمز باشند.
    assert readiness["critical_unbound"] == [
        SmsTemplateKey.BOOKING_CONFIRMED,
        SmsTemplateKey.OFFER_CREATED,
        SmsTemplateKey.OTP_LOGIN,
    ]
    assert SmsTemplateKey.BOOKING_CONFIRMED in readiness["unbound"]


def test_digit_helper_matches_rendering_rule() -> None:
    """یک قاعده برای ارقام در کل سامانه: تابع ``core.text`` مرجع است."""
    from apps.core.text import to_latin_digits, to_persian_digits

    assert to_persian_digits("1405-07-24") == "۱۴۰۵-۰۷-۲۴"
    assert to_persian_digits(2.5) == "۲.۵"
    assert to_latin_digits("۱۴۰۵/۰۷/۲۴") == "1405/07/24"
    assert timezone.now() is not None  # تضمین: تست به زمان وابسته نیست
