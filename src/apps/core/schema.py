"""کمکی‌های OpenAPI — قرارداد خطای یکسان برای همهٔ نقاط پایانی.

تصمیم ADR-0006: خطا همیشه ``application/problem+json`` (RFC 9457) است. این
ماژول آن قرارداد را به سند OpenAPI تزریق می‌کند تا مصرف‌کنندهٔ API (فرانت،
اپ موبایل، پنل کارکنان) دقیقاً بداند با چه شکلی از خطا روبه‌رو می‌شود.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import OpenApiExample, OpenApiResponse

PROBLEM_CONTENT_TYPE = "application/problem+json"

PROBLEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["type", "title", "status", "detail", "code"],
    "properties": {
        "type": {"type": "string", "format": "uri"},
        "title": {"type": "string"},
        "status": {"type": "integer"},
        "detail": {"type": "string"},
        "code": {"type": "string"},
        "instance": {"type": "string"},
        "trace_id": {"type": "string"},
        "errors": {"type": "object", "additionalProperties": True},
    },
}


def problem_response(
    description: str, *, example_code: str, example_detail: str, status: int
) -> OpenApiResponse:
    """ساخت پاسخ خطای مستندشده با نمونهٔ واقعی بدنه."""
    return OpenApiResponse(
        description=description,
        response=PROBLEM_SCHEMA,
        examples=[
            OpenApiExample(
                "نمونهٔ بدنهٔ خطا",
                value={
                    "type": f"https://docs.mahdieh.ir/problems/{example_code}",
                    "title": description,
                    "status": status,
                    "detail": example_detail,
                    "code": example_code,
                    "trace_id": "01HQ2Z4N6T9K8M7P5R3S1V0X2Y",
                },
                response_only=True,
            )
        ],
    )


PROBLEM_401 = problem_response(
    "ورود لازم است",
    example_code="authentication_required",
    example_detail="برای این عملیات ابتدا وارد شوید.",
    status=401,
)
PROBLEM_403 = problem_response(
    "دسترسی مجاز نیست",
    example_code="permission_denied",
    example_detail="اجازهٔ انجام این عملیات را ندارید.",
    status=403,
)
PROBLEM_404 = problem_response(
    "یافت نشد",
    example_code="not_found",
    example_detail="منبع درخواستی وجود ندارد یا حذف شده است.",
    status=404,
)
PROBLEM_409 = problem_response(
    "تصادم با وضعیت موجود",
    example_code="conflict",
    example_detail="این عملیات با وضعیت فعلی سیستم سازگار نیست.",
    status=409,
)
PROBLEM_422 = problem_response(
    "ورودی نامعتبر",
    example_code="validation_error",
    example_detail="اطلاعات ارسالی کامل یا درست نیست.",
    status=422,
)
PROBLEM_429 = problem_response(
    "تعداد درخواست زیاد",
    example_code="rate_limited",
    example_detail="کمی بعد دوباره تلاش کنید.",
    status=429,
)
