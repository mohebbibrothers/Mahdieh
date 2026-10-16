"""مسیرهای وب سایت مهدیه — فضا‌ی‌نام ``web``.

قرارداد: صفحات عمومی بدون پیشوند، صفحات خصوصی زیر ``my``، پنل کارکنان زیر
``board``. مسیرها با اسلش پایانی بسته می‌شوند تا پیوندها پایدار بمانند.
"""

from __future__ import annotations

from django.conf import settings
from django.urls import path

from apps.web import (
    views,
    views_calendar,
    views_feedback,
    views_insights,
    views_staff,
    views_waitlist,
)

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
        "reservations/<str:code>/calendar.ics",
        views_calendar.ReservationCalendarView.as_view(),
        name="reservation_calendar",
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
        "board/calendar.ics",
        views_calendar.StaffCalendarView.as_view(),
        name="board-calendar",
    ),
    path(
        "board/feedback/<str:code>/",
        views_feedback.FeedbackRequestView.as_view(),
        name="board-feedback-request",
    ),
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
    # ── بینش و حاکمیت (کارکنان) ──────────────────────────────────────────────
    path("board/insights/", views_insights.InsightsView.as_view(), name="board-insights"),
    path("board/waitlist/", views_insights.WaitlistBoardView.as_view(), name="board-waitlist"),
    path(
        "board/waitlist/<uuid:entry_id>/withdraw/",
        views_insights.WaitlistWithdrawStaffView.as_view(),
        name="board-waitlist-withdraw",
    ),
    path("board/policies/", views_insights.PolicyBoardView.as_view(), name="board-policies"),
    path(
        "board/policies/apply/",
        views_insights.PolicyUpdateView.as_view(),
        name="board-policy-update",
    ),
    # ── صف انتظار (زائر) ─────────────────────────────────────────────────────
    path("my/calendar.ics", views_calendar.MyCalendarView.as_view(), name="my_calendar"),
    path("my/waitlist/", views_waitlist.MyWaitlistView.as_view(), name="my_waitlist"),
    path("my/waitlist/join/", views_waitlist.JoinWaitlistView.as_view(), name="waitlist-join"),
    path(
        "my/waitlist/<uuid:entry_id>/withdraw/",
        views_waitlist.WithdrawWaitlistView.as_view(),
        name="waitlist-withdraw",
    ),
    path(
        "offer/<str:token>/<str:action>/",
        views_waitlist.OfferRespondView.as_view(),
        name="waitlist-offer",
    ),
    # ── نظرسنجی پس از اقامت (توکن‌محور، بدون ورود) ──────────────────────────
    path("feedback/<str:token>/", views_feedback.FeedbackView.as_view(), name="feedback"),
    # ── تأیید گواهی (عمومی، بدون ورود) ──────────────────────────────────────
    path(
        "verify/", views_insights.CertificateOfflineCheckView.as_view(), name="certificate-offline"
    ),
    path(
        "verify/<str:token>/",
        views_insights.CertificateVerifyView.as_view(),
        name="certificate-verify",
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
