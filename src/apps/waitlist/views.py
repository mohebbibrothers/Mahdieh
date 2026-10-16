"""API صف انتظار — ثبت، انصراف، فهرست و پذیرش پیشنهاد.

قرارداد خطاها همان قرارداد سراسری سایت است (RFC 9457): ۴۰۰ برای ورودی ناقص،
۴۲۲ برای نقض سیاست با ``violations``، ۴۰۹ برای رقابت روی سانس.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import ConflictError, NotFoundError
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


class WaitlistListCreateView(APIView):
    """فهرست درخواست‌های من + ثبت درخواست تازه."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        """درخواست‌های «در جریان» زائر."""
        entries = selectors.entries_for_user(request.user)
        return Response({"results": WaitlistSerializer(entries, many=True).data})

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
