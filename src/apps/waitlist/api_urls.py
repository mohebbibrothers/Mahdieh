"""مسیرهای API صف انتظار — زیر ``/api/v1/``."""

from __future__ import annotations

from django.urls import path

from apps.waitlist import views

urlpatterns = [
    # مسیر زائر
    path("waitlist", views.WaitlistListCreateView.as_view(), name="waitlist"),
    path(
        "waitlist/<uuid:entry_id>/withdraw",
        views.WaitlistWithdrawView.as_view(),
        name="waitlist-withdraw",
    ),
    path(
        "waitlist/offers/by-token/<str:token>",
        views.OfferByTokenView.as_view(),
        name="waitlist-offer-by-token",
    ),
    path(
        "waitlist/offers/<uuid:offer_id>/respond",
        views.OfferRespondView.as_view(),
        name="waitlist-offer-respond",
    ),
    # مسیر کارکنان
    path("staff/waitlist", views.StaffWaitlistView.as_view(), name="staff-waitlist"),
]
