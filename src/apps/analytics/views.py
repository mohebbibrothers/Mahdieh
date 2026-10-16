"""لایهٔ HTTP تحلیل‌ها — فقط خواندن، فقط کارکنان.

خروجی JSON خالص است (بدون صفحه‌بندی مکانی) چون پاسخ یک «گزارش تحلیلی» است،
نه مجموعه‌ای از رکوردها؛ مصرف‌کننده‌اش داشبورد و ابزارهای بیرونی‌اند.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analytics import insights
from apps.analytics.serializers import InsightsSerializer
from apps.core.schema import PROBLEM_401, PROBLEM_403, PROBLEM_422
from apps.identity.permissions import IsStaffRole


class WindowSerializer(serializers.Serializer[Any]):
    """پنجرهٔ زمانی تحلیل — روز آغاز و طول بازه."""

    start = serializers.DateField(required=False, allow_null=True)
    days = serializers.IntegerField(required=False, min_value=1, max_value=insights.MAX_WINDOW_DAYS)


class InsightsView(APIView):
    """خلاصهٔ تحلیلی زائرسرا: اشغال، روند، واحدها، کیفیت مهمان، پیش‌بینی."""

    permission_classes = [IsAuthenticated, IsStaffRole]

    @extend_schema(
        tags=["تحلیل‌ها"],
        summary="داشبورد تحلیلی (کارکنان)",
        description=(
            "اشغال روزبه‌روز، روند، نقشهٔ حرارتی واحد×روز، فشار پیش‌رو و پیش‌بینی "
            "سادهٔ تقاضا. پنجرهٔ نامعتبر ⇒ ۴۲۲؛ بیش از حد مجاز به سقف سیاست بریده می‌شود."
        ),
        parameters=[
            OpenApiParameter(
                name="start",
                description="روز آغاز پنجره (ISO، پیش‌فرض: امروز)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="days",
                description=f"طول پنجره به روز (۱ تا {insights.MAX_WINDOW_DAYS})",
                required=False,
                type=int,
            ),
        ],
        responses={
            200: InsightsSerializer,
            401: PROBLEM_401,
            403: PROBLEM_403,
            422: PROBLEM_422,
        },
    )
    def get(self, request: Request) -> Response:
        """ساخت داشبورد تحلیلی برای پنجرهٔ درخواستی."""
        params = WindowSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        start: date | None = params.validated_data.get("start")
        days: int = params.validated_data.get("days") or insights.DEFAULT_WINDOW_DAYS
        data = insights.dashboard(start_day=start, days=days)
        data["start"] = data["start_day"].isoformat()
        data["end"] = data["end_day"].isoformat()
        data["points"] = [
            {
                "day": point.day.isoformat(),
                "state": point.state,
                "date_jalali": point.date_jalali,
                "weekday": point.weekday,
                "occupied_cells": point.occupied_cells,
                "free_cells": point.free_cells,
                "total_cells": point.total_cells,
                "rate": point.rate,
            }
            for point in data["points"]
        ]
        for key in ("busiest", "quietest"):
            point = data.get(key)
            data[key] = (
                {
                    "day": point.day.isoformat(),
                    "date_jalali": point.date_jalali,
                    "rate": point.rate,
                }
                if point
                else None
            )
        data["fully_free_days"] = [point.day.isoformat() for point in data["fully_free_days"]]
        data["pressure"] = _pressure_payload(data["pressure"])
        data["planning"] = {
            "start_day": data["planning"]["start_day"].isoformat(),
            "days": data["planning"]["days"],
            "labels": data["planning"]["labels"],
            "rows": data["planning"]["rows"],
        }
        data["forecast"] = [{**row, "day": row["day"].isoformat()} for row in data["forecast"]]
        return Response(data)


def _pressure_payload(pressure: dict[str, Any]) -> dict[str, Any]:
    """ساختار قابل‌سریال‌سازی «فشار پیش‌رو» — تاریخ‌ها به ISO."""
    return {
        "start_day": pressure["start_day"].isoformat(),
        "days": pressure["days"],
        "empty_count": pressure["empty_count"],
        "tight_count": pressure["tight_count"],
        "average_lead_time_days": pressure["average_lead_time_days"],
        "empty_days": [point.day.isoformat() for point in pressure["empty_days"]],
        "tight_days": [
            {"day": point.day.isoformat(), "rate": point.rate} for point in pressure["tight_days"]
        ],
    }
