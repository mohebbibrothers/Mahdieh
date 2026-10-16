"""قرارداد پایشگر سلامت — کد خروجی، هشدار ضد-اسپم، و یک منبع حقیقت.

چرا این آزمون‌ها مهم‌اند؟ پایشگری که خودش اشتباه بگوید، بدتر از نبودنش است:
یا آژیرِ همیشگی می‌شود (و کسی گوش نمی‌دهد) یا سکوتِ همیشگی (و حادثه لو نمی‌رود).
پس سه چیز آزموده می‌شود: درستیِ داوری، درستیِ کد خروج، و **درستیِ سکوت**.
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from typing import Any

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.utils import timezone
import pytest
import time_machine

from apps.core.management.commands import healthcheck as healthcheck_command
from apps.core.management.commands.healthcheck import (
    EXIT_DEGRADED,
    EXIT_HEALTHY,
    EXIT_UNHEALTHY,
)

#: پایشگر، پایگاه‌داده را واقعاً می‌سنجد؛ پس آزمون‌ها هم به پایگاه‌دادهٔ واقعی
#: (Postgres در محیط آزمون) نیاز دارند — همان چیزی که در تولید سنجیده می‌شود.
pytestmark = pytest.mark.django_db


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    """پروندهٔ وضعیت جدا برای هر آزمون (تا آزمون‌ها به هم نشت نکنند)."""
    return tmp_path / "healthcheck.state"


def _run(*args: str) -> int:
    """فرمان را اجرا می‌کند و کد خروجش را برمی‌گرداند (SystemExit را می‌گیرد)."""
    with pytest.raises(SystemExit) as exit_info:
        call_command("healthcheck", *args)
    code = exit_info.value.code
    return int(code) if code is not None else 0


def test_healthy_stack_exits_zero(state_file: Path) -> None:
    """پشتهٔ سالم ⇒ کد صفر. اگر این آزمون قرمز شود، هر هشدار دیگری بی‌معناست."""
    assert _run(f"--state-file={state_file}") == EXIT_HEALTHY
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["status"] == "healthy"


def test_cached_outage_is_degraded_not_unhealthy(state_file: Path, monkeypatch, capsys) -> None:
    """قطعی کش ⇒ «افت‌کرده» (کد ۱)، نه «ناسالم».

    کش منبع حقیقت نیست؛ اگر قطعی‌اش ترافیک را قطع کند، خودمان را بی‌دلیل از چرخه
    بیرون گذاشته‌ایم. ولی باید *دیده* شود — و این آزمون همان را تضمین می‌کند.
    """

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        msg = "cache down"
        raise ConnectionError(msg)

    monkeypatch.setattr(cache, "set", _boom)
    monkeypatch.setattr(cache, "get", _boom)
    code = _run(f"--state-file={state_file}", "--json")
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == EXIT_DEGRADED
    assert payload["status"] == "degraded"
    assert "cache" in payload["detail"]


def test_database_outage_is_unhealthy(state_file: Path, monkeypatch) -> None:
    """قطعی پایگاه‌داده ⇒ «ناسالم» (کد ۲): باید همان لحظه رسیدگی شود."""

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        msg = "db down"
        raise OperationalErrorLike(msg)

    monkeypatch.setattr(connection, "cursor", _boom)
    assert _run(f"--state-file={state_file}") == EXIT_UNHEALTHY


class OperationalErrorLike(Exception):
    """جانشین خطای پایگاه‌داده در آزمون (بدون وابستگی به درایور)."""


def test_unreachable_endpoint_is_unhealthy_with_a_clear_reason(state_file: Path, capsys) -> None:
    """اگر سرویس بالا نباشد، پایشگر باید «ناسالم» بگوید، نه «سالم» یا «بی‌خبر»."""
    code = _run(
        "--state-file=" + str(state_file),
        "--base-url=http://127.0.0.1:9",
        "--json",
    )
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == EXIT_UNHEALTHY
    assert payload["status"] == "unhealthy"
    assert "9" in payload["detail"] or "نرسیدن" in payload["detail"]


def test_only_http_schemes_are_accepted(state_file: Path, capsys) -> None:
    """نشانی پایش باید وب باشد: ``file://`` نباید پروندهٔ سرور را بخواند."""
    code = _run(
        f"--state-file={state_file}",
        "--base-url=file:///etc/passwd",
        "--json",
    )
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == EXIT_UNHEALTHY
    assert "http" in payload["detail"]


def test_alert_fires_on_transition_not_on_every_run(
    state_file: Path, monkeypatch, settings
) -> None:
    """هشدار ضد-اسپم: بار اول هشدار، بار دوم سکوت، و هنگام بهبود یک خبر خوب.

    این «درستیِ سکوت» است؛ اگر هر ۵ دقیقه یک ایمیل برود، تیم هشدارها را می‌بندد و
    حادثهٔ واقعی هم دیده نمی‌شود.
    """
    # محیط آزمون از پیش صندوق پستی درون‌حافظه‌ای دارد (``MAILERS`` در
    # ``config.settings.test``)؛ نیازی به تنظیم دوباره نیست — و تنظیم دوباره،
    # هشدار منسوخ‌شدن ``EMAIL_BACKEND`` در Django 6.1 را روشن می‌کند.
    # قالب تازهٔ Django 6.1: ADMINS فهرست نشانی‌ها است (زوج «نام، نشانی» منسوخ شد).
    settings.ADMINS = ["ops@mahdie-shohada.example"]

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        msg = "cache down"
        raise ConnectionError(msg)

    monkeypatch.setattr(cache, "set", _boom)
    monkeypatch.setattr(cache, "get", _boom)

    assert _run(f"--state-file={state_file}", "--notify") == EXIT_DEGRADED
    assert len(mail.outbox) == 1, "نخستین افت باید هشدار بدهد."
    assert "degraded" in mail.outbox[0].subject

    assert _run(f"--state-file={state_file}", "--notify") == EXIT_DEGRADED
    assert len(mail.outbox) == 1, "افتِ پایدار نباید هر بار ایمیل بفرستد."

    # یادآوری دیرهنگام: پس از گذشتن «فاصلهٔ یادآوری»، یک یادآوری می‌رود — نه بیشتر.
    with time_machine.travel(timezone.now() + timedelta(hours=13), tick=False):
        assert _run(f"--state-file={state_file}", "--notify") == EXIT_DEGRADED
    assert len(mail.outbox) == 2, "خرابیِ طولانی باید یک یادآوری دیرهنگام بدهد."

    monkeypatch.undo()
    assert _run(f"--state-file={state_file}", "--notify") == EXIT_HEALTHY
    assert len(mail.outbox) == 3, "بهبود وضعیت باید خبر خوب بفرستد."
    assert "بازگشت" in mail.outbox[2].subject


def test_renotify_can_be_disabled(state_file: Path, monkeypatch, settings) -> None:
    """با ``--renotify-after=0`` پس از هشدار نخست، هیچ یادآوری‌ای نمی‌رود."""
    settings.ADMINS = ["ops@mahdie-shohada.example"]

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        msg = "cache down"
        raise ConnectionError(msg)

    monkeypatch.setattr(cache, "set", _boom)
    monkeypatch.setattr(cache, "get", _boom)
    _run(f"--state-file={state_file}", "--notify", "--renotify-after=0")
    with time_machine.travel(timezone.now() + timedelta(days=3), tick=False):
        _run(f"--state-file={state_file}", "--notify", "--renotify-after=0")
    assert len(mail.outbox) == 1


def test_quiet_mode_is_silent_when_healthy(state_file: Path, capsys) -> None:
    """``--quiet`` در حالت سالم هیچ چاپ نمی‌کند (لاگ systemd تمیز می‌ماند)."""
    assert _run(f"--state-file={state_file}", "--quiet") == EXIT_HEALTHY
    assert capsys.readouterr().out.strip() == ""


def test_probe_uses_the_readiness_contract() -> None:
    """پایشگر و نمای آمادگی باید **یک** داوری داشته باشند، نه دو تا.

    اگر روزی کسی معیار پایشگر را جدا کند، این آزمون قرمز می‌شود و جلوی
    «سایت سالم است ولی پایشگر فریاد می‌زند» را می‌گیرد.
    """
    from apps.core.health import readiness_payload

    payload, status_code = readiness_payload()
    probe_payload, probe_status = healthcheck_command.Command()._probe_in_process()
    # مقایسهٔ بخش **معنادار** گزارش؛ ``latency_ms`` طبیعتاً در هر اجرا فرق می‌کند.
    for key in ("status", "failed", "degraded"):
        assert probe_payload[key] == payload[key], f"اختلاف در «{key}» بین پایشگر و نما."
    assert set(probe_payload["checks"]) == set(payload["checks"])
    assert probe_status == status_code


def test_exit_codes_are_the_documented_contract() -> None:
    """کدهای خروجی بخشی از قرارداد عمومی‌اند (systemd و پایش روی آن حساب می‌کنند)."""
    assert (EXIT_HEALTHY, EXIT_DEGRADED, EXIT_UNHEALTHY) == (0, 1, 2)
