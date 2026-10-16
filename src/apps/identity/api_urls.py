"""مسیرهای API هویت — زیر ``/api/v1/`` و در فضای‌نام ``api-v1``.

تصمیم: این ماژول ``app_name`` جدا ندارد و نام‌ها را در فضای‌نام مشترک
``api-v1`` می‌نویسد؛ نتیجه: ``reverse("api-v1:me")`` — کوتاه، بدون تودرتویی
فضای‌نام، و همچنان تفکیک‌شده در سطح ماژول.
"""

from __future__ import annotations

from django.urls import path

from apps.identity import views

urlpatterns = [
    path("auth/otp/request", views.OtpRequestView.as_view(), name="auth-otp-request"),
    path("auth/otp/verify", views.OtpVerifyView.as_view(), name="auth-otp-verify"),
    path("auth/logout", views.LogoutView.as_view(), name="auth-logout"),
    path("me", views.MeView.as_view(), name="me"),
    path("me/profile", views.ProfileUpdateView.as_view(), name="me-profile"),
]
