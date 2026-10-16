"""مسیرهای API رزرو — زیر ``/api/v1/`` و در فضای‌نام ``api-v1``."""

from __future__ import annotations

from django.urls import path

from apps.reservation import views

urlpatterns = [
    # تقویم عمومی
    path("calendar", views.CalendarView.as_view(), name="calendar"),
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
    # مسیر کارکنان
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
