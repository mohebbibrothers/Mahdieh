"""صفحه‌بندی — مکان‌نما برای دادهٔ متغیر، شماره‌ای برای دادهٔ مدیریتی پایدار.

تصمیم: صفحه‌بندی مکان‌نما (cursor) پیش‌فرض است چون:
    • رزروهای جدید مرتباً اضافه می‌شوند و صفحه‌بندی شماره‌ای باعث
      «پرش/تکرار» ردیف‌ها می‌شود.
    • کارایی روی جدول بزرگ ثابت می‌ماند (بدون OFFSET بزرگ).
"""

from __future__ import annotations

from typing import Any

from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.response import Response


class CursorPageNumberPagination(CursorPagination):
    """صفحه‌بندی مکان‌نما با اندازهٔ قابل‌تنظیم توسط کلاینت."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = "-created_at"
    cursor_query_param = "cursor"


class AdminPageNumberPagination(PageNumberPagination):
    """صفحه‌بندی شماره‌ای — فقط برای پنل مدیریتی که کاربر به صفحه می‌پرد."""

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


class EnvelopedCursorPagination(CursorPageNumberPagination):
    """مکان‌نما با پیوست ``meta`` — برای فهرست‌هایی که فرانت به شمارش نیاز دارد."""

    def get_paginated_response(self, data: list[Any]) -> Response:
        """پاسخ با فرادادهٔ صفحه‌بندی در کلید ``meta``."""
        assert self.cursor is not None
        return Response(
            {
                "results": data,
                "meta": {
                    "next": self.get_next_link(),
                    "previous": self.get_previous_link(),
                    "page_size": self.page_size,
                },
            }
        )
