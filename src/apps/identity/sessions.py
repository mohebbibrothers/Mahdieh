"""ثبت نشست‌ها — «دستگاه‌های من»، ابطال یک نشست و خروج از همهٔ دستگاه‌ها.

منبع حقیقت «واردبودن» همچنان انبار نشست جنگو است؛ این ماژول فقط یک **فهرست
قابل‌نمایش** کنار آن نگه می‌دارد. ابطال = حذف کلید از انبار نشست (اثر فوری) +
مهر ``revoked_at`` برای تاریخچه.

حریم خصوصی: آی‌پی کامل نگه داشته نمی‌شود (فقط پیشوند شبکه) و متن مرورگر به
برچسب کوتاه «Chrome · Android» خلاصه می‌شود.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import import_module
import ipaddress
from typing import Any, Final

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.audit.choices import AuditAction
from apps.audit.services import AuditEntry, record as audit_record
from apps.core.middleware import client_ip
from apps.identity.models import User, UserSession

TOUCH_INTERVAL: Final[timedelta] = timedelta(minutes=5)
SESSION_TOUCHED_AT: Final[str] = "_session_touched_at"
IPV4_PREFIX: Final[int] = 24
IPV6_PREFIX: Final[int] = 48

_BROWSERS: Final[tuple[tuple[str, str], ...]] = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("SamsungBrowser", "Samsung Internet"),
    ("Firefox/", "Firefox"),
    ("CriOS", "Chrome"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_SYSTEMS: Final[tuple[tuple[str, str], ...]] = (
    ("Android", "Android"),
    ("iPhone", "iOS"),
    ("iPad", "iPadOS"),
    ("Windows", "Windows"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
)


@dataclass(frozen=True, slots=True)
class SessionView:
    """نمای ایمن یک نشست برای فهرست «دستگاه‌های من»."""

    id: str
    device: str
    network: str
    created_at: datetime
    last_seen_at: datetime
    is_current: bool

    def as_dict(self) -> dict[str, Any]:
        """نمای سریال‌پذیر."""
        return {
            "id": self.id,
            "device": self.device,
            "network": self.network,
            "created_at": self.created_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "is_current": self.is_current,
        }


def network_of(ip: str) -> str:
    """پیشوند شبکهٔ یک آی‌پی (IPv4 ‎/24، IPv6 ‎/48) — کافی برای «این من بودم؟»."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    prefix = IPV4_PREFIX if address.version == 4 else IPV6_PREFIX  # noqa: PLR2004
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


def describe_agent(user_agent: str) -> str:
    """برچسب کوتاه مرورگر و سیستم‌عامل از رشتهٔ User-Agent."""
    browser = next((name for token, name in _BROWSERS if token in user_agent), "مرورگر")
    system = next((name for token, name in _SYSTEMS if token in user_agent), "")
    return f"{browser} · {system}" if system else browser


def _store(session_key: str) -> Any:
    engine = import_module(settings.SESSION_ENGINE)
    return engine.SessionStore(session_key=session_key)


def register(request: HttpRequest, user: User) -> None:
    """ثبت نشست تازه پس از ورود موفق (گیرندهٔ سیگنال ``user_logged_in``)."""
    session = getattr(request, "session", None)
    if session is None:
        return
    if not session.session_key:
        session.save()
    key = session.session_key or ""
    if not key:  # pragma: no cover - انبار نشست کلید نساخت
        return
    UserSession.objects.update_or_create(
        session_key=key,
        defaults={
            "user": user,
            "network": network_of(client_ip(request)),
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300],
            "created_at": timezone.now(),
            "last_seen_at": timezone.now(),
            "revoked_at": None,
        },
    )
    session[SESSION_TOUCHED_AT] = timezone.now().timestamp()


def end(session_key: str | None) -> None:
    """پایان عادی نشست (خروج) — فقط مهر زمان؛ انبار نشست را جنگو پاک می‌کند."""
    if session_key:
        UserSession.objects.filter(session_key=session_key, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )


def rekey(*, old_key: str | None, new_key: str | None) -> None:
    """هم‌گام‌سازی ردیف نشست پس از چرخش کلید (تأیید دومرحله‌ای)."""
    if old_key and new_key and old_key != new_key:
        UserSession.objects.filter(session_key=old_key).update(session_key=new_key)


def touch(request: HttpRequest) -> None:
    """به‌روزرسانی «آخرین فعالیت» — حداکثر هر پنج دقیقه یک نوشتن."""
    session = getattr(request, "session", None)
    user = getattr(request, "user", None)
    if session is None or user is None or not user.is_authenticated or not session.session_key:
        return
    now = timezone.now()
    last = session.get(SESSION_TOUCHED_AT)
    if isinstance(last, int | float) and now.timestamp() - last < TOUCH_INTERVAL.total_seconds():
        return
    session[SESSION_TOUCHED_AT] = now.timestamp()
    updated = UserSession.objects.filter(
        session_key=session.session_key, revoked_at__isnull=True
    ).update(last_seen_at=now)
    if not updated:
        # نشست‌های پیش از این قابلیت (یا ورود از پنل جنگو) هم در فهرست دیده شوند.
        register(request, user)


def active_for(user: User, *, current_key: str | None) -> list[SessionView]:
    """نشست‌های فعال کاربر — جاری در صدر."""
    rows = UserSession.objects.filter(user=user, revoked_at__isnull=True).order_by("-last_seen_at")
    views = [
        SessionView(
            id=str(row.id),
            device=describe_agent(row.user_agent),
            network=row.network,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            is_current=bool(current_key and row.session_key == current_key),
        )
        for row in rows
    ]
    return sorted(views, key=lambda view: not view.is_current)


def revoke(*, user: User, session_id: str, request: HttpRequest) -> bool:
    """ابطال یک نشست کاربر (نه نشست جاری — برای آن «خروج» هست)."""
    row = UserSession.objects.filter(user=user, id=session_id, revoked_at__isnull=True).first()
    if row is None:
        return False
    current = getattr(getattr(request, "session", None), "session_key", None)
    if row.session_key == current:
        return False
    _revoke_rows([row], user=user, request=request, scope="single")
    return True


def revoke_others(*, user: User, request: HttpRequest) -> int:
    """خروج از همهٔ دستگاه‌های دیگر — نشست جاری باز می‌ماند."""
    current = getattr(getattr(request, "session", None), "session_key", None)
    rows = list(
        UserSession.objects.filter(user=user, revoked_at__isnull=True).exclude(
            session_key=current or ""
        )
    )
    _revoke_rows(rows, user=user, request=request, scope="others")
    return len(rows)


def _revoke_rows(rows: list[UserSession], *, user: User, request: HttpRequest, scope: str) -> None:
    if not rows:
        return
    now = timezone.now()
    with transaction.atomic():
        for row in rows:
            _store(row.session_key).delete()
        UserSession.objects.filter(pk__in=[row.pk for row in rows]).update(revoked_at=now)
        audit_record(
            AuditEntry(
                action=AuditAction.SESSION_REVOKED,
                actor_id=user.pk,
                actor_label=user.display_name,
                target_type="user",
                target_id=str(user.pk),
                metadata={"count": len(rows), "scope": scope},
                request=request,
            )
        )
