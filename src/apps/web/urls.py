"""مسیرهای وب سایت مهدیه — فضا‌ی‌نام ``web``.

قرارداد: صفحات عمومی بدون پیشوند، صفحات خصوصی زیر ``my``، پنل کارکنان زیر
``board``. مسیرها با اسلش پایانی بسته می‌شوند تا پیوندها پایدار بمانند.
"""

from __future__ import annotations

from django.conf import settings
from django.urls import path

from apps.web import views, views_staff

app_name = "web"

urlpatterns = [
    # ── عمومی ────────────────────────────────────────────────────────────────
    path("", views.HomeView.as_view(), name="home"),
    path("units/", views.UnitListView.as_view(), name="units"),
    path("units/<str:code>/", views.UnitDetailView.as_view(), name="unit-detail"),
    path("calendar/", views.CalendarView.as_view(), name="calendar"),
    path("calendar/grid/", views.CalendarGridPartialView.as_view(), name="calendar-grid"),
    # ── احراز هویت با تلفن ───────────────────────────────────────────────────
    path("login/", views.LoginView.as_view(), name="login"),
    path("login/verify/", views.LoginVerifyView.as_view(), name="login-verify"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    # ── ناحیهٔ زائر ──────────────────────────────────────────────────────────
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("my/", views.MyReservationsView.as_view(), name="my_reservations"),
    path("book/<str:code>/", views.BookView.as_view(), name="book"),
    path("book/<str:code>/quote/", views.QuotePartialView.as_view(), name="book-quote"),
    path(
        "reservations/<str:code>/",
        views.ReservationDetailView.as_view(),
        name="reservation_detail",
    ),
    path(
        "reservations/<str:code>/certificate/",
        views.ReservationCertificateView.as_view(),
        name="reservation_certificate",
    ),
    path(
        "reservations/<str:code>/cancel/",
        views.CancelReservationView.as_view(),
        name="reservation_cancel",
    ),
    # ── پنل کارکنان ──────────────────────────────────────────────────────────
    path("board/", views_staff.BoardView.as_view(), name="board"),
    path("board/table/", views_staff.BoardPartialView.as_view(), name="board-table"),
    path(
        "board/reservations/<str:code>/<str:action>/",
        views_staff.BoardActionView.as_view(),
        name="board-action",
    ),
    path("board/blocks/", views_staff.BlockCreateView.as_view(), name="board-block-create"),
    path(
        "board/blocks/<uuid:block_id>/remove/",
        views_staff.BlockRemoveView.as_view(),
        name="board-block-remove",
    ),
]

# ── مسیر نمایشی (فقط توسعه) ──────────────────────────────────────────────────
# وجود خودِ مسیر هم مشروط است: در تولید، حتی یک ۴۰۴ «مسیر پنهان» می‌سازد که
# نمی‌خواهیم. پس در محیط غیرتوسعه، این مسیر اصلاً ثبت نمی‌شود.
if settings.DEBUG and getattr(settings, "DEMO_MODE", False):
    from apps.web import views_dev

    urlpatterns.append(
        path("demo/<str:role>/", views_dev.DemoLoginView.as_view(), name="demo-login")
    )
