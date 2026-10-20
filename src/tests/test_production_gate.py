"""دروازهٔ انتشار — «تولید فقط وقتی سبز است که واقعاً سبز باشد».

این فایل سه چیز را قفل می‌کند:

    ۱. ``production_check``: خطاهای واقعی بلوکه می‌کنند؛ «هشدارهای عمدی» پروژه
       (شمارهٔ پذیرشِ نرسیده و الگوهای پیامکی که پنل پس از دامنه می‌دهد) انتشار را
       نمی‌بندند **ولی پنهان هم نمی‌شوند** — و در حالت ``--strict`` بلوکه می‌کنند.
    ۲. گارد «ناسازگاری محیط»: ماشینی که تنظیمات تولید دارد ولی خودش را تولید
       نمی‌داند (یا برعکس) باید در لحظهٔ استقرار لو برود — همان اشتباهی که یک بار
       در نمونهٔ محیط تولید رخ داد.
    ۳. قطعات استقرار: نقطهٔ ورود کانتینر همین دروازه را اجرا می‌کند، نمونهٔ محیط
       تولید هم‌خوان است، و ابزارهای کاوش/آشوب در جای خود هستند.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from django.conf import settings
from django.core.checks import Error, Warning
from django.core.management import call_command
from django.test import override_settings
import pytest

from apps.core.management.commands.production_check import (
    INTENTIONAL_WARNINGS,
    Command as ProductionCheckCommand,
)

pytestmark = [pytest.mark.integration, pytest.mark.contract]

REPO_ROOT = Path(settings.BASE_DIR).parent
DEPLOY_EXAMPLE = REPO_ROOT / "deploy" / "env" / "mahdieh.env.example"
ENTRYPOINT = REPO_ROOT / "entrypoint.sh"


def _error(identifier: str, message: str = "خطای آزمون") -> Error:
    """ساخت یک خطای بررسی با شناسهٔ دلخواه."""
    return Error(message, id=identifier)


def _warning(identifier: str, message: str = "هشدار آزمون") -> Warning:
    """ساخت یک هشدار بررسی با شناسهٔ دلخواه."""
    return Warning(message, id=identifier)


# ───────────────────────────── تفکیک دو سبد ─────────────────────────────────
def test_intentional_warnings_do_not_block_by_default() -> None:
    """شمارهٔ پذیرشِ نرسیده و الگوی پیامکیِ نیامده، سایت را قفل نمی‌کنند."""
    messages = [_error("mahdieh.W010"), _warning("mahdieh.W001")]
    blocking, intentional = ProductionCheckCommand._split(messages, strict=False)
    assert blocking == []
    assert {item.id for item in intentional} == {"mahdieh.W010", "mahdieh.W001"}


def test_intentional_warnings_block_in_strict_mode() -> None:
    """در روز انتشار، همان هشدارهای عمدی هم باید بسته شوند (سبزِ واقعی)."""
    messages = [_error("mahdieh.W010"), _warning("mahdieh.W011")]
    blocking, intentional = ProductionCheckCommand._split(messages, strict=True)
    assert {item.id for item in blocking} == {"mahdieh.W010", "mahdieh.W011"}
    assert intentional == []


def test_unknown_errors_and_warnings_always_block() -> None:
    """خطای بی‌شناسه و هشدار امنیتی جنگو هرگز بخشیده نمی‌شوند."""
    messages = [_error("security.W018"), _warning("", "پیام بی‌شناسه"), _error("mahdieh.E004")]
    blocking, intentional = ProductionCheckCommand._split(messages, strict=False)
    assert len(blocking) == 3
    assert intentional == []


def test_every_intentional_id_has_a_written_reason() -> None:
    """هیچ بخشودگی بدون دلیل مکتوب: هر شناسه، یک توضیح انسانی دارد."""
    # W020 (مرحلهٔ ۲): فرانت چرخهٔ استقرار و بازگشت خودش را دارد؛ بستهٔ خرابش نباید بک‌اند را قفل کند.
    assert set(INTENTIONAL_WARNINGS) == {
        "mahdieh.W001",
        "mahdieh.W010",
        "mahdieh.W011",
        "mahdieh.W020",
    }
    assert all(len(reason) > 20 for reason in INTENTIONAL_WARNINGS.values())


# ─────────────────────── گارد ناسازگاری نامِ محیط ───────────────────────────
# بررسی‌های مدل در موتور MySQL/MariaDB نسخهٔ سرور را می‌پرسند (اتصال واقعی).
@pytest.mark.django_db
def test_mismatch_between_settings_module_and_environment_blocks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ماژول تنظیمات «توسعه» با MAHDIEH_ENVIRONMENT=production ⇒ بلوکه."""
    with override_settings(ENVIRONMENT="production"), pytest.raises(SystemExit) as exit_info:
        call_command("production_check", json=False)
    assert exit_info.value.code == 1
    assert "ناسازگاری محیط" in capsys.readouterr().out


def test_local_mode_is_quiet_and_does_not_run_deployment_checks() -> None:
    """در اجرای محلی/آزمون، بررسی‌های استقرار کنار گذاشته می‌شوند (بی‌ربط، نه پنهان)."""
    command = ProductionCheckCommand()
    assert ProductionCheckCommand.requires_system_checks == [], (
        "بدون این تنظیم، جنگو پیش از رسیدن به دروازه پروژه را با SystemCheckError می‌بندد."
    )
    mode = command._mode()
    assert mode in {"local", "production", "mismatch"}


# بررسی‌های مدل در موتور MySQL/MariaDB نسخهٔ سرور را می‌پرسند (اتصال واقعی).
@pytest.mark.django_db
def test_command_reports_machine_readable_shape(capsys: pytest.CaptureFixture[str]) -> None:
    """گزارش JSON برای پایش: محیط، حالت، سبدها و وضعیت."""
    call_command("production_check", json=True)
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) >= {"environment", "settings_module", "mode", "strict", "blocking", "ok"}
    assert payload["ok"] is True


# ──────────────────────────── قطعات استقرار ─────────────────────────────────
def test_entrypoint_uses_the_production_gate() -> None:
    """کانتینر باید از همین دروازه رد شود، نه از یک بررسی دستی."""
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "production_check" in text
    assert "gunicorn" in text or 'exec "$@"' in text
    assert "MAHDIEH_SECRET_KEY" in text, "گارد متغیرهای حیاتی در نقطهٔ ورود نیست"


def test_docker_image_sets_entrypoint_and_exec_bit() -> None:
    """تصویر تولیدی باید نقطهٔ ورود داشته باشد؛ وگرنه مهاجرت‌ها جا می‌مانند."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["/app/entrypoint.sh"]' in dockerfile
    assert "chmod +x /app/entrypoint.sh" in dockerfile
    assert (REPO_ROOT / "entrypoint.sh").stat().st_mode & 0o111


def test_deploy_env_sample_declares_the_environment_name_settings_read() -> None:
    """نامِ محیط در نمونهٔ تولید، همان چیزی است که تنظیمات می‌خواند."""
    text = DEPLOY_EXAMPLE.read_text(encoding="utf-8")
    assert "MAHDIEH_ENVIRONMENT=production" in text
    assert "\nENVIRONMENT=production" not in text, "نام قدیمی/غلط برگشته است"


def test_env_parity_check_passes_for_both_samples() -> None:
    """اسکریپت هم‌خوانی محیط، هر دو نمونه (توسعه و تولید) را سبز می‌بیند."""
    result = subprocess.run(  # noqa: S603 - مسیر ثابت مخزن
        [sys.executable, str(REPO_ROOT / "scripts" / "check_env_parity.py")],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_chaos_and_dast_tooling_exist_and_are_executable() -> None:
    """ابزارهای فاز پایانی: کاوش امنیتی و آزمون آشوب، در جای خود."""
    probe = REPO_ROOT / "scripts" / "dast_probe.py"
    chaos = REPO_ROOT / "src" / "tests" / "test_chaos.py"
    assert probe.is_file() and probe.stat().st_mode & 0o111
    assert chaos.is_file()
    chaos_text = chaos.read_text(encoding="utf-8")
    assert chaos_text.count("def test_") >= 10, "مجموعهٔ آشوب باید دست‌کم ده سناریو داشته باشد"
    dast_text = probe.read_text(encoding="utf-8")
    for check in ("check_csrf", "check_injection", "check_anonymous_access", "check_cors"):
        assert check in dast_text


def test_makefile_exposes_the_final_phase_targets() -> None:
    """اهداف ساخت باید همان چیزی باشند که در رانبوک‌ها نوشته شده است."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("dast:", "chaos:", "sms-golive:", "sms-selfcheck:", "preflight:"):
        assert target in makefile, f"هدف «{target}» در Makefile نیست"
