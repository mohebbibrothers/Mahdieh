"""گزینش دادهٔ صف انتظار — خواندن‌های تخصصی، بدون منطق تجاری.

قاعدهٔ عدالت («نوبت = نخستین زمان ورود یا بازگشت به صف») این‌جا **یک‌بار**
بیان می‌شود و همهٔ مصرف‌کننده‌ها از همین عبارت استفاده می‌کنند.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db.models import Count, Expression, Q, QuerySet
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.waitlist.choices import OfferStatus, WaitlistStatus
from apps.waitlist.models import SlotOffer, WaitlistEntry

MY_ENTRIES = "mine"


def queue_key_expression() -> Expression:
    """عبارت کلید نوبت: نخستین زمان «ورود یا بازگشت به صف».

    درخواستی که پیشنهادش را رد کرده و به انتهای صف برگشته، مؤثرتر از زمان
    ثبت اولیه‌اش دیده می‌شود — همین یک خط، عدالت صف را تضمین می‌کند.
    """
    return Coalesce("requeued_at", "created_at")


def entries_for_user(user: Any, *, scope: str = MY_ENTRIES) -> QuerySet[WaitlistEntry]:
    """درخواست‌های یک زائر — فعال‌ها یا همه."""
    queryset = WaitlistEntry.objects.filter(guest=user)
    if scope == MY_ENTRIES:
        return queryset.filter(status__in=WaitlistStatus.active_statuses()).select_related("unit")
    return queryset.select_related("unit").order_by("-created_at")


def active_entry_for(user: Any) -> WaitlistEntry | None:
    """تنها درخواست در جریانِ یک زائر (اگر باشد)."""
    return entries_for_user(user).first()


def entry_by_id(entry_id: Any) -> WaitlistEntry | None:
    """درخواست بر پایهٔ شناسه (با واحد و زائر آمادهٔ نمایش)."""
    return (
        WaitlistEntry.objects.select_related("unit", "guest", "converted_reservation")
        .filter(pk=entry_id)
        .first()
    )


def queue_page(*, statuses: tuple[str, ...] | None = None, limit: int = 50) -> list[WaitlistEntry]:
    """صفحهٔ صف به ترتیب نوبت — برای تابلوی کارکنان."""
    if statuses is None:
        statuses = WaitlistStatus.active_statuses()
    return list(
        WaitlistEntry.objects.select_related("unit", "guest")
        .filter(status__in=statuses)
        .annotate(queue_key=queue_key_expression())
        .order_by("queue_key", "id")[:limit]
    )


def open_offers(*, limit: int = 50) -> list[SlotOffer]:
    """پیشنهادهای باز، نزدیک‌ترین مهلت اول."""
    return list(
        SlotOffer.objects.select_related("unit", "entry", "entry__guest")
        .filter(status=OfferStatus.OPEN)
        .order_by("expires_at")[:limit]
    )


def open_offer_for_entry(entry: WaitlistEntry) -> SlotOffer | None:
    """پیشنهاد بازِ یک درخواست خاص (اگر داشته باشد).

    قاعدهٔ دامنه تضمین می‌کند برای هر درخواست حداکثر **یک** پیشنهاد باز وجود
    دارد؛ پس ``first()`` این‌جا قطعی است و نه دلبخواهی.
    """
    return (
        SlotOffer.objects.select_related("unit", "entry")
        .filter(entry=entry, status=OfferStatus.OPEN)
        .order_by("expires_at")
        .first()
    )


def offer_by_token_payload(offer_id: Any) -> SlotOffer | None:
    """پیشنهاد بر پایهٔ شناسه (برای لینک یک‌ضربه‌ای امضاشده)."""
    return (
        SlotOffer.objects.select_related("unit", "entry", "entry__guest", "entry__unit")
        .filter(pk=offer_id)
        .first()
    )


def offers_history(entry: WaitlistEntry) -> list[SlotOffer]:
    """تاریخچهٔ پیشنهادهای یک درخواست."""
    return list(entry.offers.select_related("unit").order_by("-created_at"))


def expired_candidates(*, moment: datetime | None = None) -> QuerySet[SlotOffer]:
    """پیشنهادهای گذشته از مهلت که هنوز بسته نشده‌اند."""
    return SlotOffer.objects.filter(
        status=OfferStatus.OPEN, expires_at__lte=moment or timezone.now()
    )


def waitlist_overview() -> dict[str, Any]:
    """نمای کلی صف برای داشبورد: شمار بر پایهٔ وضعیت و ساعت‌های اوج درخواست."""
    by_status = dict(
        WaitlistEntry.objects.values_list("status").annotate(total=Count("id")).order_by()
    )
    busiest_unit = (
        WaitlistEntry.objects.filter(status__in=WaitlistStatus.active_statuses())
        .values("unit__code")
        .annotate(total=Count("id"))
        .order_by("-total")
        .first()
    )
    conversion = WaitlistEntry.objects.filter(
        Q(status=WaitlistStatus.CONVERTED) | Q(status__in=WaitlistStatus.terminal_statuses())
    ).aggregate(
        converted=Count("id", filter=Q(status=WaitlistStatus.CONVERTED)),
        total=Count("id"),
    )
    total = conversion["total"] or 0
    return {
        "by_status": by_status,
        "waiting": by_status.get(WaitlistStatus.WAITING, 0),
        "offered": by_status.get(WaitlistStatus.OFFERED, 0),
        "converted": by_status.get(WaitlistStatus.CONVERTED, 0),
        "unfulfilled": by_status.get(WaitlistStatus.UNFULFILLED, 0),
        "conversion_rate": round(conversion["converted"] / total, 4) if total else 0.0,
        "busiest_unit": busiest_unit,
        "oldest_waiting": (
            WaitlistEntry.objects.filter(status=WaitlistStatus.WAITING)
            .annotate(queue_key=queue_key_expression())
            .order_by("queue_key")
            .first()
        ),
    }
