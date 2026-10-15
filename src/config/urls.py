"""مسیرهای ریشهٔ پروژه — همهٔ مسیرهای نسخه‌دار زیر /api/v1 جمع می‌شوند."""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("", include("apps.core.urls")),
    path("health/", include("apps.core.health_urls")),
    path("django-admin/", admin.site.urls),
    path("api/v1/", include("apps.core.api_urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="openapi-schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="openapi-schema"),
        name="api-docs",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
