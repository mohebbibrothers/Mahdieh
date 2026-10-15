"""مسیرهای API نسخهٔ ۱ — در فازهای بعدی ماژول‌ها اینجا وصل می‌شوند."""

from __future__ import annotations

from django.urls import path

from apps.core import views

app_name = "api-v1"

urlpatterns = [
    path("meta", views.meta, name="meta"),
]
