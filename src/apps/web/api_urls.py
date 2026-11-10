"""مسیرهای API لایهٔ وب (راهنما، بازخورد پاسخ‌ها، اوقات شرعی) — زیر ``/api/v1/``."""

from __future__ import annotations

from django.urls import path

from apps.web import api_views

urlpatterns = [
    path("help", api_views.HelpView.as_view(), name="help"),
    path("help/feedback", api_views.HelpFeedbackView.as_view(), name="help-feedback"),
    path("prayer-times", api_views.PrayerTimesView.as_view(), name="prayer-times"),
]
