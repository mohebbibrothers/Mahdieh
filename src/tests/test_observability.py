"""قرارداد گیرندهٔ گزارش CSP و نگهبانیِ صف Outbox.

دو موضوع که ظاهراً بی‌ربط‌اند و در واقع یک درس دارند: **«بی‌خبری» گران‌ترین
حالت است.** بدون گیرندهٔ CSP نمی‌دانیم چرا صفحه‌ای نصفه کار می‌کند؛ بدون سنجش
کهنگیِ صف، «صف خالی» می‌تواند رلهٔ مرده باشد و کسی نفهمد.
"""

from __future__ import annotations

from datetime import timedelta
import json

from django.core.cache import cache
from django.utils import timezone
import pytest
import time_machine

from apps.core.health import (
    OUTBOX_HEARTBEAT_KEY,
    OUTBOX_MAX_PENDING_AGE_SECONDS,
    _check_outbox,
)

pytestmark = pytest.mark.django_db


# ═════════════════════════════════════════════════════════════════════════════
# گیرندهٔ گزارش CSP
# ═════════════════════════════════════════════════════════════════════════════
def test_csp_report_is_accepted_with_204(client) -> None:  # type: ignore[no-untyped-def]
    """گزارش معتبر پذیرفته می‌شود و ۲۰۴ برمی‌گردد (بدون بدنه، بدون احراز هویت)."""
    payload = {
        "csp-report": {
            "document-uri": "https://mahdie-shohada.ir/units/",
            "violated-directive": "script-src-elem",
            "blocked-uri": "https://evil.example/x.js",
            "disposition": "enforce",
        }
    }
    response = client.post(
        "/csp-report/",
        data=json.dumps(payload),
        content_type="application/csp-report",
    )
    assert response.status_code == 204
    assert response.content == b""


def test_csp_report_accepts_the_reporting_api_shape(client) -> None:  # type: ignore[no-untyped-def]
    """قالب تازهٔ Reporting API (فهرست) هم پذیرفته می‌شود."""
    payload = [
        {
            "type": "csp-violation",
            "body": {
                "effectiveDirective": "img-src",
                "blockedURL": "https://cdn.example/a.png",
            },
        }
    ]
    response = client.post(
        "/csp-report/", data=json.dumps(payload), content_type="application/reports+json"
    )
    assert response.status_code == 204


@pytest.mark.parametrize("body", ["", "not json", "[]", "{", "null"])
def test_csp_report_never_fails_on_garbage(client, body: str) -> None:  # type: ignore[no-untyped-def]
    """بدنهٔ خراب هرگز ۵۰۰ نمی‌سازد: گزارش‌دهنده مرورگر است، نه مهاجم."""
    response = client.post("/csp-report/", data=body, content_type="application/csp-report")
    assert response.status_code == 204


def test_csp_report_rejects_get(client) -> None:  # type: ignore[no-untyped-def]
    """فقط POST پذیرفته می‌شود (GET یعنی کسی نشانی را در مرورگر باز کرده است)."""
    assert client.get("/csp-report/").status_code == 405


def test_csp_report_does_not_require_csrf_token(client) -> None:  # type: ignore[no-untyped-def]
    """گزارش‌دهندهٔ مرورگر توکن CSRF ندارد؛ این مسیر باید معاف باشد."""
    response = client.post(
        "/csp-report/",
        data=json.dumps({"csp-report": {"blocked-uri": "inline"}}),
        content_type="application/json",
    )
    assert response.status_code == 204


def test_csp_report_truncates_huge_bodies(client) -> None:  # type: ignore[no-untyped-def]
    """بدنهٔ بسیار بزرگ خوانده می‌شود ولی به لاگ سرریز نمی‌کند."""
    huge = {"csp-report": {"blocked-uri": "x" * 500_000}}
    response = client.post("/csp-report/", data=json.dumps(huge), content_type="application/json")
    assert response.status_code == 204


def test_csp_header_points_to_our_collector(client, settings) -> None:  # type: ignore[no-untyped-def]
    """هدر CSP باید گزارش را به مسیر خودمان بفرستد (نه به هیچ‌جا)."""
    response = client.get("/")
    csp = response.headers.get("Content-Security-Policy-Report-Only") or response.headers.get(
        "Content-Security-Policy", ""
    )
    assert "/csp-report/" in csp


# ═════════════════════════════════════════════════════════════════════════════
# نگهبانیِ صف Outbox
# ═════════════════════════════════════════════════════════════════════════════
def test_empty_queue_with_live_relay_is_healthy() -> None:
    """صف خالی + ضربان تازه ⇒ سالم."""
    cache.set(OUTBOX_HEARTBEAT_KEY, timezone.now(), timeout=3600)
    result = _check_outbox()
    assert result["status"] == "healthy"
    assert result["pending"] == 0


def test_empty_queue_without_relay_is_degraded() -> None:
    """صف خالی ولی رلهٔ بی‌ضربان ⇒ افت‌کرده؛ نه «سالمِ دروغ».

    این آزمون قلبِ این بخش است: «صف خالی» به‌تنهایی هیچ نمی‌گوید.
    """
    cache.delete(OUTBOX_HEARTBEAT_KEY)
    assert _check_outbox()["status"] == "degraded"


def test_stale_heartbeat_is_degraded() -> None:
    """ضربان کهنه (رلهٔ ایستاده) ⇒ افت‌کرده."""
    cache.set(OUTBOX_HEARTBEAT_KEY, timezone.now() - timedelta(hours=2), timeout=3600)
    result = _check_outbox()
    assert result["status"] == "degraded"
    assert result["relay_heartbeat_seconds"] > 300


def test_old_pending_event_is_degraded() -> None:
    """رخدادِ در انتظارِ کهنه (بیش از ۱۵ دقیقه) ⇒ افت‌کرده، حتی با رلهٔ زنده."""
    from apps.core.outbox import OutboxEvent

    cache.set(OUTBOX_HEARTBEAT_KEY, timezone.now(), timeout=3600)
    with time_machine.travel(
        timezone.now() - timedelta(seconds=OUTBOX_MAX_PENDING_AGE_SECONDS + 60), tick=False
    ):
        OutboxEvent.objects.create(
            event_type="test.old",
            aggregate_type="test",
            aggregate_id="1",
            payload={},
        )
    result = _check_outbox()
    assert result["status"] == "degraded"
    assert result["oldest_pending_seconds"] > OUTBOX_MAX_PENDING_AGE_SECONDS


def test_dead_event_is_unhealthy() -> None:
    """رخدادِ مرده ⇒ ناسالم (پردازش قطعی شکست خورده و کسی باید سراغش برود)."""
    from apps.core.choices import OutboxStatus
    from apps.core.outbox import OutboxEvent

    cache.set(OUTBOX_HEARTBEAT_KEY, timezone.now(), timeout=3600)
    OutboxEvent.objects.create(
        event_type="test.dead",
        aggregate_type="test",
        aggregate_id="2",
        payload={},
        status=OutboxStatus.DEAD,
    )
    assert _check_outbox()["status"] == "unhealthy"


def test_relay_cycle_writes_a_heartbeat() -> None:
    """هر چرخهٔ رله، ضربان تازه می‌نویسد — تنها شاهدِ «رله زنده است»."""
    from django.core.management import call_command

    cache.delete(OUTBOX_HEARTBEAT_KEY)
    call_command("outbox_relay", "--once")
    heartbeat = cache.get(OUTBOX_HEARTBEAT_KEY)
    assert heartbeat is not None, "رله باید پس از هر چرخه ضربان بگذارد."
    assert (timezone.now() - heartbeat).total_seconds() < 60
