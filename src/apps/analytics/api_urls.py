"""مسیرهای API تحلیل‌ها — زیر ``/api/v1/``.

قرارداد بقیهٔ مسیرها اینجا هم برقرار است: نام‌ها Flat و در فضای‌نام ``api-v1``.
همهٔ این نقاط فقط‌خواندنی و مخصوص نقش‌های کارکنان‌اند (نگهبانی در خود نما).
"""

from __future__ import annotations

from django.urls import path

from apps.analytics import views

urlpatterns = [
    path("staff/insights", views.InsightsView.as_view(), name="insights"),
]
