"""پیکربندی یکجای فرانت — «هرچه فرانت باید بداند، از یک جا و با یک درخواست».

چرا؟ بدون این نقطه، فرانت یا عددها را سخت‌کد می‌کرد (سقف اقامت، ساعت آغاز
شبکه، کد وضعیت‌ها) یا برای هر کدام یک درخواست جدا می‌زد. حالا یک پاسخ کوچک،
با ETag و کش مرورگر/لبه، همهٔ این‌ها را می‌دهد و با هر تغییر سیاست خودبه‌خود
تازه می‌شود (ETag از خودِ محتوا ساخته می‌شود؛ هیچ نسخه‌ای دستی نیست).

هیچ دادهٔ شخصی یا محرمانه‌ای این‌جا نیست؛ پاسخ برای همه یکسان و قابل‌کش است.
"""

from __future__ import annotations

import json
from typing import Any, Final

from django.conf import settings
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.conditional import conditional_response, weak_etag
from apps.identity.choices import IdDocumentType, Province
from apps.identity.countries import country_payload
from apps.identity.services import CODE_LENGTH
from apps.inventory import selectors as inventory_selectors
from apps.inventory.choices import UnitType
from apps.reservation.choices import (
    CancelReasonKind,
    ReservationEventKind,
    ReservationSource,
    ReservationStatus,
)
from apps.reservation.policy import LABELS, all_policies, lattice_config

CONFIG_CACHE_CONTROL: Final[str] = "public, max-age=60, stale-while-revalidate=600"
SCHEMA_VERSION: Final[int] = 1


def _choices(enum: Any) -> list[dict[str, str]]:
    """نمای ``[{value, label}]`` از یک ``TextChoices`` (ترتیب تعریف حفظ می‌شود)."""
    return [{"value": member.value, "label": str(member.label)} for member in enum]


def _policies() -> list[dict[str, Any]]:
    """همهٔ سیاست‌ها با برچسب فارسی (بدون نام تغییردهنده و دلیل — آن مال پنل است)."""
    rows: list[dict[str, Any]] = []
    for item in all_policies():
        title, description = LABELS.get(item.key, (item.key, ""))
        rows.append(
            {"key": item.key, "value": item.value, "label": title, "description": description}
        )
    return rows


def _units() -> list[dict[str, Any]]:
    """واحدهای فعال به ترتیب نمایش — همان ترتیب تقویم."""
    return [
        {
            "code": unit.code,
            "title": unit.title,
            "floor_number": unit.floor.number,
            "unit_type": unit.unit_type,
            "capacity_total": unit.capacity_total,
        }
        for unit in inventory_selectors.active_units()
    ]


def build_config() -> dict[str, Any]:
    """ساخت کامل بدنهٔ پیکربندی (ارزان: سیاست‌ها کش نسخه‌دار دارند)."""
    lattice = lattice_config()
    return {
        "schema_version": SCHEMA_VERSION,
        "site": {
            "name": "مهدیه",
            "timezone": settings.TIME_ZONE,
            "support_phone": str(getattr(settings, "SUPPORT_PHONE", "") or ""),
            "support_address": str(getattr(settings, "SUPPORT_ADDRESS", "") or ""),
        },
        "features": {
            "international_phones": bool(getattr(settings, "INTERNATIONAL_PHONES_ENABLED", False)),
            "staff_mfa_required": bool(getattr(settings, "STAFF_MFA_REQUIRED", True)),
        },
        "auth": {
            "otp_length": CODE_LENGTH,
            "otp_ttl_seconds": int(getattr(settings, "OTP_TTL_SECONDS", 120)),
            "otp_resend_seconds": int(getattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 60)),
            "mfa_code_length": 6,
        },
        "lattice": {
            "cell_hours": lattice.cell_hours,
            "grid_start_hour": lattice.grid_start_hour,
            "cells_per_day": max(1, 24 // max(1, lattice.cell_hours)),
        },
        "policies": _policies(),
        "units": _units(),
        "provinces": _choices(Province),
        "countries": country_payload(),
        "labels": {
            "reservation_status": _choices(ReservationStatus),
            "reservation_source": _choices(ReservationSource),
            "cancel_reason_kind": _choices(CancelReasonKind),
            "event_kind": _choices(ReservationEventKind),
            "unit_type": _choices(UnitType),
            "id_document_type": _choices(IdDocumentType),
        },
        "status_groups": {
            "active": [str(status) for status in ReservationStatus.active_statuses()],
            "cancellable": [str(status) for status in ReservationStatus.cancellable_statuses()],
            "terminal": [str(status) for status in ReservationStatus.terminal_statuses()],
        },
    }


class ConfigSerializer(serializers.Serializer[Any]):
    """پاسخ پیکربندی فرانت (سند؛ زیرساختارها ``object`` تعریف شده‌اند)."""

    schema_version = serializers.IntegerField()
    site = serializers.DictField()
    features = serializers.DictField(child=serializers.BooleanField())
    auth = serializers.DictField(child=serializers.IntegerField())
    lattice = serializers.DictField(child=serializers.IntegerField())
    policies = serializers.ListField(child=serializers.DictField())
    units = serializers.ListField(child=serializers.DictField())
    provinces = serializers.ListField(child=serializers.DictField())
    countries = serializers.ListField(child=serializers.DictField())
    labels = serializers.DictField()
    status_groups = serializers.DictField()


class ConfigView(APIView):
    """پیکربندی عمومی فرانت — قابل‌کش با ETag."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []

    @extend_schema(
        tags=["پیکربندی"],
        summary="پیکربندی فرانت (سیاست‌ها، شبکهٔ زمانی، برچسب‌ها، کشورها)",
        parameters=[
            OpenApiParameter(
                "If-None-Match",
                str,
                location=OpenApiParameter.HEADER,
                description="ETag پاسخ قبلی ⇒ ۳۰۴ اگر چیزی عوض نشده باشد",
            )
        ],
        responses={200: ConfigSerializer, 304: None},
    )
    def get(self, request: Request) -> Response:
        """ساخت پیکربندی و پاسخ شرطی."""
        payload = build_config()
        etag = weak_etag(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str))
        return conditional_response(
            request, etag=etag, build=lambda: payload, cache_control=CONFIG_CACHE_CONTROL, vary=""
        )
