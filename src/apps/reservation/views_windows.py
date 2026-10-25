"""نمای «کشف بازهٔ آزاد» — نخستین زمان آزاد هر واحد در کل افق رزرو (P-04)."""

from __future__ import annotations

import time
from typing import Any

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.cache import get_epoch
from apps.core.conditional import PUBLIC_REVALIDATE, conditional_response, weak_etag
from apps.core.errors import ValidationError as DomainValidationError
from apps.core.schema import PROBLEM_422
from apps.reservation import availability
from apps.reservation.policy import POLICY_NAMESPACE, POLICY_SCOPE, policy_int
from apps.reservation.serializers import CalendarWindowsSerializer
from apps.reservation.windows import first_free_windows

MAX_GUESTS = 60
# مرز «زودترین آغاز» با گذر زمان جلو می‌رود؛ تگ هر پنج دقیقه تازه می‌شود تا پیشنهادِ
# سانسِ ازدست‌رفته از کش مرورگر برنگردد (هر تغییر رزرو/بلوک هم با epoch تگ را می‌شکند).
WINDOWS_ETAG_BUCKET_SECONDS = 300


def _bounded_int(request: Request, name: str, *, default: int, low: int, high: int) -> int:
    """خواندن پارامتر عددی با مرز؛ مقدار نامعتبر ⇒ ۴۲۲ با پیام فارسی."""
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise DomainValidationError(
            f"پارامتر {name} باید عدد باشد.", code=f"invalid_{name}"
        ) from exc
    if value < low or value > high:
        raise DomainValidationError(
            f"پارامتر {name} باید بین {low} و {high} باشد.", code=f"invalid_{name}"
        )
    return value


class CalendarWindowsView(APIView):
    """نخستین بازهٔ پیوستهٔ آزاد هر واحد — عمومی، قابل‌کش، با قواعد خود موتور رزرو."""

    permission_classes = [AllowAny]

    @extend_schema(
        tags=["تقویم"],
        summary="کشف نخستین بازهٔ آزاد هر واحد",
        description=(
            "برای هر واحد فعال، زودترین ران پیوستهٔ نیم‌روزهای آزاد را در کل افق رزرو "
            "برمی‌گرداند (آغاز دست‌کم به‌اندازهٔ حداقل مهلت پیش از ورود پس از اکنون). "
            "واحدی که ظرفیتش از ``guests`` کمتر است ``fits_guests=false`` و بازهٔ تهی دارد."
        ),
        parameters=[
            OpenApiParameter(
                "guests", int, description=f"تعداد همسفران (۱ تا {MAX_GUESTS})؛ پیش‌فرض ۱"
            ),
            OpenApiParameter(
                "min_cells", int, description="کوتاه‌ترین ران پذیرفتنی (نیم‌روز)؛ پیش‌فرض کف سیاست"
            ),
            OpenApiParameter(
                "If-None-Match",
                str,
                location=OpenApiParameter.HEADER,
                description="ETag پاسخ قبلی؛ بی‌تغییر ⇒ ۳۰۴ بدون بدنه",
            ),
        ],
        responses={200: CalendarWindowsSerializer, 304: None, 422: PROBLEM_422},
    )
    def get(self, request: Request) -> Response:
        """کشف بازه با پارامترهای اعتبارسنجی‌شده و پاسخ شرطی."""
        max_cells = policy_int("reservation.max_cells")
        guests = _bounded_int(request, "guests", default=1, low=1, high=MAX_GUESTS)
        min_cells = _bounded_int(
            request,
            "min_cells",
            default=policy_int("reservation.min_cells"),
            low=1,
            high=max_cells,
        )
        etag = weak_etag(
            "calendar-windows",
            get_epoch(availability.AVAILABILITY_NAMESPACE, availability.SCOPE_PUBLIC),
            get_epoch(POLICY_NAMESPACE, POLICY_SCOPE),
            guests,
            min_cells,
            int(time.time() // WINDOWS_ETAG_BUCKET_SECONDS),
        )

        def build() -> dict[str, Any]:
            """محاسبهٔ کشف — فقط وقتی کلاینت نسخهٔ تازه را ندارد."""
            result = first_free_windows(guests=guests, min_cells=min_cells)
            return dict(CalendarWindowsSerializer(result).data)

        return conditional_response(
            request, etag=etag, build=build, cache_control=PUBLIC_REVALIDATE
        )
