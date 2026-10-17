"""پیکربندی اپ هویت."""

from __future__ import annotations

from typing import Any

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class IdentityConfig(AppConfig):
    """هویت و دسترسی — تنها مالک مدل کاربر."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.identity"
    verbose_name = _("هویت و دسترسی")
    label = "identity"

    def ready(self) -> None:
        """اتصال ثبت نشست به سیگنال‌های ورود/خروج جنگو (همهٔ مسیرها: API، وب، پنل)."""
        from django.contrib.auth.signals import user_logged_in, user_logged_out

        user_logged_in.connect(_on_login, dispatch_uid="identity.sessions.login")
        user_logged_out.connect(_on_logout, dispatch_uid="identity.sessions.logout")


def _on_login(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    """ثبت نشست تازه پس از هر ورود موفق."""
    del sender, kwargs
    if request is None:
        return
    from apps.identity import sessions

    sessions.register(request, user)


def _on_logout(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    """مهر پایان نشست هنگام خروج."""
    del sender, user, kwargs
    session = getattr(request, "session", None)
    if session is None:
        return
    from apps.identity import sessions

    sessions.end(session.session_key)
