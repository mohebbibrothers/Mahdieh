"""لاگ ساخت‌یافته با پوشاندن خودکار دادهٔ شخصی (PII).

قاعدهٔ پروژه: دادهٔ حساس هرگز در لاگ نمی‌رود — نه در پیام، نه در فراداده.
این ماژول یک پردازندهٔ structlog اضافه می‌کند که الگوهای کد ملی و تلفن را
پیش از نوشتن پاک می‌کند. تست خودکار این رفتار را تضمین می‌کند.
"""

from __future__ import annotations

from collections.abc import MutableMapping
import logging
import re
import sys
from typing import Any

from django.conf import settings
import structlog

# الگوهای حساس — ترتیب مهم است (تلفن پیش از شمارهٔ عمومی)
_PII_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<!\d)(?:\+98|0098|0)?9\d{9}(?!\d)"), "[phone]"),
    (re.compile(r"(?<!\d)\d{10}(?!\d)"), "[national-id]"),
    # کل مقدار تا پایان خط پوشانده می‌شود (مثلاً «Bearer abc...»)
    (
        re.compile(r"(?i)(authorization|api[_-]?key|token|password|secret)([\"':=\s]+)([^\n]+)"),
        r"\1\2[redacted]",
    ),
)

_SENSITIVE_KEYS = frozenset(
    {
        "phone",
        "phone_number",
        "national_id",
        "national_code",
        "otp",
        "code",
        "password",
        "token",
        "secret",
        "api_key",
        "authorization",
        "cookie",
        "session_key",
    }
)


def scrub_text(value: str) -> str:
    """پوشاندن الگوهای حساس در متن آزاد."""
    for pattern, replacement in _PII_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _scrub_value(value: Any) -> Any:
    """پوشاندن بازگشتی مقادیر در ساختارهای تودرتو."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {
            key: "[redacted]" if str(key).lower() in _SENSITIVE_KEYS else _scrub_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub_value(item) for item in value)
    return value


def pii_scrubbing_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """پردازندهٔ structlog: پوشاندن PII و رخدادهای غیرقابل‌اعتماد."""
    return {key: _scrub_value(value) for key, value in event_dict.items()}


def add_correlation_ids(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """افزودن شناسهٔ درخواست و ترِیس به هر خط لاگ."""
    from apps.core.middleware import get_current_request_id, get_current_trace_id

    request_id = get_current_request_id()
    if request_id:
        event_dict.setdefault("request_id", request_id)
    trace_id = get_current_trace_id()
    if trace_id:
        event_dict.setdefault("trace_id", trace_id)
    return event_dict


def configure_logging() -> None:
    """پیکربندی structlog و پل‌زدن لاگ استاندارد جنگو به آن."""
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    is_json = settings.LOG_FORMAT == "json"

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_correlation_ids,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if is_json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            pii_scrubbing_processor,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[pii_scrubbing_processor, *shared_processors],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # لاگ‌های جنگو هم از همان قالب استفاده کنند
    for name in ("django", "django.request", "django.security", "django.db.backends"):
        child = logging.getLogger(name)
        child.handlers = [handler]
        child.propagate = False

    # کوئری‌های SQL فقط با DEBUG روشن شوند تا لاگ پرحجم نشود
    logging.getLogger("django.db.backends").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """گرفتن لاگر ساخت‌یافته."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]
