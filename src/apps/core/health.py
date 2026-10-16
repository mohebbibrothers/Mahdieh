"""بررسی‌های سلامت — زنده‌بودن، آمادگی، و جزئیات عملیاتی.

سه سطح، عمداً جدا:
    • ``live``  — فرآیند زنده است؟ (سبک؛ هرگز به سرویس بیرونی دست نمی‌زند)
    • ``ready`` — می‌تواند ترافیک بپذیرد؟ (DB + کش + وضعیت مهاجرت‌ها)
    • ``detailed`` — وضعیت عملیاتی کامل (عمق صف، ذخیره‌سازی، ارائه‌دهنده‌ها)

خطای آمادگی باید **۵۰۳** بدهد تا لودبالانسر ترافیک را جای دیگر بفرستد.
"""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache

_HEALTHY = "healthy"
_DEGRADED = "degraded"
_UNHEALTHY = "unhealthy"


def _check_database() -> dict[str, Any]:
    """اتصال و پاسخ‌دهی دیتابیس."""
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {
            "status": _HEALTHY,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:
        return {"status": _UNHEALTHY, "error": type(exc).__name__}


def _check_cache() -> dict[str, Any]:
    """خواندن/نوشتن کش."""
    started = time.perf_counter()
    try:
        cache.set("health:probe", "ok", 10)
        value = cache.get("health:probe")
        return {
            "status": _HEALTHY if value == "ok" else _DEGRADED,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:
        return {"status": _DEGRADED, "error": type(exc).__name__}


def _check_migrations() -> dict[str, Any]:
    """آیا مهاجرت اجرانشده باقی مانده است؟ (استقرار نیمه‌کاره = خطر)"""
    try:
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)
        if plan:
            return {"status": _UNHEALTHY, "pending": len(plan)}
        return {"status": _HEALTHY, "pending": 0}
    except Exception as exc:
        return {"status": _DEGRADED, "error": type(exc).__name__}


def _check_outbox() -> dict[str, Any]:
    """عمق صف Outbox — نشانهٔ سلامت انتشار رخدادها."""
    from apps.core.choices import OutboxStatus
    from apps.core.outbox import OutboxEvent

    try:
        pending = OutboxEvent.all_objects.filter(status=OutboxStatus.PENDING).count()
        dead = OutboxEvent.all_objects.filter(status=OutboxStatus.DEAD).count()
        status = _HEALTHY
        if dead:
            status = _UNHEALTHY
        elif pending > 1000:  # noqa: PLR2004
            status = _DEGRADED
        return {"status": status, "pending": pending, "dead": dead}
    except Exception as exc:
        return {"status": _DEGRADED, "error": type(exc).__name__}


def _check_storage() -> dict[str, Any]:
    """قابل‌نوشتن بودن ریشهٔ رسانه (فقط حالت محلی)."""
    if settings.STORAGE_BACKEND != "local":
        return {"status": _HEALTHY, "backend": settings.STORAGE_BACKEND}
    try:
        root = settings.MEDIA_ROOT
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".health-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"status": _HEALTHY, "backend": "local"}
    except Exception as exc:
        return {"status": _DEGRADED, "error": type(exc).__name__}


def _check_sms() -> dict[str, Any]:
    """وضعیت ارائه‌دهندهٔ پیامک (فقط پیکربندی؛ تماس شبکه‌ای نمی‌گیریم)."""
    if not settings.SMS_ENABLED:
        return {"status": _HEALTHY, "provider": "disabled"}
    configured = bool(settings.SMS_API_KEY and settings.SMS_SENDER_NUMBER)
    return {
        "status": _HEALTHY if configured else _DEGRADED,
        "provider": settings.SMS_PROVIDER,
        "configured": configured,
    }


@never_cache
def liveness(_request: HttpRequest) -> JsonResponse:
    """بررسی زنده‌بودن فرآیند — سبک و بدون وابستگی."""
    return JsonResponse({"status": "alive", "service": settings.OTEL_SERVICE_NAME})


def readiness_payload() -> tuple[dict[str, Any], int]:
    """گزارش آمادگی + کد وضعیت — **یک منبع حقیقت** برای نما و پایشگر.

    چرا جدا شد؟ چون «پایشگر» (``manage.py healthcheck``) و «نمای آمادگی» باید
    دقیقاً یک داوری داشته باشند. اگر پایشگر معیار خودش را داشته باشد، روزی
    می‌رسد که سایت سالم است و پایشگر فریاد می‌زند (یا برعکس).
    """
    checks: dict[str, dict[str, Any]] = {
        "database": _check_database(),
        "cache": _check_cache(),
        "migrations": _check_migrations(),
    }
    failed = [name for name, result in checks.items() if result["status"] == _UNHEALTHY]
    degraded = [name for name, result in checks.items() if result["status"] == _DEGRADED]
    status = _UNHEALTHY if failed else (_DEGRADED if degraded else _HEALTHY)
    payload = {
        "status": status,
        "checks": checks,
        "failed": failed,
        "degraded": degraded,
    }
    return payload, (503 if failed else 200)


@never_cache
def readiness(_request: HttpRequest) -> JsonResponse:
    """بررسی آمادگی پذیرش ترافیک.

    سه حالت، سه معنا:
      • ``healthy``   ⇒ همه‌چیز سالم؛ ترافیک بفرست.
      • ``degraded``  ⇒ وابستگی جانبی افت کرده (مثل کش) ولی سامانه پاسخ‌گو
        است؛ ترافیک بفرست، **ولی هشدار پایش را روشن کن**. کد وضعیت ۲۰۰ است تا
        سدِ بار، سامانهٔ سالم را از چرخه بیرون نگذارد.
      • ``unhealthy`` ⇒ پایگاه‌داده أو مهاجرت معلق؛ ترافیک نفرست (۵۰۳).
    """
    payload, status_code = readiness_payload()
    return JsonResponse(payload, status=status_code)


@never_cache
def detailed(request: HttpRequest) -> JsonResponse:
    """گزارش عملیاتی کامل — محافظت‌شده در تولید."""
    if not settings.HEALTH_DETAILED_PUBLIC:
        user = getattr(request, "user", None)
        if not (user is not None and user.is_authenticated and user.is_staff):
            return JsonResponse({"detail": "دسترسی مجاز نیست."}, status=403)

    checks: dict[str, Callable[[], dict[str, Any]]] = {
        "database": _check_database,
        "cache": _check_cache,
        "migrations": _check_migrations,
        "outbox": _check_outbox,
        "storage": _check_storage,
        "sms": _check_sms,
    }
    results = {name: check() for name, check in checks.items()}
    statuses = {result["status"] for result in results.values()}
    overall = (
        _UNHEALTHY if _UNHEALTHY in statuses else (_DEGRADED if _DEGRADED in statuses else _HEALTHY)
    )
    return JsonResponse(
        {"status": overall, "environment": settings.ENVIRONMENT, "checks": results},
        status=503 if overall == _UNHEALTHY else 200,
    )
