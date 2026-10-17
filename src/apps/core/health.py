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


#: کلید ضربان رلهٔ Outbox. رله هر چرخه آن را تازه می‌کند و ``health`` سنش را
#: می‌خواند. «صف خالی» می‌تواند یعنی «همه‌چیز خوب» یا «رله مرده» — این کلید
#: تفاوت این دو را می‌گوید.
OUTBOX_HEARTBEAT_KEY = "mahdieh:outbox:heartbeat"
#: سقف کهنگی رخدادِ در انتظار: ۱۵ دقیقه (بیشتر از این، یعنی رله یا مصرف‌کننده
#: ایستاده است). عدد از تجربهٔ واقعی: چرخهٔ رله هر ۲ ثانیه است؛ ۴۵۰ برابر آن،
#: حاشیهٔ سخاوتمندانه‌ای است که هشدارِ الکی نمی‌سازد.
OUTBOX_MAX_PENDING_AGE_SECONDS = 900
#: اگر ضربان رله از این بیشتر کهنه باشد، صف «بی‌نگهبان» است.
OUTBOX_HEARTBEAT_MAX_AGE_SECONDS = 300


def _check_outbox() -> dict[str, Any]:
    """عمق، کهنگی و نگهبانیِ صف Outbox — نه فقط «چند تا در صف است؟».

    چرا کهنگی مهم‌تر از شمار است؟ صفِ صفر با رلهٔ مرده و صفِ صفر سالم، از بیرون
    یکسان‌اند؛ ولی یکی فاجعه است و دیگری آرامش. پس دو چیز جدا سنجیده می‌شود:
    «قدیمی‌ترین رخدادِ در انتظار» و «آخرین ضربانِ رله».
    """
    from django.utils import timezone as django_timezone

    from apps.core.choices import OutboxStatus
    from apps.core.outbox import OutboxEvent

    try:
        pending_qs = OutboxEvent.all_objects.filter(status=OutboxStatus.PENDING)
        pending = pending_qs.count()
        dead = OutboxEvent.all_objects.filter(status=OutboxStatus.DEAD).count()
        oldest = pending_qs.order_by("occurred_at").values_list("occurred_at", flat=True).first()
        age_seconds = (
            int((django_timezone.now() - oldest).total_seconds()) if oldest is not None else 0
        )

        result: dict[str, Any] = {
            "status": _HEALTHY,
            "pending": pending,
            "dead": dead,
            "oldest_pending_seconds": age_seconds,
        }

        heartbeat = cache.get(OUTBOX_HEARTBEAT_KEY)
        if heartbeat is None:
            result["relay_heartbeat_seconds"] = None
        else:
            result["relay_heartbeat_seconds"] = int(
                (django_timezone.now() - heartbeat).total_seconds()
            )

        if dead:
            # رخداد مرده یعنی پردازشِ آن قطعی شکست خورده؛ باید کسی سراغش برود.
            result["status"] = _UNHEALTHY
        elif age_seconds > OUTBOX_MAX_PENDING_AGE_SECONDS:
            result["status"] = _DEGRADED
        elif result["relay_heartbeat_seconds"] is None:
            # هنوز هیچ چرخه‌ای اجرا نشده یا رله اصلاً بالا نیامده است.
            result["status"] = _DEGRADED
        elif result["relay_heartbeat_seconds"] > OUTBOX_HEARTBEAT_MAX_AGE_SECONDS:
            result["status"] = _DEGRADED
        return result
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
