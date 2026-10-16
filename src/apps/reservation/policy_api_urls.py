"""API سیاست‌ها — خواندن برای کارکنان، نوشتن فقط برای مدیر.

چرا جداست؟ ``api_urls.py`` رزرو به دامنهٔ رزرو می‌پردازد؛ تغییر سیاست یک کنش
**حاکمیتی** است و جای خودش را دارد: مسیر جدا، دسترسی جدا، ممیزی جدا.
"""

from __future__ import annotations

from typing import Any

from django.urls import path
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import NotFoundError, PermissionDeniedError
from apps.core.schema import PROBLEM_401, PROBLEM_403, PROBLEM_404, PROBLEM_422
from apps.identity.permissions import IsManagerRole, IsStaffRole, authenticated_user
from apps.reservation.policy import all_policies, set_policy


class PolicyValueSerializer(serializers.Serializer[Any]):
    """یک سیاست با مقدار مؤثر، پیش‌فرض، نوع فارسی و تاریخ آخرین تغییر."""

    key = serializers.CharField()
    # ``label`` و ``help_text`` نام‌های عمدی و هم‌نام با مفهومشان در دامنه‌اند؛
    # در DRF همین دو نام روی کلاس پایهٔ ``Field`` هم وجود دارند، پس ایگنور نوع
    # این‌جا توضیح‌دار است، نه راه‌حل سرسری (همان سبک ``serializers.py``).
    label = serializers.CharField()  # type: ignore[assignment]
    help_text = serializers.CharField()  # type: ignore[assignment]
    value = serializers.JSONField()
    default = serializers.JSONField()
    type = serializers.CharField()
    version = serializers.IntegerField()
    changed = serializers.BooleanField()
    updated_at = serializers.DateTimeField(allow_null=True)
    changed_by = serializers.CharField(allow_null=True)
    reason = serializers.CharField(allow_blank=True)


class PolicyUpdateSerializer(serializers.Serializer[Any]):
    """بدنهٔ تغییر سیاست — دلیل اجباری است (حداقل ۵ نویسه)."""

    value = serializers.JSONField()
    reason = serializers.CharField(min_length=5, max_length=200)


class PolicyListSerializer(serializers.Serializer[Any]):
    """پوشش پاسخ فهرست سیاست‌ها — ``{"results": [...]}`` (فقط برای سند OpenAPI)."""

    results = PolicyValueSerializer(many=True)


class PolicyRevisionSerializer(serializers.Serializer[Any]):
    """پاسخ انتشار نسخهٔ تازهٔ سیاست (فقط برای سند OpenAPI)."""

    key = serializers.CharField()
    value = serializers.JSONField()
    version = serializers.IntegerField()
    effective_from = serializers.DateTimeField()


class PolicyListView(APIView):
    """فهرست همهٔ سیاست‌ها با مقدار مؤثر، پیش‌فرض و تاریخ آخرین تغییر."""

    permission_classes = [IsAuthenticated, IsStaffRole]

    @extend_schema(
        tags=["سیاست‌ها"],
        summary="فهرست سیاست‌ها (کارکنان)",
        description=(
            "همهٔ کلیدهای سیاست با مقدار مؤثر، پیش‌فرض، نوع، نسخه و آخرین تغییر. "
            "فرانت پنل کارکنان از همین پاسخ، جدول «قواعد اقامت» را می‌سازد."
        ),
        operation_id="staff_policies_list",
        responses={200: PolicyListSerializer, 401: PROBLEM_401, 403: PROBLEM_403},
    )
    def get(self, request: Request) -> Response:  # noqa: ARG002 - قرارداد DRF
        """همهٔ کلیدهای سیاست — از پایگاه داده و پیش‌فرض کد."""
        return Response({"results": _serialize(all_policies())})


class PolicyDetailView(APIView):
    """خواندن یا تغییر یک سیاست خاص (نوشتن: فقط مدیر)."""

    def get_permissions(self) -> list[Any]:
        """خواندن برای کارکنان، تغییر برای مدیر."""
        if self.request.method == "GET":
            return [IsAuthenticated(), IsStaffRole()]
        return [IsAuthenticated(), IsManagerRole()]

    @extend_schema(
        tags=["سیاست‌ها"],
        summary="خواندن یک سیاست (کارکنان)",
        operation_id="staff_policies_retrieve",
        responses={
            200: PolicyValueSerializer,
            401: PROBLEM_401,
            403: PROBLEM_403,
            404: PROBLEM_404,
        },
    )
    def get(self, request: Request, key: str) -> Response:  # noqa: ARG002 - قرارداد DRF
        """مقدار مؤثر یک سیاست."""
        for item in all_policies():
            if item.key == key:
                return Response(_serialize([item])[0])
        raise NotFoundError("سیاست مورد نظر شناخته‌شده نیست.")

    @extend_schema(
        tags=["سیاست‌ها"],
        summary="تغییر یک سیاست (فقط مدیر)",
        description=(
            "نسخهٔ تازهٔ سیاست را منتشر می‌کند. **دلیل اجباری است** و در تاریخچهٔ "
            "حسابرسی ثبت می‌شود: هر تغییر سیاست باید قابل توضیح بماند."
        ),
        operation_id="staff_policies_update",
        request=PolicyUpdateSerializer,
        responses={
            200: PolicyRevisionSerializer,
            401: PROBLEM_401,
            403: PROBLEM_403,
            404: PROBLEM_404,
            422: PROBLEM_422,
        },
    )
    def post(self, request: Request, key: str) -> Response:
        """انتشار نسخهٔ تازهٔ سیاست با دلیل و حسابرسی."""
        payload = PolicyUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        actor = authenticated_user(request)
        if actor is None:  # pragma: no cover - نگهبان: پیش از این‌جا احراز شده است
            raise PermissionDeniedError("برای تغییر سیاست باید وارد شوید.")
        policy = set_policy(
            key=key,
            value=payload.validated_data["value"],
            actor=actor,
            reason=payload.validated_data["reason"],
            request=request,
        )
        return Response(
            {
                "key": policy.key,
                "value": policy.value,
                "version": policy.version,
                "effective_from": policy.effective_from.isoformat(),
            },
            status=200,
        )


def _serialize(items: list[Any]) -> list[dict[str, Any]]:
    """تبدیل مقادیر سیاست به ساختار JSON."""
    return [
        {
            "key": item.key,
            "value": item.value,
            "default": item.default,
            "type": item.type_name,
            "version": item.version,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            "changed_by": item.changed_by,
            "reason": item.reason,
            "label": item.label,
            "help_text": item.help_text,
            "changed": item.changed,
        }
        for item in items
    ]


urlpatterns = [
    path("staff/policies", PolicyListView.as_view(), name="policies"),
    path("staff/policies/<str:key>", PolicyDetailView.as_view(), name="policy-detail"),
]


Serializer = PolicyValueSerializer  #: نام‌گذاری مستندسازی OpenAPI
