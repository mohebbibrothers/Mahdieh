"""مسیرهای سطح سایت (غیر API)."""

from __future__ import annotations

from django.urls import path

from apps.core import metrics, views

app_name = "core"

urlpatterns = [
    path("robots.txt", views.robots, name="robots"),
    path("metrics", metrics.metrics_view, name="metrics"),
]
