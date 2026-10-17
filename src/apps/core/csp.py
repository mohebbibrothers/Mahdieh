"""گیرندهٔ گزارش‌های نقض CSP — گوشِ سامانه برای «چه چیزی مسدود شد و چرا».

مسئله: در تولید، سیاست CSP **اعمال‌شده** است. بدون گیرندهٔ گزارش، دو حالت بد
پیش می‌آید: یا کسی نمی‌فهمد چرا بخشی از صفحه کار نمی‌کند، یا تیم برای «احتیاط»
CSP را خاموش می‌کند — و همان محافظت از دست می‌رود.

قرارداد این نما (عمداً ساده و ضدسوءاستفاده):

* **همیشه ۲۰۴** برمی‌گرداند، حتی برای بدنهٔ خراب. گزارش‌دهنده مرورگر است؛ خطای
  ما نباید به خطای کنسول کاربر تبدیل شود. خرابی، فقط در لاگ دیده می‌شود.
* **بدون احراز هویت و بدون CSRF** پذیرفته می‌شود (مرورگر توکن ندارد)، ولی
  اندازهٔ بدنه سقف دارد و محتوا **بازتاب داده نمی‌شود**.
* فقط چند فیلد لازم ثبت می‌شود؛ نشانی کامل صفحه (که ممکن است شناسهٔ زائر داشته
  باشد) نگه داشته نمی‌شود — خودِ گزارش ممکن است دادهٔ شخصی داشته باشد.
"""

from __future__ import annotations

import json
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import structlog

logger = structlog.get_logger(__name__)

#: سقف بدنهٔ گزارش. مرورگرها گزارش چند صد بایتی می‌فرستند؛ بیشتر از این یعنی
#: یا خراب است یا سوءاستفاده — در هر دو حالت خواندنش بی‌فایده است.
MAX_REPORT_BYTES = 16_384

#: فیلدهایی که ثبت می‌شوند. «document-uri» عمداً فقط دامنه‌اش نگه داشته می‌شود.
LOGGED_FIELDS: tuple[str, ...] = (
    "violated-directive",
    "effective-directive",
    "blocked-uri",
    "source-file",
    "line-number",
    "disposition",
)


def _first_report(payload: Any) -> dict[str, Any]:
    """استخراج نخستین گزارش از هر یک از سه قالب رایج.

    * ``application/csp-report`` ⇒ ``{"csp-report": {...}}``
    * Reporting API ⇒ ``[{"type": "csp-violation", "body": {...}}]``
    * گزارش سادهٔ JSON ⇒ خودِ دیکشنری
    """
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            body = first.get("body", first)
            return body if isinstance(body, dict) else {}
    if isinstance(payload, dict):
        nested = payload.get("csp-report")
        if isinstance(nested, dict):
            return nested
        return payload
    return {}


@csrf_exempt
@require_POST
def csp_report(request: HttpRequest) -> HttpResponse:
    """دریافت گزارش نقض CSP و ثبت فشردهٔ آن (همیشه ۲۰۴)."""
    try:
        raw = request.body[:MAX_REPORT_BYTES]
        payload: Any = json.loads(raw.decode("utf-8", errors="replace"))
        report = _first_report(payload)
        blocked = str(report.get("blocked-uri", ""))[:200]
        # نشانی کامل صفحه ثبت نمی‌شود؛ فقط اینکه گزارش از کدام مسیر آمده است.
        logger.info(
            "csp_violation",
            path=request.path,
            blocked_uri=blocked,
            **{
                field.replace("-", "_"): str(report.get(field, ""))[:200]
                for field in LOGGED_FIELDS
                if report.get(field) is not None
            },
        )
    except (ValueError, UnicodeDecodeError, AttributeError, TypeError):
        # بدنهٔ خراب یا خالی: نه خطا، نه لاگ پرحجم. گزارش‌دهنده حرفش را از دست
        # می‌دهد، ولی سامانه سالم می‌ماند و کسی هم اسپم نمی‌گیرد.
        logger.debug("csp_report_unparseable", path=getattr(request, "path", ""))
    return HttpResponse(status=204)
