"""روز راه‌اندازی پیامک — قرارداد «کد که رسید، یک فرمان همه‌چیز را انجام می‌دهد».

این فایل، همان چیزی را قفل می‌کند که در آن روزِ پرریسک لازم است:

    ۱. ``sms_selfcheck`` **بی‌شبکه** و **با شبکه** درست رفتار کند: بدون شماره یا
       بدون ``--confirm`` هیچ پیامی نمی‌رود؛ با کد غایب، ارسال **مسدود** می‌شود و
       کد خروج ۱ می‌دهد (سبزِ نمایشی ممنوع).
    ۲. اسکریپت ``scripts/sms_golive.sh``: فرم کدها را می‌سنجد، فایل محیط را
       اتمی و با پشتیبان بازنویسی می‌کند، کدهای حیاتی را اجباری می‌داند و
       بازگشت (``--rollback``) دارد.
    ۳. رانبوک روز راه‌اندازی موجود است و به فرمان‌های واقعی اشاره می‌کند.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
import sys

from django.conf import settings
from django.core.management import call_command
from django.test import override_settings
import pytest

from apps.notifications import patterns
from apps.notifications.choices import SmsStatus, SmsTemplateKey
from apps.notifications.models import SmsDelivery

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

REPO_ROOT = Path(settings.BASE_DIR).parent
SCRIPT = REPO_ROOT / "scripts" / "sms_golive.sh"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "sms-golive.md"
#: مسیر کامل مفسر پوسته — «مسیر جزئی» هم ایراد امنیتی است (S607).
_BASH = shutil.which("bash") or "/bin/bash"
PHONE = "+989121234567"
GOOD_CODES = {
    "otp_login": "AbC123xyz",
    "booking_confirmed": "Zz9Yy8Xx7",
    "offer_created": "Qq1Ww2Ee3",
}


@contextmanager
def _env_file(text: str) -> Iterator[Path]:
    """فایل محیط سرآزمون (بی‌اثر بر سرور واقعی)."""
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "mahdieh.env"
        path.write_text(text, encoding="utf-8")
        yield path


def _run_script(
    *args: str, env_file: Path, verify: bool = False
) -> subprocess.CompletedProcess[str]:
    """اجرای اسکریپت راه‌اندازی روی فایل موقت.

    ``verify=True`` بازرسی واقعی سرویس را می‌خواهد (کند و وابسته به مفسر پروژه)؛
    آزمون‌های سازوکارِ فایل، ``--no-verify`` می‌دهند تا سریع و hermetic بمانند و
    یک آزمون جدا، *خودِ* بازرسی را می‌سنجد.
    """
    extra = [] if verify else ["--no-verify"]
    return subprocess.run(  # noqa: S603 - آرگومان‌ها در خود همین آزمون ساخته می‌شوند
        [_BASH, str(SCRIPT), "--env", str(env_file), *extra, *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )


# ───────────────────────────── اسکریپت راه‌اندازی ─────────────────────────────
def test_script_exists_and_is_executable() -> None:
    """اسکریپت باید موجود، اجراشدنی و بی‌خطای نحوی باشد."""
    assert SCRIPT.is_file(), "scripts/sms_golive.sh نیست"
    assert SCRIPT.stat().st_mode & 0o111, "اسکریپت اجراشدنی نیست"
    check = subprocess.run(  # noqa: S603 - مسیر ثابت مخزن
        [_BASH, "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert check.returncode == 0, check.stderr


def test_check_mode_validates_without_touching_the_file() -> None:
    """حالت --check چیزی تغییر نمی‌دهد — «بازرسی» یعنی بی‌اثر."""
    with _env_file("SMS_PATTERN_CODES=\nOTHER=1\n") as path:
        before = path.read_text(encoding="utf-8")
        result = _run_script(
            "--codes", ",".join(f"{k}={v}" for k, v in GOOD_CODES.items()), "--check", env_file=path
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert path.read_text(encoding="utf-8") == before


def test_apply_writes_codes_and_keeps_other_lines() -> None:
    """اعمال: خط SMS_PATTERN_CODES بازنویسی می‌شود، بقیهٔ خطوط دست‌نخورده می‌مانند."""
    with _env_file("SECRET_KEY=keepme\nSMS_PATTERN_CODES=\nAFTER=1\n") as path:
        result = _run_script(
            "--codes", ",".join(f"{k}={v}" for k, v in GOOD_CODES.items()), env_file=path
        )
        assert result.returncode == 0, result.stdout + result.stderr
        text = path.read_text(encoding="utf-8")
        assert "SECRET_KEY=keepme" in text
        assert "AFTER=1" in text
        codes = json.loads(text.split("SMS_PATTERN_CODES=", 1)[1].splitlines()[0])
        assert codes == GOOD_CODES
        backup = list(path.parent.glob("mahdieh.env.bak.*"))
        assert backup, "پشتیبان گرفته نشد"


def test_scripts_rollback_restores_the_previous_file() -> None:
    """بازگشت، فایل محیط را به وضع پیشین برمی‌گرداند (بی‌ریسکی روز راه‌اندازی)."""
    with _env_file("SMS_PATTERN_CODES=\nKEEP=1\n") as path:
        _run_script("--codes", ",".join(f"{k}={v}" for k, v in GOOD_CODES.items()), env_file=path)
        assert "AbC123xyz" in path.read_text(encoding="utf-8")
        rollback = _run_script("--rollback", env_file=path)
        assert rollback.returncode == 0, rollback.stdout + rollback.stderr
        assert "AbC123xyz" not in path.read_text(encoding="utf-8")


def test_missing_critical_code_refuses_to_apply() -> None:
    """کد حیاتی غایب ⇒ اسکریپت **اعمال نمی‌کند** مگر با پذیرش صریح."""
    with _env_file("SMS_PATTERN_CODES=\n") as path:
        result = _run_script("--codes", "welcome=Ww12Ee34", env_file=path)
        assert result.returncode != 0
        assert "حیاتی" in (result.stdout + result.stderr)
        assert "Ww12Ee34" not in path.read_text(encoding="utf-8")

        allowed = _run_script("--codes", "welcome=Ww12Ee34", "--allow-missing", env_file=path)
        assert allowed.returncode == 0, allowed.stdout + allowed.stderr
        assert "Ww12Ee34" in path.read_text(encoding="utf-8")


def test_malformed_and_duplicate_codes_are_rejected() -> None:
    """شکل کد و یکتایی کلید، پیش از نوشتن سنجیده می‌شود."""
    with _env_file("SMS_PATTERN_CODES=\n") as path:
        short = _run_script("--codes", "otp_login=ab", env_file=path)
        assert short.returncode != 0 and "نامعتبر" in (short.stdout + short.stderr)
        dup = _run_script("--codes", "otp_login=AbC123xyz,otp_login=Zz9Yy8Xx7", env_file=path)
        assert dup.returncode != 0 and "تکراری" in (dup.stdout + dup.stderr)


def test_codes_file_accepts_colon_format_and_comments() -> None:
    """فایل کدها: «کلید: کد»، خط خالی و # نظر پذیرفته می‌شود."""
    with _env_file("SMS_PATTERN_CODES=\n") as path:
        codes_file = path.parent / "codes.txt"
        codes_file.write_text(
            "# کدهای پنل\n\notp_login: AbC123xyz\nbooking_confirmed: Zz9Yy8Xx7\n",
            encoding="utf-8",
        )
        result = _run_script("--codes-file", str(codes_file), "--allow-missing", env_file=path)
        assert result.returncode == 0, result.stdout + result.stderr
        written = json.loads(
            path.read_text(encoding="utf-8").split("SMS_PATTERN_CODES=", 1)[1].splitlines()[0]
        )
        assert written["otp_login"] == "AbC123xyz"


def test_service_verification_is_loud_when_the_interpreter_is_missing() -> None:
    """اگر مفسر پروژه نباشد، اسکریپت هشدار می‌دهد — «بازرسیِ ساکت» ممنوع."""
    with _env_file("SMS_PATTERN_CODES=\n") as path:
        result = _run_script(
            "--codes",
            ",".join(f"{k}={v}" for k, v in GOOD_CODES.items()),
            "--app-dir",
            str(path.parent / "nowhere"),
            env_file=path,
            verify=True,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        assert "مفسر پروژه پیدا نشد" in output


# ────────────────────────────── دستور sms_selfcheck ──────────────────────────
def test_selfcheck_without_phone_sends_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    """بدون ``--to`` هیچ ارسالی انجام نمی‌شود — آزمون بی‌قربانی."""
    call_command("sms_selfcheck", json=False)
    captured = capsys.readouterr()
    assert "آزمون ارسال نشد" in captured.out
    assert SmsDelivery.objects.count() == 0


def test_selfcheck_without_confirm_sends_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    """بدون ``--confirm`` هم چیزی نمی‌رود (اجرای تصادفی، پیام واقعی نمی‌فرستد)."""
    call_command("sms_selfcheck", to=PHONE, json=False)
    captured = capsys.readouterr()
    assert "تأیید صریح" in captured.out
    assert SmsDelivery.objects.count() == 0


@override_settings(SMS_PATTERN_CODES="")
def test_selfcheck_without_pattern_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    """الگوی آزمون بی‌کد ⇒ کد خروج ۱ و توضیح روشن (نه سبزِ نمایشی)."""
    with pytest.raises(SystemExit) as exit_info:
        call_command("sms_selfcheck", to=PHONE, confirm=True, json=False)
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "کد الگو ندارد" in captured.out
    assert SmsDelivery.objects.count() == 0


@override_settings(
    SMS_ENABLED=True,
    SMS_PROVIDER="mock",
    SMS_PATTERN_CODES=json.dumps({"dry_run": "Dd09Ry11Un", "otp_login": "AbC123xyz"}),
)
def test_selfcheck_sends_one_real_message_and_records_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """با کد موجود: یک پیام (از الگوی آزمایشی) ثبت و گزارش می‌شود."""
    call_command("sms_selfcheck", to=PHONE, confirm=True, json=False)
    captured = capsys.readouterr()
    assert "پیام آزمون ارسال شد" in captured.out
    delivery = SmsDelivery.objects.get()
    assert delivery.template_key == str(SmsTemplateKey.DRY_RUN)
    # ماک، «ارسال آزمایشی» است ⇒ وضعیت SUPPRESSED؛ همان «رسیده» شمرده می‌شود.
    assert delivery.status in {SmsStatus.SENT, SmsStatus.SUPPRESSED}


@override_settings(SMS_PATTERN_CODES="")
def test_selfcheck_json_report_is_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    """گزارش JSON برای پایش: آمادگی الگوها + نتیجهٔ آزمون."""
    call_command("sms_selfcheck", json=True)
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"readiness", "test"}
    assert payload["readiness"]["bound"] == []
    assert payload["test"]["outcome"] == "skipped"


def test_selfcheck_rejects_unknown_template() -> None:
    """قالب ناشناخته ⇒ خطای روشن با فهرست قالب‌های مجاز."""
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("sms_selfcheck", to=PHONE, template="nope", confirm=True)


# ─────────────────────────────── سند و قرارداد ───────────────────────────────
def test_runbook_exists_and_points_at_real_commands() -> None:
    """رانبوک روز راه‌اندازی باید باشد و به فرمان‌های واقعی اشاره کند."""
    assert RUNBOOK.is_file(), "docs/runbooks/sms-golive.md نیست"
    text = RUNBOOK.read_text(encoding="utf-8")
    for needle in ("sms_golive.sh", "sms_selfcheck", "--rollback", "sms_patterns --check"):
        assert needle in text, f"«{needle}» در رانبوک نیست"
    assert "mahdie-shohada.ir" in (REPO_ROOT / "docs" / "sms" / "pattern-request.md").read_text(
        encoding="utf-8"
    )


def test_letter_asks_for_every_pattern_including_the_internal_ones() -> None:
    """نامه باید همهٔ الگوها را بخواهد: هیچ‌چیز پیش‌فرض «تأییدشده» فرض نمی‌شود."""
    from apps.notifications import pattern_texts

    letter = patterns.request_text()
    for draft in pattern_texts.DRAFTS:
        assert draft.key in letter, f"الگوی «{draft.key}» در نامه نیست"
    assert "پس از بالا آمدن دامنهٔ سایت" in letter
    assert sys.version_info >= (3, 12)  # محیط اجرای پشتیبانی‌شده (پایتون میزبان: 3.12)


def test_fixture_example_payload_matches_letter() -> None:
    """نمونهٔ درخواست پنل در رانبوک پیامک، با کد جاری هم‌خوان است (سند نمی‌پوسد)."""
    runbook = (REPO_ROOT / "docs" / "runbooks" / "sms-provider.md").read_text(encoding="utf-8")
    assert "IWoflaqY6M" not in runbook, "کد ردشدهٔ پنل در رانبوک مانده است"
    assert "⏳ در انتظار پنل" in runbook
    assert runbook.count("docs/sms/pattern-request.md") >= 1
