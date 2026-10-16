"""آمادگی فعال‌سازی الگوهای پیامک — «کد که رسید، فقط کد عوض شود، نه کد برنامه».

چرا این فایل؟ ما امروز تنها بخشی از کدهای الگوی پنل را داریم (کد ورود، خوش‌آمد،
تأیید رزرو) و بقیه پس از درخواست از پنل می‌رسند. اگر فعال‌سازی هر الگو نیاز به
تغییر کد داشته باشد، دو خطر واقعی هست:

    ۱) **فعال‌سازی دیرهنگام**: متن پنل آماده است، ولی سامانه پیام نمی‌فرستد،
       چون فراخوان متغیر لازم را پاس نمی‌دهد و ارسال (به‌درستی) مسدود می‌شود.
    ۲) **پیام درهم**: اگر برای رد کردن مسدودی، متغیر گم‌شده را حدس بزنیم، زائر
       پیامک «{name} عزیز» می‌گیرد — بدتر از نرسیدن پیام.

این تست‌ها سه چیز را **اثبات** می‌کنند، نه ادعا:
    • هر فراخوان پیامکی، دقیقاً همان متغیرهایی را پاس می‌دهد که کاتالوگ الگو
      برای همان قالب اعلام کرده است (تحلیل AST روی همهٔ فراخوان‌ها).
    • فعال‌سازی یک الگوی تازه، صرفاً با گذاشتن کد در ``SMS_PATTERN_CODES``
      انجام می‌شود و کل زنجیرهٔ ارسال کار می‌کند (بدون تغییر کد).
    • رکورد «مسدود» قفل ابدی نمی‌شود: پس از رفع پیکربندی، همان رویداد یک بار
      و فقط یک بار فرستاده می‌شود.
"""

from __future__ import annotations

import ast
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
import pytest

from apps.notifications import patterns
from apps.notifications.choices import SmsStatus, SmsTemplateKey
from apps.notifications.models import SmsDelivery
from apps.notifications.providers import LOCAL_PROVIDERS, NETWORK_PROVIDERS, is_local_provider
from apps.notifications.services import send_sms

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

PHONE = "+989121234567"
LINE = "50002178584000"
APPS_DIR: Final[Path] = Path(settings.BASE_DIR) / "apps"
STATIC_SENDER_KEYS: Final[tuple[str, ...]] = ("phone", "template_key", "dedup_key")

# فراخوان‌هایی که زمینه را از تابع می‌گیرند: نام تابع ⇒ جایی که باید صدا زده شود.
DYNAMIC_CONTEXT_RESOLVERS: Final[dict[str, str]] = {
    "_sms_context": "apps.reservation.services",
}


class _FakeResponse:
    """پاسخ شبیه‌سازی‌شدهٔ HTTP — هیچ بایتی از شبکه رد و بدل نمی‌شود."""

    def __init__(self, payload: str) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._payload.encode("utf-8")


def _capture_urlopen(monkeypatch: Any, payload: str) -> list[Any]:
    """جایگزینی ``urlopen`` و ثبت درخواست‌ها."""
    calls: list[Any] = []

    def fake_urlopen(request: Any, timeout: Any = None) -> _FakeResponse:
        calls.append((request, timeout))
        return _FakeResponse(payload)

    monkeypatch.setattr("apps.notifications.providers.urlopen", fake_urlopen)
    return calls


def _send_sms_calls() -> list[tuple[Path, ast.Call]]:
    """همهٔ فراخوان‌های ``send_sms`` در بستهٔ ``apps`` (به‌جز تعریف خودش)."""
    found: list[tuple[Path, ast.Call]] = []
    for path in sorted(APPS_DIR.rglob("*.py")):
        if "notifications/services.py" in path.as_posix() or "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if "send_sms(" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "send_sms"
            ):
                found.append((path, node))
    return found


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    """مقدار یک آرگومان کلیدواژه‌ای در یک فراخوان."""
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _template_key_of(call: ast.Call) -> str | None:
    """کلید قالب یک فراخوان — فقط به‌شکل ``SmsTemplateKey.X`` (بدون رشتهٔ خام)."""
    value = _keyword(call, "template_key")
    if (
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == "SmsTemplateKey"
    ):
        return value.attr.lower()
    return None


def _static_context_keys(call: ast.Call) -> set[str] | None:
    """کلیدهای زمینهٔ ایستا؛ ``None`` یعنی زمینه پویا است."""
    value = _keyword(call, "context")
    if value is None:
        return set()
    if isinstance(value, ast.Dict):
        return {key.value for key in value.keys if isinstance(key, ast.Constant)}
    return None


def _stub_reservation() -> SimpleNamespace:
    """جانشین سبک یک رزرو برای صدا زدن سازندهٔ زمینهٔ پویا."""
    start = timezone.now() + timedelta(days=2)
    return SimpleNamespace(
        code="MH-1001",
        guests_count=2,
        starts_at=start,
        ends_at=start + timedelta(hours=24),
        guest=SimpleNamespace(display_name="حسین محمدی", phone=PHONE),
        unit=SimpleNamespace(title="سوئیت طبقهٔ اول", code="A1"),
    )


def test_senders_use_typed_template_keys_and_dedup_keys() -> None:
    """هر فراخوان: کلید قالب از نوع ``SmsTemplateKey`` و کلید یکتایی اجباری.

    رشتهٔ خام یعنی غلط‌تایپی که هیچ‌کس نمی‌بیند؛ نبودِ ``dedup_key`` یعنی
    بازپخش، پیامک تکراری برای زائر می‌فرستد.
    """
    problems: list[str] = []
    calls = _send_sms_calls()
    assert len(calls) >= 8, "تعداد فراخوان‌های پیامک کمتر از انتظار است"
    for path, call in calls:
        where = f"{path.name}:{call.lineno}"
        if _template_key_of(call) is None:
            problems.append(f"{where} — کلید قالب باید SmsTemplateKey.X باشد")
        for name in STATIC_SENDER_KEYS:
            if _keyword(call, name) is None:
                problems.append(f"{where} — آرگومان «{name}» غایب است")
    assert not problems, "فراخوان‌های ناسالم: " + " | ".join(problems)


def test_every_call_site_feeds_the_pattern_catalog_requirements() -> None:
    """زمینهٔ هر فراخوان، همهٔ متغیرهای الزامیِ کاتالوگ الگو را دارد.

    متغیرهای الزامی از خودِ کاتالوگ می‌آید (``patterns.catalog_bindings``)، پس
    این تست با رسیدن هر کد الگوی تازه خودکار سخت‌گیرتر می‌شود.
    """
    bindings = patterns.catalog_bindings() | patterns.all_bindings()
    problems: list[str] = []
    seen: set[str] = set()
    for path, call in _send_sms_calls():
        key = _template_key_of(call)
        if key is None:
            continue
        seen.add(key)
        required = set(bindings.get(key).required) if bindings.get(key) else set()
        if not required:
            continue
        keys = _static_context_keys(call)
        if keys is None:
            resolver = _keyword(call, "context")
            assert isinstance(resolver, ast.Call) and isinstance(resolver.func, ast.Name)
            keys = set(_run_dynamic_resolver(resolver.func.id))
        missing = sorted(required - keys)
        if missing:
            problems.append(f"{path.name}:{call.lineno} [{key}] کم دارد: {', '.join(missing)}")
    assert not problems, "زمینهٔ ناقص برای الگوهای پنل: " + " | ".join(problems)
    assert {"otp_login", "booking_confirmed", "welcome"} <= seen


def _run_dynamic_resolver(name: str) -> dict[str, Any]:
    """صدا زدن سازندهٔ زمینهٔ پویا با یک رزرو جانشین (تا کلیدها واقعی باشند)."""
    module_path = DYNAMIC_CONTEXT_RESOLVERS[name]
    module = __import__(module_path, fromlist=["*"])
    resolver = getattr(module, name)
    return dict(resolver(_stub_reservation()))


def test_catalogued_templates_are_activation_ready_but_unbound_without_codes() -> None:
    """هر الگوی کاتالوگ، نگاشت کامل دارد اما تا رسیدن کد، «بی‌الگو» است."""
    bindings = patterns.catalog_bindings()
    for key in (SmsTemplateKey.OFFER_CREATED, SmsTemplateKey.WAITLIST_JOINED):
        binding = bindings[key]
        assert binding.attributes, key
        assert binding.required == frozenset(binding.attributes), key
        assert patterns.resolve_binding(key) is None, f"{key} باید بی‌الگو باشد"


@override_settings(SMS_PATTERN_CODES=json.dumps({SmsTemplateKey.OFFER_CREATED: "ZZZ123"}))
def test_activation_is_configuration_only() -> None:
    """کد الگو که رسید، تنها «تنظیم» عوض می‌شود — نه کد برنامه."""
    resolved = patterns.resolve_binding(SmsTemplateKey.OFFER_CREATED)
    assert resolved is not None
    key, binding = resolved
    assert key == SmsTemplateKey.OFFER_CREATED
    assert binding.code == "ZZZ123"
    assert binding.attributes == {
        "full_name": "name",
        "unit": "unit",
        "window": "window",
        "url": "link",
    }
    assert binding.required == frozenset(binding.attributes)


@override_settings(
    SMS_ENABLED=True,
    SMS_PROVIDER="iranpayamak",
    SMS_API_KEY="test-key",
    SMS_SENDER_NUMBER=LINE,
)
def test_full_chain_works_the_day_the_code_arrives(monkeypatch: Any) -> None:
    """زنجیرهٔ کامل با یک کد تازهٔ پنل: از قالب تا بدنهٔ درخواست واقعی."""
    calls = _capture_urlopen(monkeypatch, json.dumps({"status": 200, "messageid": 4242}))
    with override_settings(SMS_PATTERN_CODES=json.dumps({SmsTemplateKey.OFFER_CREATED: "ZZZ123"})):
        delivery = send_sms(
            phone=PHONE,
            template_key=SmsTemplateKey.OFFER_CREATED,
            context={
                "full_name": "زهرا کریمی",
                "unit": "سوئیت طبقهٔ دوم",
                "window": "۲۳ تا ۲۵ مهر",
                "url": "https://mahdieh.ir/offer/abc",
            },
            dedup_key="offer-ready:1",
        )
    assert delivery.status == SmsStatus.SENT
    assert delivery.provider_ref == "4242"
    assert calls, "درخواست HTTP ثبت نشد"
    request = calls[0][0]
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["code"] == "ZZZ123"
    assert payload["recipient"] == "09121234567"
    assert set(payload["attributes"]) == {"name", "unit", "window", "link"}
    assert payload["attributes"]["name"] == "زهرا کریمی"


@override_settings(SMS_ENABLED=True, SMS_PROVIDER="iranpayamak", SMS_API_KEY="k")
def test_blocked_delivery_is_revived_once_configuration_is_fixed(monkeypatch: Any) -> None:
    """رکورد «مسدود» پس از رفع پیکربندی، دقیقاً یک بار ارسال می‌شود."""
    before = send_sms(
        phone=PHONE,
        template_key=SmsTemplateKey.WAITLIST_JOINED,
        context={"full_name": "مریم", "unit": "A1", "window": "۲۳ مهر"},
        dedup_key="join:1",
    )
    assert before.status == SmsStatus.BLOCKED
    calls = _capture_urlopen(monkeypatch, json.dumps({"status": 200, "messageid": 7}))
    with override_settings(
        SMS_PATTERN_CODES=json.dumps({SmsTemplateKey.WAITLIST_JOINED: "JOIN123"})
    ):
        after = send_sms(
            phone=PHONE,
            template_key=SmsTemplateKey.WAITLIST_JOINED,
            context={"full_name": "مریم", "unit": "A1", "window": "۲۳ مهر"},
            dedup_key="join:1",
        )
    assert after.pk == before.pk, "رکورد تازه ساخته شد، در حالی که باید همان بازآماده شود"
    assert after.status == SmsStatus.SENT
    assert after.attempts == 2
    assert len(calls) == 1
    assert SmsDelivery.objects.filter(dedup_key="join:1").count() == 1


@override_settings(SMS_ENABLED=True, SMS_PROVIDER="mock")
def test_delivered_delivery_is_never_sent_twice(monkeypatch: Any) -> None:
    """پس از ارسال موفق، همان کلید یکتایی هرگز دوباره به شبکه نمی‌رود."""
    from apps.notifications.providers import MockSmsProvider

    MockSmsProvider.reset()
    first = send_sms(
        phone=PHONE,
        template_key=SmsTemplateKey.WELCOME,
        context={"full_name": "علی"},
        dedup_key="welcome:1",
    )
    # ماک، «ارسال آزمایشی» است ⇒ وضعیت SUPPRESSED؛ هر دو وضعیت «رسیده» شمرده می‌شوند.
    assert first.status in {SmsStatus.SENT, SmsStatus.SUPPRESSED}
    second = send_sms(
        phone=PHONE,
        template_key=SmsTemplateKey.WELCOME,
        context={"full_name": "علی"},
        dedup_key="welcome:1",
    )
    assert second.pk == first.pk
    assert len(MockSmsProvider.outbox) == 1


def test_local_and_network_providers_are_disjoint() -> None:
    """دسته‌بندی آداپتورها: محلی‌ها هرگز شبکه را لمس نمی‌کنند و ایران‌پیامک شبکه‌ای است."""
    assert frozenset({"mock", "console"}) == LOCAL_PROVIDERS
    assert "iranpayamak" in NETWORK_PROVIDERS
    assert is_local_provider("mock") is True
    assert is_local_provider("iranpayamak") is False
    assert not (LOCAL_PROVIDERS & NETWORK_PROVIDERS)
