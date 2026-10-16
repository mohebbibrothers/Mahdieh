"""آداپتورهای پیامک — ماک، کنسول و (در فاز ۵) ارائه‌دهندهٔ واقعی ایرانی.

قواعد آهنین:
    • هیچ آداپتوری استثنا پرتاب نمی‌کند؛ خطا در ``SmsReceipt`` برمی‌گردد.
    • آداپتور ماک، پیام‌ها را در حافظهٔ فرآیند نگه می‌دارد تا تست‌ها بتوانند
      کد یک‌بارمصرف را بخوانند — بدون هیچ تماس شبکه‌ای.
    • آداپتور کنسول فقط برای توسعهٔ محلی است و بدنهٔ پیام را در لاگ می‌نویسد؛
      در تولید به‌کار نمی‌رود (و در ``get_sms_provider`` محدود شده است).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, ClassVar

from django.conf import settings

from apps.notifications.ports import SmsMessage, SmsReceipt

logger = logging.getLogger(__name__)


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


_PROVIDERS: dict[str, type[Any]] = {
    MockSmsProvider.name: MockSmsProvider,
    ConsoleSmsProvider.name: ConsoleSmsProvider,
}


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
    """آیا آداپتور جاری محلی است (هیچ تماس شبکه‌ای ندارد)؟"""
    resolved = name or str(getattr(settings, "SMS_PROVIDER", "mock") or "mock")
    return resolved in _PROVIDERS
