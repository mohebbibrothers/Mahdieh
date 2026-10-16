"""مسیرهای API نسخهٔ ۱ — نقطهٔ تجمیع همهٔ ماژول‌ها زیر ``/api/v1/``.

قرارداد: نام مسیرها Flat و در فضای‌نام ``api-v1`` است (مثل ``api-v1:units``). هر
ماژول فایل مسیرهای خودش را دارد و اینجا فقط وصل می‌شود — هیچ منطقی اینجا نیست.
"""

from __future__ import annotations

from django.urls import include, path

from apps.core import views

app_name = "api-v1"

urlpatterns = [
    path("meta", views.meta, name="meta"),
    path("", include("apps.identity.api_urls")),
    path("", include("apps.inventory.api_urls")),
    path("", include("apps.reservation.api_urls")),
]
