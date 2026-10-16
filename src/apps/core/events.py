"""گذرگاه رخداد درون‌فرآیندی — پیوند ماژول‌ها بدون وابستگی حلقه‌ای.

چرا لازم است؟ وقتی «سانس آزاد می‌شود» (لغو رزرو، جابه‌جایی، انقضای نگه‌داشت،
برداشتن بلوک)، صف انتظار باید واکنش نشان دهد؛ اما `apps.reservation` نباید به
`apps.waitlist` وابسته باشد و برعکس. راه‌حل: ناشر فقط **نام رخداد و داده** را
می‌داند؛ مشترک‌ها در `apps.py` خودشان ثبت‌نام می‌کنند.

تفکیک مهم از Outbox:
    • ``apps.core.outbox`` → تحویل **پایدار** به بیرون (Valkey Streams، بازپخش).
    • ``apps.core.events`` → واکنش **هم‌زمان و محلی** درون همان فرآیند.

قواعد:
    • استثنای یک مشترک **هرگز** عملیات دامنه را نمی‌شکند (گزارش + شمارش می‌شود).
    • رخدادها را پس از کامیت شدن تراکنش منتشر کنید (``transaction.on_commit``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any, Final

import structlog

logger: Final = structlog.get_logger(__name__)

Handler = Callable[[Mapping[str, Any]], None]

_HANDLERS: dict[str, list[Handler]] = defaultdict(list)

# ── نام رخدادها (ثابت‌ها، نه رشته‌های پراکنده) ────────────────────────────────
SLOT_FREED: Final[str] = "slot.freed"
RESERVATION_CANCELLED: Final[str] = "reservation.cancelled"
RESERVATION_MOVED: Final[str] = "reservation.moved"
OFFER_ACCEPTED: Final[str] = "waitlist.offer_accepted"
OFFER_EXPIRED: Final[str] = "waitlist.offer_expired"
ENTRY_REQUEUED: Final[str] = "waitlist.entry_requeued"


def subscribe(event: str) -> Callable[[Handler], Handler]:
    """ثبت مشترک برای یک رخداد (به‌شکل دکوراتور).

    نمونه::

        @subscribe(SLOT_FREED)
        def _handle(payload: Mapping[str, Any]) -> None: ...
    """

    def decorator(handler: Handler) -> Handler:
        """افزودن مشترک به فهرست (بدون تکرار)."""
        if handler not in _HANDLERS[event]:
            _HANDLERS[event].append(handler)
        return handler

    return decorator


def unsubscribe(event: str, handler: Handler) -> None:
    """برداشتن یک مشترک — برای تست و پیکربندی دوباره."""
    if handler in _HANDLERS.get(event, []):
        _HANDLERS[event].remove(handler)


def emit(event: str, payload: Mapping[str, Any]) -> int:
    """انتشار یک رخداد محلی؛ بازگشت تعداد مشترک‌هایی که بی‌خطا اجرا شدند.

    خطای مشترک‌ها سرکوب و ثبت می‌شود: عملیات دامنه (مثلاً لغو رزرو) باید
    حتی وقتی صف انتظار مشکل دارد کامل شود.
    """
    handlers = list(_HANDLERS.get(event, ()))
    if not handlers:
        return 0
    succeeded = 0
    for handler in handlers:
        try:
            handler(payload)
        except Exception:
            logger.exception(
                "event_handler_failed",
                topic=event,
                handler=getattr(handler, "__qualname__", repr(handler)),
            )
            continue
        succeeded += 1
    logger.debug("event_emitted", topic=event, handlers=len(handlers), ok=succeeded)
    return succeeded


def subscribers(event: str) -> tuple[Handler, ...]:
    """فهرست مشترک‌های یک رخداد — برای تشخیص و تست."""
    return tuple(_HANDLERS.get(event, ()))


def clear(event: str | None = None) -> None:
    """پاک‌کردن مشترک‌ها؛ ``None`` یعنی همه. ویژهٔ تست."""
    if event is None:
        _HANDLERS.clear()
    else:
        _HANDLERS.pop(event, None)
