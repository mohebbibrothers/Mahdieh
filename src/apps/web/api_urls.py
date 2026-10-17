"""مسیرهای API لایهٔ وب (محتوای راهنما) — زیر ``/api/v1/``."""

from __future__ import annotations

from django.urls import path

from apps.web import api_views

urlpatterns = [
    path("help", api_views.HelpView.as_view(), name="help"),
]
