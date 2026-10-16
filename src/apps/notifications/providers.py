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

from apps.notifications.ports import SmsMessage, SmsReceipt

logger = logging.getLogger(__name__)

# ── ثابت‌های قالب شماره و پاسخ درگاه ─────────────────────────────────────────
COUNTRY_CODE: Final[str] = "98"
E164_LENGTH: Final[int] = 12  # 98912… (بدون +)
E164_WITH_PREFIX_LENGTH: Final[int] = 14  # 0098912…
NATIONAL_LENGTH: Final[int] = 10  # 912…
GATEWAY_OK_STATUS: Final[int] = 200


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


_PROVIDERS: dict[str, type[Any]] = {
    MockSmsProvider.name: MockSmsProvider,
    ConsoleSmsProvider.name: ConsoleSmsProvider,
    KavenegarSmsProvider.name: KavenegarSmsProvider,
    GhasedakSmsProvider.name: GhasedakSmsProvider,
}

# آداپتورهای محلی: بی‌شبکه‌اند و در محیط غیرتولیدی هم اجازهٔ ارسال دارند (dry-run).
# آداپتورهای واقعی تنها با ``SMS_ENABLED=True`` فرستاده می‌شوند — تا یک اشتباه
# در تنظیمات محیط، پیامک واقعی به زائر نفرستد.
LOCAL_PROVIDERS: frozenset[str] = frozenset(
    provider.name for provider in (MockSmsProvider, ConsoleSmsProvider)
)
NETWORK_PROVIDERS: frozenset[str] = frozenset(
    provider.name for provider in (KavenegarSmsProvider, GhasedakSmsProvider)
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
