"""نمای گزارش‌ها — خروجی اکسل و خلاصهٔ پیش‌نمایش برای کارکنان."""

from __future__ import annotations

from typing import Any

from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.schema import PROBLEM_403, PROBLEM_422
from apps.identity.permissions import IsStaffRole, authenticated_user
from apps.reporting.archive import XLSX_CONTENT_TYPE
from apps.reporting.ranges import MAX_SPAN_DAYS, parse_range
from apps.reporting.services import build_report, summary_only

UNIT_PARAM = OpenApiParameter(
    "units", str, description=_("کد واحدها با کاما؛ خالی = همهٔ واحدها (مثال: A1,B1)")
)
STATUS_PARAM = OpenApiParameter(
    "status", str, description=_("وضعیت رزروها با کاما؛ خالی = همهٔ وضعیت‌ها")
)


def _parse_csv(raw: str | None) -> tuple[str, ...]:
    """تفسیر فهرست جداشده با کاما (پاک‌سازی فاصله‌ها و مقادیر خالی)."""
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


class ReservationReportView(APIView):
    """دریافت گزارش اکسل رزروها، سانس‌ها و اشغال واحدها."""

    permission_classes = [IsStaffRole]

    @extend_schema(
        tags=["گزارش"],
        summary="خروجی اکسل گزارش رزروها",
        description=(
            "یک کارپوشهٔ هفت‌برگی راست‌به‌چپ برمی‌گرداند: راهنما، رزروها، تقویم "
            f"سانس‌ها، اشغال واحدها، آمار روزانه، کنش‌های کارکنان و پیامک‌ها. "
            f"بیشترین بازه {MAX_SPAN_DAYS} روز است و هش فایل در هدر "
            "``X-Report-SHA256`` می‌آید."
        ),
        parameters=[
            OpenApiParameter("from", str, description=_("از تاریخ (YYYY-MM-DD)")),
            OpenApiParameter("to", str, description=_("تا تاریخ (YYYY-MM-DD)")),
            UNIT_PARAM,
            STATUS_PARAM,
        ],
        responses={
            (200, XLSX_CONTENT_TYPE): OpenApiTypes.BINARY,
            403: PROBLEM_403,
            422: PROBLEM_422,
        },
    )
    def get(self, request: Request) -> HttpResponse:
        """ساخت و ارسال فایل گزارش با مهر هش."""
        report = build_report(
            from_raw=request.query_params.get("from"),
            to_raw=request.query_params.get("to"),
            unit_codes=_parse_csv(request.query_params.get("units")),
            statuses=_parse_csv(request.query_params.get("status")),
            actor=authenticated_user(request),
            request=request._request,
        )
        response = HttpResponse(report.content, content_type=XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = f'attachment; filename="{report.filename}"'
        response["Content-Length"] = str(len(report.content))
        response["X-Report-SHA256"] = report.sha256
        response["X-Report-Rows"] = str(sum(report.rows.values()))
        response["Cache-Control"] = "no-store"
        return response


class ReportSummaryView(APIView):
    """پیش‌نمایش عددی گزارش — کارت‌های آماری پیش از دانلود."""

    permission_classes = [IsStaffRole]

    @extend_schema(
        tags=["گزارش"],
        summary="خلاصهٔ گزارش (پیش‌نمایش)",
        parameters=[
            OpenApiParameter("from", str, description=_("از تاریخ (YYYY-MM-DD)")),
            OpenApiParameter("to", str, description=_("تا تاریخ (YYYY-MM-DD)")),
            UNIT_PARAM,
        ],
        responses={200: dict, 403: PROBLEM_403, 422: PROBLEM_422},
    )
    def get(self, request: Request) -> Response:
        """جمع‌بندی مدیریتی بازه: شمار رزروها، نرخ اشغال و اثر انگشت محتوا."""
        report_range = parse_range(
            from_raw=request.query_params.get("from"),
            to_raw=request.query_params.get("to"),
        )
        payload: dict[str, Any] = summary_only(
            report_range=report_range,
            unit_codes=_parse_csv(request.query_params.get("units")),
        )
        return Response(payload)


__all__ = ["ReportSummaryView", "ReservationReportView"]
