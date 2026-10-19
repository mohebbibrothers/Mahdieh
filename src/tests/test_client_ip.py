"""آی‌پی واقعی کاربر و شناسهٔ سقف نرخ — جعل‌ناپذیر با هدر کاربر.

درس استقرار cPanel: Apache مستقیم درخواست را به برنامه می‌دهد و هیچ پروکسی‌ای
``X-Forwarded-For`` را بازنویسی نمی‌کند. اگر برنامه به ابتدای این هدر اعتماد کند،
هر درخواست با یک هدر تازه «کاربر تازه» است و سقف نرخ کد ورود بی‌اثر می‌شود.
"""

from __future__ import annotations

from django.http import HttpRequest
from django.test import RequestFactory, override_settings
import pytest
from rest_framework.request import Request
from rest_framework.throttling import AnonRateThrottle

from apps.core.middleware import client_ip

pytestmark = pytest.mark.unit

SPOOFED = "6.6.6.6, 10.0.0.9"


class _OtpLikeThrottle(AnonRateThrottle):
    """نرخ صریح؛ تنظیمات آزمون سقف‌های سراسری را عمداً خالی می‌کند."""

    rate = "5/min"


def _request(xff: str | None = SPOOFED, remote: str = "203.0.113.7") -> HttpRequest:
    extra = {"REMOTE_ADDR": remote}
    if xff is not None:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    return RequestFactory().get("/", **extra)


@override_settings(TRUSTED_PROXY_COUNT=0)
def test_without_trusted_proxies_the_header_is_ignored() -> None:
    assert client_ip(_request()) == "203.0.113.7"


@override_settings(TRUSTED_PROXY_COUNT=1)
def test_one_trusted_proxy_takes_the_hop_it_appended() -> None:
    # Nginx ما آی‌پی کاربر را به **انتها** می‌افزاید؛ ابتدای زنجیره جعلی است.
    assert client_ip(_request()) == "10.0.0.9"


@override_settings(TRUSTED_PROXY_COUNT=5)
def test_overstated_proxy_count_is_clamped() -> None:
    assert client_ip(_request()) == "6.6.6.6"


@override_settings(TRUSTED_PROXY_COUNT=1)
def test_missing_or_blank_header_falls_back_to_remote_addr() -> None:
    assert client_ip(_request(xff=None)) == "203.0.113.7"
    assert client_ip(_request(xff=" , ")) == "203.0.113.7"


def test_default_setting_trusts_no_proxy() -> None:
    from django.conf import settings

    assert settings.TRUSTED_PROXY_COUNT == 0
    assert settings.REST_FRAMEWORK["NUM_PROXIES"] == settings.TRUSTED_PROXY_COUNT


@pytest.mark.parametrize("xff", ["1.1.1.1", "2.2.2.2", "3.3.3.3, 4.4.4.4"])
def test_throttle_identity_cannot_be_rotated_by_header(xff: str) -> None:
    """شناسهٔ سقف نرخ DRF با عوض‌شدن هدر عوض نمی‌شود (همان REMOTE_ADDR)."""
    ident = _OtpLikeThrottle().get_ident(Request(_request(xff=xff)))
    assert ident == "203.0.113.7"
