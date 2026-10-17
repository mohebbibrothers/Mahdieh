"""مسیرهای API هویت — زیر ``/api/v1/`` و در فضای‌نام ``api-v1``.

تصمیم: این ماژول ``app_name`` جدا ندارد و نام‌ها را در فضای‌نام مشترک
``api-v1`` می‌نویسد؛ نتیجه: ``reverse("api-v1:me")`` — کوتاه، بدون تودرتویی
فضای‌نام، و همچنان تفکیک‌شده در سطح ماژول.
"""

from __future__ import annotations

from django.urls import path

from apps.identity import views, views_security

urlpatterns = [
    path("auth/otp/request", views.OtpRequestView.as_view(), name="auth-otp-request"),
    path("auth/otp/verify", views.OtpVerifyView.as_view(), name="auth-otp-verify"),
    path("auth/logout", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/logout-all", views_security.LogoutAllView.as_view(), name="auth-logout-all"),
    path("auth/csrf", views_security.CsrfView.as_view(), name="auth-csrf"),
    # تأیید دومرحله‌ای کارکنان (ADR-0018)
    path("auth/mfa/status", views_security.MfaStatusView.as_view(), name="auth-mfa-status"),
    path("auth/mfa/totp/setup", views_security.MfaSetupView.as_view(), name="auth-mfa-setup"),
    path("auth/mfa/totp/confirm", views_security.MfaConfirmView.as_view(), name="auth-mfa-confirm"),
    path("auth/mfa/verify", views_security.MfaVerifyView.as_view(), name="auth-mfa-verify"),
    path(
        "auth/mfa/recovery-codes/regenerate",
        views_security.MfaRecoveryCodesView.as_view(),
        name="auth-mfa-recovery-codes",
    ),
    path("me", views.MeView.as_view(), name="me"),
    path("me/profile", views.ProfileUpdateView.as_view(), name="me-profile"),
    path("me/sessions", views_security.SessionListView.as_view(), name="me-sessions"),
    path(
        "me/sessions/<uuid:session_id>/revoke",
        views_security.SessionRevokeView.as_view(),
        name="me-session-revoke",
    ),
]
