"""مسیرهای API گزارش‌گیری — زیر ``/api/v1/`` و در فضای‌نام ``api-v1``."""

from __future__ import annotations

from django.urls import path

from apps.reporting import views

urlpatterns = [
    path(
        "staff/reports/reservations.xlsx",
        views.ReservationReportView.as_view(),
        name="staff-report-reservations",
    ),
    path("staff/reports/summary", views.ReportSummaryView.as_view(), name="staff-report-summary"),
]
