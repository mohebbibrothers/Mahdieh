"""مسیرهای سطح سایت (غیر API)."""

from __future__ import annotations

from django.urls import path

from apps.core import metrics, views
from apps.core.csp import csp_report

app_name = "core"

urlpatterns = [
    path("robots.txt", views.robots, name="robots"),
    path("metrics", metrics.metrics_view, name="metrics"),
    # مرورگرها گزارش نقض CSP را به همین مسیر می‌فرستند (بدون احراز هویت).
    path("csp-report/", csp_report, name="csp-report"),
]
