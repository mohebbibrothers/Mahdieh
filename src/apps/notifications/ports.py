"""پورت پیامک — قرارداد ثابت میان دامنه و ارائه‌دهنده.

الگوی Ports & Adapters: دامنه فقط این قرارداد را می‌شناسد. هر ارائه‌دهندهٔ
آینده (کاوه‌نگار، ملی‌پیامک، …) یک آداپتور تازه است، نه تغییری در دامنه.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

MAX_BODY_LENGTH = 480


@dataclass(frozen=True, slots=True)
class SmsMessage:
    """یک پیامک آمادهٔ ارسال — حامل متن نهایی (ممکن است کد یک‌بارمصرف باشد)."""

    phone: str
    body: str
    template_key: str
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SmsReceipt:
    """رسید ارسال از سوی ارائه‌دهنده."""

    ok: bool
    provider: str
    provider_ref: str = ""
    error: str = ""
    is_dry_run: bool = False


@runtime_checkable
class SmsPort(Protocol):
    """قرارداد هر آداپتور پیامک."""

    name: str

    def send(self, message: SmsMessage) -> SmsReceipt:
        """ارسال پیام و بازگرداندن رسید؛ هرگز استثنا پرتاب نمی‌کند."""
        ...
