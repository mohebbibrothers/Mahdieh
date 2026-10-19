"""تست بررسی‌های سلامت — زنده، آماده، و کامل."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_liveness_is_lightweight(client) -> None:  # type: ignore[no-untyped-def]
    """بررسی زنده‌بودن باید سبک و مستقل از دیتابیس باشد."""
    response = client.get("/health/live/")
    assert response.status_code == 200
    assert json.loads(response.content)["status"] == "alive"


def test_readiness_reports_components(client) -> None:  # type: ignore[no-untyped-def]
    """بررسی آمادگی باید وضعیت دیتابیس، کش و مهاجرت‌ها را گزارش کند."""
    response = client.get("/health/ready/")
    assert response.status_code == 200, response.content
    body = json.loads(response.content)
    assert body["status"] == "healthy"
    assert set(body["checks"]) == {"database", "cache", "migrations"}
    assert body["checks"]["database"]["status"] == "healthy"
    assert body["checks"]["migrations"]["pending"] == 0


def test_detailed_requires_staff_when_not_public(client, settings) -> None:  # type: ignore[no-untyped-def]
    """گزارش کامل نباید برای کاربر عادی قابل دسترس باشد."""
    settings.HEALTH_DETAILED_PUBLIC = False
    response = client.get("/health/detailed/")
    assert response.status_code == 403


def test_detailed_returns_outbox_and_storage_for_staff(client, settings, django_user_model) -> None:  # type: ignore[no-untyped-def]
    """برای کارمند، جزئیات عملیاتی (از جمله عمق Outbox) نمایش داده شود."""
    settings.HEALTH_DETAILED_PUBLIC = False
    staff = django_user_model.objects.create_user(
        phone="09120000000", password="x-safe-password-123", is_staff=True
    )
    client.force_login(staff)
    response = client.get("/health/detailed/")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert "outbox" in body["checks"]
    assert "storage" in body["checks"]
    assert body["checks"]["outbox"]["dead"] == 0


def test_liveness_reports_running_release(client) -> None:  # type: ignore[no-untyped-def]
    """استقرار اتمی با این شناسه ثابت می‌کند فرآیندِ پاسخ‌دهنده نسخهٔ تازه است."""
    from django.conf import settings

    body = json.loads(client.get("/health/live/").content)
    assert body["release"] == settings.RELEASE


def test_release_id_prefers_env_then_bundle_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config.settings import base

    monkeypatch.setattr(base, "BASE_DIR", tmp_path / "src")
    monkeypatch.delenv("MAHDIEH_RELEASE", raising=False)
    assert base._running_release() == "dev"
    (tmp_path / "BUILD_INFO.json").write_text('{"release": "20261019-abc1234"}', encoding="utf-8")
    assert base._running_release() == "20261019-abc1234"
    (tmp_path / "BUILD_INFO.json").write_text("{not json", encoding="utf-8")
    assert base._running_release() == "dev"
    monkeypatch.setenv("MAHDIEH_RELEASE", "explicit-1")
    assert base._running_release() == "explicit-1"
