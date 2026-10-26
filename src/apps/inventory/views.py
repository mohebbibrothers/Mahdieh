"""نماهای HTTP موجودی — واحدها، طبقات، مناسبت‌ها و بلوک‌ها.

همهٔ نماها **خواندنی** هستند جز دو نقطهٔ پایانی بلوک که از سرویس استفاده می‌کنند
(قاعدهٔ لایه‌بندی: هیچ نوشتنی مستقیم روی مدل در نما).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import NotFoundError, ValidationError as DomainValidationError
from apps.core.schema import PROBLEM_401, PROBLEM_403, PROBLEM_404, PROBLEM_409, PROBLEM_422
from apps.identity.permissions import IsStaffRole, authenticated_user
from apps.inventory import selectors, services
from apps.inventory.models import Block
from apps.inventory.serializers import (
    BlockCreateSerializer,
    BlockSerializer,
    FloorSerializer,
    OccasionSerializer,
    UnitDetailSerializer,
    UnitSerializer,
)

DEFAULT_OCCASION_WINDOW_DAYS = 180
MAX_RANGE_DAYS = 365


def _parse_day(raw: str | None, *, fallback: date) -> date:
    """تبدیل رشتهٔ ``YYYY-MM-DD`` به تاریخ با خطای دامنه‌ای قابل‌فهم."""
    if not raw:
        return fallback
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise DomainValidationError(
            "قالب تاریخ درست نیست؛ قالب مورد انتظار YYYY-MM-DD است.", code="invalid_date"
        ) from exc


def _date_range(request: Request, *, days: int) -> tuple[date, date]:
    """بازهٔ تاریخی از پارامترهای ``from``/``to`` با مرز ایمن."""
    today = date.today()
    start = _parse_day(request.query_params.get("from"), fallback=today)
    end = _parse_day(request.query_params.get("to"), fallback=start + timedelta(days=days))
    if end < start:
        raise DomainValidationError("پایان بازه باید بعد از آغاز آن باشد.", code="invalid_range")
    if (end - start).days > MAX_RANGE_DAYS:
        raise DomainValidationError(
            f"حداکثر بازهٔ قابل‌درخواست {MAX_RANGE_DAYS} روز است.", code="range_too_wide"
        )
    return start, end


class UnitListView(APIView):
    """فهرست واحدهای قابل‌رزرو."""

    permission_classes = [AllowAny]

    @extend_schema(
        tags=["واحدها"],
        summary="فهرست واحدهای اقامتی",
        responses={200: UnitSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        """بازگرداندن پنج واحد زائرسرا با طبقه، ظرفیت و امکانات."""
        return Response(
            UnitSerializer(
                selectors.catalogue_units(), many=True, context={"request": request}
            ).data
        )


class UnitDetailView(APIView):
    """جزئیات یک واحد با کد نمایشی."""

    permission_classes = [AllowAny]

    @extend_schema(
        tags=["واحدها"],
        summary="جزئیات واحد",
        responses={200: UnitDetailSerializer, 404: PROBLEM_404},
    )
    def get(self, request: Request, code: str) -> Response:
        """بازگرداندن یک واحد با تصاویر و امکانات."""
        unit = selectors.unit_by_code(code)
        if unit is None:
            raise NotFoundError("واحد مورد نظر وجود ندارد یا غیرفعال شده است.")
        return Response(UnitDetailSerializer(unit, context={"request": request}).data)


class PropertyView(APIView):
    """ساختار فیزیکی زائرسرا — طبقه به طبقه."""

    permission_classes = [AllowAny]

    @extend_schema(
        tags=["واحدها"],
        summary="ساختار طبقات و واحدها",
        responses={200: FloorSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        """بازگرداندن طبقات با واحدهای فعال هر طبقه."""
        return Response(
            FloorSerializer(
                selectors.floors_with_units(), many=True, context={"request": request}
            ).data
        )


class OccasionListView(APIView):
    """مناسبت‌ها و ایام پرتقاضا در یک بازه."""

    permission_classes = [AllowAny]

    @extend_schema(
        tags=["تقویم"],
        summary="مناسبت‌های بازهٔ تاریخی",
        parameters=[
            OpenApiParameter("from", str, description="آغاز بازه (YYYY-MM-DD)"),
            OpenApiParameter("to", str, description="پایان بازه (YYYY-MM-DD)"),
        ],
        responses={200: OccasionSerializer(many=True), 422: PROBLEM_422},
    )
    def get(self, request: Request) -> Response:
        """بازگرداندن مناسبت‌های بازه؛ پیش‌فرض شش ماه آینده."""
        start, end = _date_range(request, days=DEFAULT_OCCASION_WINDOW_DAYS)
        return Response(OccasionSerializer(selectors.occasions_between(start, end), many=True).data)


class BlockListCreateView(APIView):
    """فهرست و ثبت بلوک‌های نگهداری — فقط کارکنان."""

    permission_classes = [IsStaffRole]

    @extend_schema(
        tags=["بلوک نگهداری"],
        summary="فهرست بلوک‌ها در بازه",
        parameters=[
            OpenApiParameter("from", str, description="آغاز بازه (YYYY-MM-DD)"),
            OpenApiParameter("to", str, description="پایان بازه (YYYY-MM-DD)"),
        ],
        responses={200: BlockSerializer(many=True), 401: PROBLEM_401, 403: PROBLEM_403},
    )
    def get(self, request: Request) -> Response:
        """بازگرداندن بلوک‌های فعال هم‌پوشان با بازه."""
        start, end = _date_range(request, days=90)
        window_start = selectors.day_window(start)[0]
        window_end = selectors.day_window(end)[1]
        blocks = selectors.blocks_overlapping(start=window_start, end=window_end)
        return Response(BlockSerializer(blocks, many=True).data)

    @extend_schema(
        tags=["بلوک نگهداری"],
        summary="ثبت بلوک نگهداری",
        description="دلیل اجباری است؛ دو بلوک هم‌پوشان روی یک واحد از سمت دیتابیس رد می‌شود.",
        request=BlockCreateSerializer,
        responses={201: BlockSerializer, 401: PROBLEM_401, 403: PROBLEM_403, 409: PROBLEM_409},
    )
    def post(self, request: Request) -> Response:
        """ثبت بلوک واحد‌محور یا سراسری از طریق سرویس موجودی."""
        serializer = BlockCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data: dict[str, Any] = serializer.validated_data
        unit = None
        if data.get("unit_code"):
            unit = selectors.unit_by_code(data["unit_code"])
            if unit is None:
                raise NotFoundError("واحد انتخاب‌شده وجود ندارد.")
        block = services.create_block(
            unit=unit,
            start=data["start"],
            end=data["end"],
            kind=data["kind"],
            reason=data["reason"],
            actor=authenticated_user(request),
            request=request._request,
        )
        return Response(BlockSerializer(block).data, status=status.HTTP_201_CREATED)


class BlockRemoveView(APIView):
    """برداشتن بلوک نگهداری — فقط مدیر، با دلیل اجباری."""

    permission_classes = [IsStaffRole]

    @extend_schema(
        tags=["بلوک نگهداری"],
        summary="برداشتن بلوک",
        request=BlockCreateSerializer,
        responses={204: None, 401: PROBLEM_401, 403: PROBLEM_403, 404: PROBLEM_404},
    )
    def post(self, request: Request, block_id: str) -> Response:
        """حذف نرم بلوک با ثبت دلیل در حسابرسی."""
        block = Block.objects.filter(id=block_id).first()
        if block is None:
            raise NotFoundError("بلوک مورد نظر یافت نشد.")
        payload = request.data if isinstance(request.data, dict) else {}
        reason = str(payload.get("reason", "")).strip()
        services.remove_block(
            block=block,
            actor=authenticated_user(request),
            reason=reason,
            request=request._request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


def block_period_bounds(block: Block) -> tuple[Any, Any]:
    """مرزهای یک بلوک به‌صورت تاپل (کمکی برای پنل کارکنان)."""
    return block.starts_at, block.ends_at
