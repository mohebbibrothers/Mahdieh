"""API صف انتظار — ثبت، انصراف، فهرست و پذیرش پیشنهاد.

قرارداد خطاها همان قرارداد سراسری سایت است (RFC 9457): ۴۰۰ برای ورودی ناقص،
۴۲۲ برای نقض سیاست با ``violations``، ۴۰۹ برای رقابت روی سانس.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import ConflictError, NotFoundError
from apps.core.schema import PROBLEM_401, PROBLEM_403, PROBLEM_404, PROBLEM_409, PROBLEM_422
from apps.identity.permissions import IsStaffRole
from apps.inventory.selectors import unit_by_code
from apps.waitlist import selectors, services


class JoinSerializer(serializers.Serializer[Any]):
    """بدنهٔ ثبت درخواست انتظار."""

    unit_code = serializers.CharField(max_length=8, required=False, allow_blank=True)
    start = serializers.DateTimeField(help_text="آغاز بازهٔ دلخواه (ISO 8601)")
    end = serializers.DateTimeField(help_text="پایان بازهٔ دلخواه (ISO 8601)")
    guests_count = serializers.IntegerField(min_value=1, max_value=60)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True)


class OfferActionSerializer(serializers.Serializer[Any]):
    """بدنهٔ پاسخ به پیشنهاد."""

    reason_kind = serializers.CharField(max_length=32, required=False, allow_blank=True)


class OfferRespondSerializer(serializers.Serializer[Any]):
    """بدنهٔ پاسخ به پیشنهاد: ``accept`` یا ``decline`` با دلیل اختیاری.

    ``action`` عمداً ``CharField`` است و نه ``ChoiceField``: کنش ناشناخته باید
    مانند گذشته ``۴۰۹`` با کد ``bad_action`` برگرداند (قرارداد خطای یکسان)، نه
    ``۴۲۲`` عمومی سریالایزر.
    """

    action = serializers.CharField(
        max_length=16, required=False, allow_blank=True, help_text="accept (پیش‌فرض) یا decline"
    )
    reason_kind = serializers.CharField(max_length=32, required=False, allow_blank=True)


class WaitlistSerializer(serializers.Serializer[Any]):
    """نمایش یک درخواست صف انتظار."""

    id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    status_label = serializers.CharField(read_only=True)
    unit_code = serializers.CharField(source="unit.code", read_only=True, default=None)
    unit_title = serializers.CharField(source="unit.title", read_only=True, default=None)
    starts_at = serializers.DateTimeField(read_only=True)
    ends_at = serializers.DateTimeField(read_only=True)
    cells_count = serializers.IntegerField(read_only=True)
    guests_count = serializers.IntegerField(read_only=True)
    offers_sent = serializers.IntegerField(read_only=True)
    position = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)

    def get_position(self, entry: Any) -> int:
        """نوبت فعلی درخواست در صف."""
        return services.queue_position(entry)


class SlotOfferSerializer(serializers.Serializer[Any]):
    """نمایش یک پیشنهاد سانس."""

    id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    unit_code = serializers.CharField(source="unit.code", read_only=True)
    starts_at = serializers.DateTimeField(read_only=True)
    ends_at = serializers.DateTimeField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)
    seconds_left = serializers.IntegerField(read_only=True)
    entry_id = serializers.UUIDField(source="entry.id", read_only=True)


class WaitlistListSerializer(serializers.Serializer[Any]):
    """پوشش پاسخ فهرست — ``{"results": [...]}`` (فقط برای سند OpenAPI)."""

    results = WaitlistSerializer(many=True)


class OfferAcceptedSerializer(serializers.Serializer[Any]):
    """پاسخ پذیرش پیشنهاد: خود پیشنهاد + رزرو ساخته‌شده (فقط برای سند)."""

    offer = SlotOfferSerializer()
    reservation_code = serializers.CharField()
    reservation_id = serializers.UUIDField()


class StaffWaitlistSerializer(serializers.Serializer[Any]):
    """پاسخ نمای کارکنان: صف، پیشنهادهای باز، آمار و نمای کلی (فقط برای سند)."""

    queue = WaitlistSerializer(many=True)
    open_offers = SlotOfferSerializer(many=True)
    stats = serializers.DictField()
    overview = serializers.DictField()


class WaitlistListCreateView(APIView):
    """فهرست درخواست‌های من + ثبت درخواست تازه."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["صف انتظار"],
        summary="درخواست‌های در جریان من",
        responses={200: WaitlistListSerializer, 401: PROBLEM_401},
    )
    def get(self, request: Request) -> Response:
        """درخواست‌های «در جریان» زائر."""
        entries = selectors.entries_for_user(request.user)
        return Response({"results": WaitlistSerializer(entries, many=True).data})

    @extend_schema(
        tags=["صف انتظار"],
        summary="ثبت درخواست انتظار",
        description=(
            "برای بازه‌ای که پُر است، نوبت می‌گیرید. ``unit_code`` تهی ⇒ آمادگی "
            "برای هر واحد آزادی در آن بازه. نوبت‌ها بر پایهٔ زمان ثبت و بدون "
            "پرش‌زدن رعایت می‌شوند."
        ),
        request=JoinSerializer,
        responses={201: WaitlistSerializer, 401: PROBLEM_401, 404: PROBLEM_404, 422: PROBLEM_422},
    )
    def post(self, request: Request) -> Response:
        """ثبت درخواست انتظار تازه."""
        payload = JoinSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        unit = None
        if data.get("unit_code"):
            unit = unit_by_code(data["unit_code"])
            if unit is None:
                raise NotFoundError("واحد مورد نظر یافت نشد.")
        entry = services.join_waitlist(
            guest=request.user,
            unit=unit,
            start=data["start"],
            end=data["end"],
            guests_count=data["guests_count"],
            note=data.get("note", ""),
            source="api",
            request=request,
        )
        return Response(WaitlistSerializer(entry).data, status=201)


class WaitlistWithdrawView(APIView):
    """انصراف از درخواست (زائر یا کارکنان)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["صف انتظار"],
        summary="انصراف از نوبت",
        description="درخواست را می‌بندد و نوبت را به‌ترتیب به نفر بعدی می‌دهد.",
        request=OfferActionSerializer,
        responses={200: WaitlistSerializer, 401: PROBLEM_401, 404: PROBLEM_404},
    )
    def post(self, request: Request, entry_id: Any) -> Response:
        """بستن درخواست با ثبت دلیل."""
        entry = selectors.entry_by_id(entry_id)
        if entry is None:
            raise NotFoundError("درخواست صف انتظار یافت نشد.")
        payload = OfferActionSerializer(data=request.data if isinstance(request.data, dict) else {})
        payload.is_valid(raise_exception=True)
        updated = services.withdraw_entry(
            entry=entry,
            by=request.user,
            reason=payload.validated_data.get("reason_kind", ""),
            request=request,
        )
        return Response(WaitlistSerializer(updated).data)


class OfferRespondView(APIView):
    """پذیرش یا رد یک پیشنهاد سانس."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["صف انتظار"],
        summary="پاسخ به پیشنهاد سانس",
        description=(
            "پیشنهاد سانس آزادشده را می‌پذیرید (رزرو ساخته می‌شود) یا رد می‌کنید "
            "(سانس به نفر بعدی می‌رسد). مهلت پاسخ محدود است؛ پیشنهاد منقضی ⇒ ۴۰۹."
        ),
        request=OfferRespondSerializer,
        responses={
            200: SlotOfferSerializer,
            201: OfferAcceptedSerializer,
            401: PROBLEM_401,
            404: PROBLEM_404,
            409: PROBLEM_409,
        },
    )
    def post(self, request: Request, offer_id: Any) -> Response:
        """پاسخ به پیشنهاد بر پایهٔ بدنهٔ ``{"action": "accept|decline"}``."""
        offer = selectors.offer_by_token_payload(offer_id)
        if offer is None:
            raise NotFoundError("پیشنهاد یافت نشد.")
        # بدنهٔ درخواست می‌تواند لیست باشد (JSON نامعتبر از دید ما)؛ پس با
        # ``isinstance`` گارد می‌کنیم تا هیچ ورودی‌ای باعث ۵۰۰ نشود.
        data = request.data if isinstance(request.data, dict) else {}
        action = str(data.get("action", "accept"))
        payload = OfferActionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if action == "decline":
            declined = services.decline_offer(
                offer=offer,
                by=request.user,
                reason_kind=payload.validated_data.get("reason_kind") or "time_not_suitable",
                request=request,
            )
            return Response(SlotOfferSerializer(declined).data)
        if action != "accept":
            raise ConflictError(
                "کنش نامعتبر است؛ «accept» یا «decline» بفرستید.", code="bad_action"
            )
        _, reservation = services.accept_offer(offer=offer, by=request.user, request=request)
        return Response(
            {
                "offer": SlotOfferSerializer(offer).data,
                "reservation_code": reservation.code,
                "reservation_id": str(reservation.id),
            },
            status=201,
        )


class StaffWaitlistView(APIView):
    """نمای کارکنان: کل صف + پیشنهادهای باز + آمار."""

    permission_classes = [IsAuthenticated, IsStaffRole]

    @extend_schema(
        tags=["صف انتظار"],
        summary="کل صف (کارکنان)",
        description="صف کامل + پیشنهادهای باز + آمار — مبنای تصمیم میز پذیرش.",
        responses={200: StaffWaitlistSerializer, 401: PROBLEM_401, 403: PROBLEM_403},
    )
    def get(self, request: Request) -> Response:  # noqa: ARG002 - قرارداد DRF
        """صف، پیشنهادهای باز و خلاصهٔ آماری."""
        entries = selectors.queue_page(statuses=None, limit=100)
        offers = selectors.open_offers(limit=100)
        return Response(
            {
                "queue": WaitlistSerializer(entries, many=True).data,
                "open_offers": SlotOfferSerializer(offers, many=True).data,
                "stats": services.queue_stats(),
                "overview": {
                    key: value
                    for key, value in selectors.waitlist_overview().items()
                    if key not in {"oldest_waiting", "busiest_unit", "by_status"}
                },
            }
        )
