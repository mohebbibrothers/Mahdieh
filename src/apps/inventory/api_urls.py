"""مسیرهای API موجودی — زیر ``/api/v1/`` و در فضای‌نام ``api-v1``."""

from __future__ import annotations

from django.urls import path

from apps.inventory import views

urlpatterns = [
    path("units", views.UnitListView.as_view(), name="units"),
    path("units/<str:code>", views.UnitDetailView.as_view(), name="unit-detail"),
    path("property", views.PropertyView.as_view(), name="property"),
    path("occasions", views.OccasionListView.as_view(), name="occasions"),
    path("blocks", views.BlockListCreateView.as_view(), name="blocks"),
    path("blocks/<uuid:block_id>/remove", views.BlockRemoveView.as_view(), name="block-remove"),
]
