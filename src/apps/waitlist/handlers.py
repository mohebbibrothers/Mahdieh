"""مشترک‌های رخداد صف انتظار — اتصال به دامنهٔ رزرو بدون وابستگی حلقه‌ای.

این ماژول تنها جایی است که «رزرو» و «صف انتظار» به هم می‌رسند؛ از طریق
``apps.core.events``. اگر روزی صف انتظار از پروژه برداشته شود، فقط همین فایل
و ثبت‌نام آن در ``apps.py`` حذف می‌شود — دامنهٔ رزرو دست‌نخورده می‌ماند.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog

from apps.core import events
from apps.waitlist import services

logger = structlog.get_logger(__name__)


@events.subscribe(events.SLOT_FREED)
def handle_slot_freed(payload: Mapping[str, Any]) -> None:
    """سانس آزاد شد ⇒ پیشنهاد به نفر اول صف (اگر سیاست اجازه دهد)."""
    created = services.slot_free_handler(payload)
    if created:
        logger.info(
            "waitlist_offer_from_event",
            unit=str(payload.get("unit_id")),
            cause=str(payload.get("cause", "unknown")),
        )


def subscribe_all() -> None:
    """ثبت‌نام دوبارهٔ مشترک‌ها (پس از ``events.clear`` در تست)."""
    events.subscribe(events.SLOT_FREED)(handle_slot_freed)
