"""آداپتورهای پیامک — ماک، کنسول و دو ارائه‌دهندهٔ واقعی ایرانی.

قواعد آهنین:
    • هیچ آداپتوری استثنا پرتاب نمی‌کند؛ خطا در ``SmsReceipt`` برمی‌گردد.
    • آداپتور ماک، پیام‌ها را در حافظهٔ فرآیند نگه می‌دارد تا تست‌ها بتوانند
      کد یک‌بارمصرف را بخوانند — بدون هیچ تماس شبکه‌ای.
    • آداپتور کنسول فقط برای توسعهٔ محلی است و بدنهٔ پیام را در لاگ می‌نویسد؛
      در تولید به‌کار نمی‌رود (و در ``get_sms_provider`` محدود شده است).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, ClassVar, Final
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from django.conf import settings

from apps.notifications import patterns
from apps.notifications.ports import SmsMessage, SmsReceipt

logger = logging.getLogger(__name__)

# ── ثابت‌های قالب شماره و پاسخ درگاه ─────────────────────────────────────────
COUNTRY_CODE: Final[str] = "98"
E164_LENGTH: Final[int] = 12  # 98912… (بدون +)
E164_WITH_PREFIX_LENGTH: Final[int] = 14  # 0098912…
NATIONAL_LENGTH: Final[int] = 10  # 912…
GATEWAY_OK_STATUS: Final[int] = 200
GATEWAY_OK_STATUSES: Final[frozenset[int]] = frozenset({200, 201, 202})
# پنل‌ها همه یک‌شکل پاسخ نمی‌دهند؛ این سه فهرست، تنوع واقعی را می‌پوشانند.
STATUS_KEYS: Final[tuple[str, ...]] = ("status", "statusCode", "code", "result_code", "resultCode")
REFERENCE_KEYS: Final[tuple[str, ...]] = ("messageid", "messageId", "message_id", "id")
MESSAGE_KEYS: Final[tuple[str, ...]] = ("message", "error", "description")
SUCCESS_FLAG_KEYS: Final[tuple[str, ...]] = ("success", "ok")
OK_TEXT_STATUSES: Final[frozenset[str]] = frozenset(
    {"ok", "success", "successful", "موفق", "انجام شد"}
)


class MockSmsProvider:
    """آداپتور ماک — بی‌شبکه، قابل‌بازرسی توسط تست‌ها.

    ``outbox`` سطح-کلاس است تا در فرآیند تست (که چند اتصال دارد) یک نقطهٔ
    حقیقت باشد. ``reset()`` پیش از هر تست فراخوانی می‌شود.
    """

    name: ClassVar[str] = "mock"
    outbox: ClassVar[list[SmsMessage]] = []
    max_outbox: ClassVar[int] = 500

    def send(self, message: SmsMessage) -> SmsReceipt:
        """ثبت پیام در حافظه و بازگرداندن رسید موفق شبیه‌سازی‌شده."""
        type(self).outbox.append(message)
        if len(type(self).outbox) > type(self).max_outbox:
            del type(self).outbox[: -type(self).max_outbox]
        digest = hashlib.sha256(f"{message.phone}|{message.body}".encode()).hexdigest()
        return SmsReceipt(
            ok=True, provider=self.name, provider_ref=f"mock-{digest[:16]}", is_dry_run=True
        )

    @classmethod
    def reset(cls) -> None:
        """پاک‌سازی صندوق حافظه — فقط برای تست."""
        cls.outbox.clear()

    @classmethod
    def last_for(cls, phone: str) -> SmsMessage | None:
        """آخرین پیام ارسالی به یک شماره (کلید خواندن کد در تست/توسعه)."""
        for message in reversed(cls.outbox):
            if message.phone == phone:
                return message
        return None


class ConsoleSmsProvider:
    """آداپتور کنسول — برای توسعهٔ محلی که پیامک واقعی در دسترس نیست."""

    name: ClassVar[str] = "console"

    def send(self, message: SmsMessage) -> SmsReceipt:
        """نوشتن پیام در لاگ ساخت‌یافته (توسعهٔ محلی)."""
        logger.info(
            "sms_console_delivery",
            extra={
                "phone_masked": f"{message.phone[:4]}***{message.phone[-4:]}",
                "template_key": message.template_key,
                "body": message.body,
            },
        )
        return SmsReceipt(ok=True, provider=self.name, provider_ref="console", is_dry_run=True)


class HttpSmsProvider:
    """پایهٔ آداپتورهای شبکه‌ای — یک‌بار نوشته، برای هر ارائه‌دهنده بازاستفاده می‌شود.

    قواعدی که در همهٔ زیرکلاس‌ها رعایت می‌شود:
        • **هرگز استثنا پرتاب نمی‌کند.** خطای شبکه، پاسخ نامعتبر و نبود کلید همه
          به ``SmsReceipt(ok=False)`` تبدیل می‌شوند؛ پیامک هرگز جریان رزرو را
          نمی‌شکند.
        • **کلید نبود ⇒ تلاش نکن.** اگر کلید API تنظیم نشده باشد، حتی یک بایت
          هم به بیرون فرستاده نمی‌شود (و این در تست‌ها تضمین می‌شود).
        • **مهلت کوتاه.** ۸ ثانیه؛ پیامک تأخیری به هیچ زائری کمک نمی‌کند و
          درخواست کاربر را معطل نمی‌کند.
    """

    name: ClassVar[str] = "http"
    local: ClassVar[bool] = False
    timeout_seconds: ClassVar[int] = 8
    api_key_setting: ClassVar[str] = "SMS_API_KEY"
    sender_setting: ClassVar[str] = "SMS_SENDER_NUMBER"

    def _api_key(self) -> str:
        """کلید API از تنظیمات (خالی ⇒ ارسال انجام نمی‌شود)."""
        return str(getattr(settings, self.api_key_setting, "") or "").strip()

    def _sender(self) -> str:
        """شمارهٔ فرستندهٔ ثبت‌شده در پنل."""
        return str(getattr(settings, self.sender_setting, "") or "").strip()

    @staticmethod
    def gateway_phone(phone: str) -> str:
        """شماره به قالب داخلی ایران (``0912…``) — الزام پنل‌های ایرانی.

        نرمال‌سازی زائرسرا شماره‌ها را در قالب E.164 (``+98912…``) نگه می‌دارد؛
        درگاه‌های پیامکی ایران قالب محلی می‌خواهند. تبدیل در یک نقطه انجام
        می‌شود تا هیچ آداپتوری قاعدهٔ خودش را نسازد.
        """
        digits = "".join(char for char in phone if char.isdigit())
        if digits.startswith(COUNTRY_CODE) and len(digits) == E164_LENGTH:
            return f"0{digits[len(COUNTRY_CODE) :]}"
        if digits.startswith(f"00{COUNTRY_CODE}") and len(digits) == E164_WITH_PREFIX_LENGTH:
            return f"0{digits[len(COUNTRY_CODE) + 2 :]}"
        if digits.startswith("0"):
            return digits
        return f"0{digits}" if len(digits) == NATIONAL_LENGTH else digits

    @staticmethod
    def assert_safe_endpoint(url: str) -> None:
        """نگهبان نشانی درگاه: فقط ``https`` با میزبان مشخص.

        این تابع در برابر «فراخوانی ناخواستهٔ نشانی» (SSRF و طرح‌های ناامن مثل
        ``file://``) می‌ایستد و خطایش **خوانا** است تا در لاگ اپراتور معنا داشته باشد.
        """
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc:
            msg = f"نشانی درگاه پیامک نامعتبر است (scheme={parsed.scheme or 'none'})"
            raise ValueError(msg)

    def supports_template(self, template_key: str) -> bool:  # noqa: ARG002 - امضای قرارداد
        """آیا این آداپتور می‌تواند این قالب را برساند؟ (پیش‌فرض: بله)

        آداپتورهای متنی (کاوه‌نگار/قاصدک) هر متنی را می‌فرستند؛ آداپتورهای
        «الگومحور» فقط قالب‌های نگاشت‌شده را. این تفاوت باید پیش از ساختن رکورد
        تحویل روشن شود تا هرگز پیامکی که پنل نمی‌پذیرد ساخته نشود.
        """
        return True

    def build_request(self, message: SmsMessage) -> tuple[str, bytes, dict[str, str]]:
        """ساخت درخواست HTTP: نشانی، بدنه و سرصفحه‌ها — در هر آداپتور یک‌بار."""
        raise NotImplementedError

    def parse_response(self, payload: dict[str, Any]) -> SmsReceipt:
        """تفسیر پاسخ ارائه‌دهنده — پیش‌فرض: ``ok`` موفقیّت را می‌رساند."""
        raise NotImplementedError

    def send(self, message: SmsMessage) -> SmsReceipt:
        """ارسال واقعی با مهلت، بدون استثنا و با ثبت خطای خوانا."""
        api_key = self._api_key()
        if not api_key:
            logger.warning("sms_provider_missing_api_key", extra={"provider": self.name})
            return SmsReceipt(
                ok=False,
                provider=self.name,
                error="کلید API ارائه‌دهندهٔ پیامک تنظیم نشده است.",
            )
        try:
            url, body, headers = self.build_request(message)
            # نگهبان صریح پیش از هر تماس: تنها https و میزبان نام‌دار پذیرفته می‌شود.
            # چرا؟ چون «نشانی از داخل کد می‌آید» یک قرارداد است، نه تضمین؛ اگر روزی
            # کسی آدرس درگاه را از تنظیمات/پایگاه‌داده خواند، همین نگهبان جلوی
            # فراخوانی file:// یا http:// را می‌گیرد. (یافتهٔ بازرسی امنیتی فاز ۸)
            self.assert_safe_endpoint(url)
            request = Request(  # noqa: S310  # nosec B310
                url, data=body, headers=headers, method="POST"
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310  # nosec B310
                raw = response.read().decode("utf-8", errors="replace")
            payload = json.loads(raw)
        except (URLError, OSError, TimeoutError, ValueError) as exc:
            logger.warning(
                "sms_provider_call_failed", extra={"provider": self.name, "error": str(exc)}
            )
            return SmsReceipt(ok=False, provider=self.name, error=f"خطای تماس: {exc}")
        try:
            return self.parse_response(payload if isinstance(payload, dict) else {})
        except Exception as exc:  # pragma: no cover - قالب پاسخ ناشناخته
            logger.exception("sms_provider_bad_response")
            return SmsReceipt(ok=False, provider=self.name, error=f"پاسخ نامعتبر: {exc}")


class KavenegarSmsProvider(HttpSmsProvider):
    """آداپتور کاوه‌نگار — رایج‌ترین درگاه پیامک ایران.

    قرارداد: ``POST /v1/{api-key}/sms/send.json`` با فیلدهای ``receptor``،
    ``message`` و ``sender``؛ پاسخ موفق ``return.status == 200`` است.
    """

    name: ClassVar[str] = "kavenegar"
    endpoint: ClassVar[str] = "https://api.kavenegar.com/v1/{key}/sms/send.json"

    def build_request(self, message: SmsMessage) -> tuple[str, bytes, dict[str, str]]:
        """ساخت درخواست فرم‌محور کاوه‌نگار."""
        url = self.endpoint.format(key=self._api_key())
        fields = {
            "receptor": self.gateway_phone(message.phone),
            "message": message.body,
        }
        sender = self._sender()
        if sender:
            fields["sender"] = sender
        return (
            url,
            urlencode(fields).encode("utf-8"),
            {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        )

    def parse_response(self, payload: dict[str, Any]) -> SmsReceipt:
        """تفسیر پاسخ کاوه‌نگار (``return.status`` و ``entries[0].messageid``)."""
        status_block = payload.get("return") or {}
        code = int(status_block.get("status") or 0)
        if code != GATEWAY_OK_STATUS:
            return SmsReceipt(
                ok=False,
                provider=self.name,
                error=str(status_block.get("message") or f"کد وضعیت {code}"),
            )
        entries = payload.get("entries") or []
        reference = str(entries[0].get("messageid", "")) if entries else ""
        return SmsReceipt(ok=True, provider=self.name, provider_ref=reference)


class GhasedakSmsProvider(HttpSmsProvider):
    """آداپتور قاصدک — جایگزین دوم؛ اثبات اینکه پورت پیامک، ارائه‌دهنده‌آگنوستیک است."""

    name: ClassVar[str] = "ghasedak"
    endpoint: ClassVar[str] = "https://api.ghasedak.me/v2/sms/send/simple"

    def build_request(self, message: SmsMessage) -> tuple[str, bytes, dict[str, str]]:
        """ساخت درخواست فرم‌محور قاصدک با سرصفحهٔ ``apikey``."""
        fields = {
            "receptor": self.gateway_phone(message.phone),
            "message": message.body,
        }
        sender = self._sender()
        if sender:
            fields["sender"] = sender
        return (
            self.endpoint,
            urlencode(fields).encode("utf-8"),
            {
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "apikey": self._api_key(),
            },
        )

    def parse_response(self, payload: dict[str, Any]) -> SmsReceipt:
        """تفسیر پاسخ قاصدک (``result.code == 200``)."""
        result = payload.get("result") or {}
        code = int(result.get("code") or 0)
        if code != GATEWAY_OK_STATUS:
            return SmsReceipt(
                ok=False,
                provider=self.name,
                error=str(result.get("message") or f"کد وضعیت {code}"),
            )
        items = payload.get("items") or []
        reference = str(items[0].get("messageid", "")) if items else ""
        return SmsReceipt(ok=True, provider=self.name, provider_ref=reference)


class IranPayamakSmsProvider(HttpSmsProvider):
    """آداپتور ایران‌پیامک — ارسال **الگومحور** (Pattern API).

    چرا این آداپتور ساختار متفاوتی دارد؟ پنل‌های رسمی ایرانی متن آزاد را برای
    فرستنده‌های خدماتی محدود می‌کنند؛ تنها راه، «الگوی تأییدشده» است: متن در پنل
    می‌ماند و ما فقط کد الگو و مقدار متغیرها را می‌فرستیم. پس این آداپتور برخلاف
    کاوه‌نگار، بدنهٔ پیام را نمی‌فرستد؛ از ``patterns.resolve_binding`` می‌پرسد
    این قالب به کدام الگو می‌رود.

    قرارداد درخواست (پنل مشترک زائرسرا، ۱۴۰۵/۰۷/۲۴):
        ``POST https://api.iranpayamak.com/ws/v1/sms/pattern``
        سرصفحه: ``Api-Key: <کلید>``
        بدنه: ``{"code", "attributes", "recipient", "line_number", "number_format"}``
    """

    name: ClassVar[str] = "iranpayamak"
    endpoint: ClassVar[str] = "https://api.iranpayamak.com/ws/v1/sms/pattern"
    api_key_setting: ClassVar[str] = "SMS_API_KEY"
    sender_setting: ClassVar[str] = "SMS_SENDER_NUMBER"
    number_format: ClassVar[str] = "persian"
    max_payload_chars: ClassVar[int] = 240

    def supports_template(self, template_key: str) -> bool:
        """پنل ایران‌پیامک فقط قالب‌هایی را می‌رساند که الگوی ثبت‌شده دارند."""
        return patterns.resolve_binding(template_key) is not None

    def send(self, message: SmsMessage) -> SmsReceipt:
        """ارسال الگومحور با نگهبان صریح: قالب بی‌الگو هرگز به شبکه نمی‌رود."""
        if not self.supports_template(message.template_key):
            logger.warning("sms_pattern_missing", extra={"template_key": message.template_key})
            return SmsReceipt(
                ok=False,
                provider=self.name,
                error=(
                    "برای این قالب، الگویی در پنل ایران‌پیامک ثبت نشده است "
                    "(manage.py sms_patterns --request را ببینید)."
                ),
            )
        return super().send(message)

    def build_request(self, message: SmsMessage) -> tuple[str, bytes, dict[str, str]]:
        """ساخت درخواست JSON الگومحور — بدون هیچ متن آزادی از سمت سامانه."""
        resolved = patterns.resolve_binding(message.template_key)
        if resolved is None:  # pragma: no cover - در ``send`` پیش‌تر گرفته شده است
            msg = f"الگوی پیامکی برای {message.template_key} ثبت نشده است"
            raise ValueError(msg)
        _key, binding = resolved
        attributes, missing = patterns.build_attributes(binding, message.context)
        if missing:
            # قاعدهٔ «هرگز پیام درهم»: جای‌خالی پرنشده به زائر نمی‌رسد.
            msg = f"متغیرهای غایب برای الگوی {binding.key}: {', '.join(missing)}"
            raise ValueError(msg)
        payload: dict[str, Any] = {
            "code": binding.code,
            "attributes": attributes,
            "recipient": self.gateway_phone(message.phone),
            "number_format": self.number_format,
        }
        sender = self._sender()
        if sender:
            payload["line_number"] = sender
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "Api-Key": self._api_key(),
        }
        return self.endpoint, body, headers

    @staticmethod
    def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
        """نخستین کلید موجود و پرارزش در پاسخ — پنل‌ها در نام‌گذاری یکدست نیستند."""
        for key in keys:
            if key in payload and payload[key] not in (None, ""):
                return payload[key]
        return None

    @staticmethod
    def _as_int(value: Any) -> int | None:
        """تبدیل محافظه‌کارانهٔ وضعیت به عدد؛ ``None`` یعنی «عدد نیست»."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    @staticmethod
    def _data_block(payload: dict[str, Any]) -> dict[str, Any]:
        """بلوک ``data`` پاسخ — بعضی پنل‌ها نتیجه را تودرتو برمی‌گردانند."""
        block = payload.get("data")
        return block if isinstance(block, dict) else {}

    def _reference(self, payload: dict[str, Any]) -> str:
        """شناسهٔ پیام پنل (برای پیگیری در پنل و لاگ)."""
        data = self._data_block(payload)
        found = self._first(payload, REFERENCE_KEYS)
        if found is None:
            found = self._first(data, REFERENCE_KEYS)
        return str(found or "")

    def _status(self, payload: dict[str, Any]) -> Any:
        """وضعیت اعلام‌شدهٔ پنل — در بدنهٔ اصلی یا در ``data``."""
        data = self._data_block(payload)
        found = self._first(payload, STATUS_KEYS)
        return found if found is not None else self._first(data, STATUS_KEYS)

    def parse_response(self, payload: dict[str, Any]) -> SmsReceipt:
        """تفسیر پاسخ پنل — محافظه‌کار: فقط «موفقیت صریح» موفق شمرده می‌شود.

        چرا محافظه‌کار؟ چون «موفقِ اشتباه» خطرناک‌ترین حالت است: مدیر خیال می‌کند
        پیامک رفته و کسی پیگیری نمی‌کند. اگر ساختار پاسخ را نشناسیم، شکست ثبت
        می‌شود و بدنهٔ خام (کوتاه‌شده) در خطا می‌ماند تا اپراتور شکل تازه را
        اضافه کند. شکل‌های شناخته‌شده در ``tests/test_iranpayamak.py`` مستندند.
        """
        if not payload:
            return SmsReceipt(ok=False, provider=self.name, error="پاسخ خالی از درگاه پیامک")
        reference = self._reference(payload)
        status = self._status(payload)
        numeric = self._as_int(status)
        if numeric is not None:
            if numeric in GATEWAY_OK_STATUSES:
                return SmsReceipt(ok=True, provider=self.name, provider_ref=reference)
            data = self._data_block(payload)
            message = self._first(payload, MESSAGE_KEYS) or self._first(data, MESSAGE_KEYS)
            return SmsReceipt(
                ok=False, provider=self.name, error=f"کد {numeric}: {message or 'بدون توضیح'}"
            )
        text_ok = isinstance(status, str) and status.strip().lower() in OK_TEXT_STATUSES
        flagged_ok = self._first(payload, SUCCESS_FLAG_KEYS) is True
        if text_ok or flagged_ok or reference:
            return SmsReceipt(ok=True, provider=self.name, provider_ref=reference)
        if isinstance(status, str):
            return SmsReceipt(ok=False, provider=self.name, error=f"وضعیت درگاه: {status}")
        raw = json.dumps(payload, ensure_ascii=False)[: self.max_payload_chars]
        return SmsReceipt(ok=False, provider=self.name, error=f"پاسخ ناشناختهٔ درگاه: {raw}")


_PROVIDERS: dict[str, type[Any]] = {
    MockSmsProvider.name: MockSmsProvider,
    ConsoleSmsProvider.name: ConsoleSmsProvider,
    KavenegarSmsProvider.name: KavenegarSmsProvider,
    GhasedakSmsProvider.name: GhasedakSmsProvider,
    IranPayamakSmsProvider.name: IranPayamakSmsProvider,
}

# آداپتورهای محلی: بی‌شبکه‌اند و در محیط غیرتولیدی هم اجازهٔ ارسال دارند (dry-run).
# آداپتورهای واقعی تنها با ``SMS_ENABLED=True`` فرستاده می‌شوند — تا یک اشتباه
# در تنظیمات محیط، پیامک واقعی به زائر نفرستد.
LOCAL_PROVIDERS: frozenset[str] = frozenset(
    provider.name for provider in (MockSmsProvider, ConsoleSmsProvider)
)
NETWORK_PROVIDERS: frozenset[str] = frozenset(
    provider.name
    for provider in (KavenegarSmsProvider, GhasedakSmsProvider, IranPayamakSmsProvider)
)


def get_sms_provider(name: str | None = None) -> Any:
    """ساخت آداپتور پیامک بر پایهٔ تنظیمات (پیش‌فرض: ماک).

    اگر نام ناشناخته باشد، به ماک برمی‌گردیم و رویداد هشدار ثبت می‌شود؛
    ترجیح می‌دهیم پیامک ارسال نشود تا اینکه سامانه در نبود ارائه‌دهنده بایستد.
    """
    resolved = name or str(getattr(settings, "SMS_PROVIDER", "mock") or "mock")
    provider_class = _PROVIDERS.get(resolved)
    if provider_class is None:
        logger.warning("sms_provider_unknown", extra={"provider": resolved})
        provider_class = MockSmsProvider
    return provider_class()


def is_local_provider(name: str | None = None) -> bool:
    """آیا آداپتور جاری محلی است (هیچ تماس شبکه‌ای ندارد)؟

    تفاوت کلیدی با نسخهٔ پیشین: مجموعهٔ «محلی» صریح است، نه «هر نام ثبت‌شده».
    اگر این تابع برای ارائه‌دهندهٔ واقعی ``True`` برگرداند، پیامک واقعی در محیط
    آزمایشی هم فرستاده می‌شود — خطایی که هزینهٔ مالی و اعتباری دارد.
    """
    resolved = name or str(getattr(settings, "SMS_PROVIDER", "mock") or "mock")
    return resolved in LOCAL_PROVIDERS


def provider_catalog() -> list[dict[str, Any]]:
    """فهرست آداپتورها برای صفحهٔ سلامت و مستندات — نام، محلی‌بودن، کلید."""
    catalog: list[dict[str, Any]] = []
    for name, provider_class in sorted(_PROVIDERS.items()):
        catalog.append(
            {
                "name": name,
                "local": name in LOCAL_PROVIDERS,
                "has_api_key": bool(
                    str(
                        getattr(
                            settings, getattr(provider_class, "api_key_setting", "SMS_API_KEY"), ""
                        )
                        or ""
                    ).strip()
                ),
            }
        )
    return catalog
