"""مسیرهای سلامت — بدون احراز هویت برای live/ready (استاندارد لودبالانسر)."""

from __future__ import annotations

from django.urls import path

from apps.core import health

app_name = "health"

urlpatterns = [
    path("live/", health.liveness, name="live"),
    path("ready/", health.readiness, name="ready"),
    path("detailed/", health.detailed, name="detailed"),
]
