"""مسیرهای API رزرو — زیر ``/api/v1/`` و در فضای‌نام ``api-v1``."""

from __future__ import annotations

from django.urls import path

from apps.reservation import views, views_config, views_guest, views_staff_guests, views_windows

urlpatterns = [
    # پیکربندی فرانت و تقویم عمومی
    path("config", views_config.ConfigView.as_view(), name="config"),
    path("calendar", views.CalendarView.as_view(), name="calendar"),
    path("calendar/windows", views_windows.CalendarWindowsView.as_view(), name="calendar-windows"),
    # مسیر زائر
    path("reservations/quote", views.QuoteView.as_view(), name="reservation-quote"),
    path("reservations", views.ReservationListCreateView.as_view(), name="reservations"),
    path(
        "reservations/<str:code>", views.ReservationDetailView.as_view(), name="reservation-detail"
    ),
    path(
        "reservations/<str:code>/cancel",
        views.ReservationCancelView.as_view(),
        name="reservation-cancel",
    ),
    path(
        "reservations/<str:code>/cancel-preview",
        views_guest.CancelPreviewView.as_view(),
        name="reservation-cancel-preview",
    ),
    path(
        "reservations/<str:code>/timeline",
        views_guest.ReservationTimelineView.as_view(),
        name="reservation-timeline",
    ),
    path(
        "reservations/<str:code>/calendar.ics",
        views_guest.ReservationIcsView.as_view(),
        name="reservation-ics",
    ),
    path(
        "reservations/<str:code>/certificate",
        views_guest.ReservationCertificateView.as_view(),
        name="reservation-certificate",
    ),
    path("me/standing", views_guest.StandingView.as_view(), name="me-standing"),
    path("me/calendar.ics", views_guest.MyIcsView.as_view(), name="me-ics"),
    # مسیرهای عمومی توکن‌محور (بدون ورود)
    path(
        "certificates/verify-offline",
        views_guest.CertificateOfflineCheckView.as_view(),
        name="certificate-verify-offline",
    ),
    path(
        "certificates/verify/<str:token>",
        views_guest.CertificateVerifyView.as_view(),
        name="certificate-verify",
    ),
    path("feedback/<str:token>", views_guest.FeedbackTokenView.as_view(), name="feedback-token"),
    # مسیر کارکنان
    path("staff/guests", views_staff_guests.StaffGuestListView.as_view(), name="staff-guests"),
    path(
        "staff/guests/<uuid:guest_id>",
        views_staff_guests.StaffGuestDetailView.as_view(),
        name="staff-guest-detail",
    ),
    path(
        "staff/guests/<uuid:guest_id>/reveal-phone",
        views_staff_guests.StaffGuestRevealPhoneView.as_view(),
        name="staff-guest-reveal-phone",
    ),
    path("staff/reservations", views.StaffReservationListView.as_view(), name="staff-reservations"),
    path(
        "staff/reservations/create",
        views.StaffReservationCreateView.as_view(),
        name="staff-reservation-create",
    ),
    path(
        "staff/reservations/<str:code>",
        views.StaffReservationDetailView.as_view(),
        name="staff-reservation-detail",
    ),
    path(
        "staff/reservations/<str:code>/events",
        views.StaffReservationEventsView.as_view(),
        name="staff-reservation-events",
    ),
    path(
        "staff/reservations/<str:code>/move",
        views.ReservationMoveView.as_view(),
        name="staff-reservation-move",
    ),
    path(
        "staff/reservations/<str:code>/<str:action_name>",
        views.ReservationTransitionView.as_view(),
        name="staff-reservation-action",
    ),
]
