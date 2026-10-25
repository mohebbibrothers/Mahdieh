"""صفحه‌بندی — مکان‌نما برای دادهٔ متغیر، شماره‌ای برای دادهٔ مدیریتی پایدار.

تصمیم: صفحه‌بندی مکان‌نما (cursor) پیش‌فرض است چون:
    • رزروهای جدید مرتباً اضافه می‌شوند و صفحه‌بندی شماره‌ای باعث
      «پرش/تکرار» ردیف‌ها می‌شود.
    • کارایی روی جدول بزرگ ثابت می‌ماند (بدون OFFSET بزرگ).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.db.models import QuerySet
from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response


class CursorPageNumberPagination(CursorPagination):
    """صفحه‌بندی مکان‌نما با اندازهٔ قابل‌تنظیم توسط کلاینت.

    ``ordering`` دو عضوی است: ``-created_at`` به‌تنهایی **یکتا نیست** (دو رزرو
    هم‌ثانیه) و مکان‌نمای DRF روی ترتیب نایکتا، ردیف را می‌تواند رد یا تکرار کند.
    افزودن ``-id`` (UUIDv7، زمان‌مرتب) ترتیب را قطعی می‌کند.
    """

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = ("-created_at", "-id")
    cursor_query_param = "cursor"


class AdminPageNumberPagination(PageNumberPagination):
    """صفحه‌بندی شماره‌ای — فقط برای پنل مدیریتی که کاربر به صفحه می‌پرد."""

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


class EnvelopedCursorPagination(CursorPageNumberPagination):
    """مکان‌نما با پیوست ``meta`` — قرارداد یکسان برای همهٔ فهرست‌های رشدی.

    شکل پاسخ: ``{"results": [...], "meta": {"next", "previous", "page_size"}}``.
    ``total`` اختیاری است و فقط برای فهرست‌های **کاربرمحور** روشن می‌شود؛ آن‌جا
    شمارش کل، یک کوئری فهرست‌شده روی چند ردیف است و ارزشش را دارد (فرانت
    «۳ رزرو فعال» را بدون صفحه‌گردی نشان می‌دهد). برای میز کارکنان، شمارش کل
    روی میز بزرگ گران است و عمداً فرستاده نمی‌شود.
    """

    include_total = False
    total: int | None = None

    def paginate_queryset(
        self,
        queryset: QuerySet[Any] | Sequence[Any],
        request: Request,
        view: Any = None,
    ) -> list[Any] | None:
        """شمارش کل (در صورت درخواست) و سپس صفحه‌بندی مکان‌نما.

        امضای والد هم QuerySet می‌پذیرد و هم Sequence (فهرست آماده)؛ ما هم همان
        قرارداد را رعایت می‌کنیم تا بازبین نوع‌ها شکایت نکند.
        """
        if self.include_total:
            self.total = queryset.count() if isinstance(queryset, QuerySet) else len(queryset)
        else:
            self.total = None
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data: Any) -> Response:
        """پاسخ با فرادادهٔ صفحه‌بندی در کلید ``meta``.

        نکته: ``self.cursor`` در **صفحهٔ نخست** تهی است (کاربر هنوز مکان‌نما
        نفرستاده)؛ پس هیچ فرضی روی حضورش نمی‌گذاریم — ``get_next_link`` خودش
        این حالت را می‌شناسد. (در نخستین اجرای واقعی همین فرضِ اضافه، ۵۰۰ می‌داد.)
        """
        meta: dict[str, Any] = {
            "next": self.get_next_link(),
            "previous": self.get_previous_link(),
            "page_size": self.page_size,
        }
        if self.include_total:
            meta["total"] = self.total
        return Response({"results": list(data), "meta": meta})


class OwnerCursorPagination(EnvelopedCursorPagination):
    """فهرست‌های «مال من» — کوچک، با شمارش کل (فرانت بدون صفحه‌گردی می‌شمارد)."""

    include_total = True


class StayCursorPagination(OwnerCursorPagination):
    """سابقهٔ اقامت زائر — به ترتیب **زمان اقامت** (تازه‌ترین سفر نخست)، نه زمان ثبت.

    مکان‌نمای DRF ترتیب خودش را بر کوئری تحمیل می‌کند؛ ترتیب ``-created_at`` پیش‌فرض،
    ``order_by`` گزینشگر را بی‌صدا کنار می‌گذاشت و رزرو دیرتر ثبت‌شده برای سفری زودتر
    جابه‌جا نمایش داده می‌شد. ``-id`` (UUIDv7) ترتیب را برای دو اقامت هم‌آغاز قطعی می‌کند.
    """

    ordering = ("-starts_at", "-id")


class UpcomingStayCursorPagination(StayCursorPagination):
    """اقامت‌های جاری/پیش‌رو — نزدیک‌ترین سفر نخست (کارت «اقامت فعال» پرتال)."""

    ordering = ("starts_at", "id")


class StaffBoardCursorPagination(EnvelopedCursorPagination):
    """میز کارکنان — صفحهٔ بزرگ‌تر، بدون شمارش کل (کوئری گران، ارزش کم)."""

    page_size = 50


class StaffDirectoryCursorPagination(StaffBoardCursorPagination):
    """دفتر زائران — ترتیب «تازه‌ترین عضو» (``date_joined`` + شناسه برای قطعیت)."""

    ordering = ("-date_joined", "-id")
