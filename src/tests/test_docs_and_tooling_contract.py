"""قرارداد ابزارها و مستندات — «آنچه نوشته‌ایم، وجود دارد و کار می‌کند».

درس واقعی این فایل: در فاز ۸، هدف `make audit-verify` به دستوری اشاره می‌کرد
که **هرگز نوشته نشده بود**؛ یعنی Makefile به چیزی وعده داده بود که وجود نداشت و
کسی نمی‌فهمید تا لحظهٔ اجرا. همین کلاس خطا این‌جا بسته می‌شود: هدفِ بی‌دستور،
سندِ بی‌فایل، و وابستگیِ بی‌اعلام.

منابع «ارجاع معتبر» سه‌جاست و هر سه شمرده می‌شوند: مستندات، اسکریپت‌های
شل، و فایل‌های استقرار — چون ابزار می‌تواند از هر کدام صدا زده شود.
"""

from __future__ import annotations

from importlib.util import find_spec
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Final

from django.conf import settings
from django.test import override_settings
import pytest

REPO_ROOT: Final[Path] = Path(settings.BASE_DIR).parent
MAKEFILE: Final[Path] = REPO_ROOT / "Makefile"
MANAGE_REFERENCE: Final[re.Pattern[str]] = re.compile(r"\$\(MANAGE\)\s+([a-z_][a-z0-9_]*)")
SCRIPT_REFERENCE: Final[re.Pattern[str]] = re.compile(r"\bscripts/([a-z_][a-z0-9_]*\.(?:py|sh))")

# دستورهای درونی خود جنگو: وجودشان تضمین‌شده است و نباید «یتیم» شمرده شوند.
DJANGO_BUILTIN_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "check",
        "collectstatic",
        "createsuperuser",
        "dbshell",
        "makemigrations",
        "migrate",
        "runserver",
        "sendtestemail",
        "shell",
        "shell_plus",
        "spectacular",
        "test",
    }
)

REQUIRED_PACKAGES: Final[dict[str, str]] = {
    "gunicorn": "مدیر کارگرها در تولید",
    "uvicorn": "لایهٔ ASGI",
    "uvicorn_worker": "کارگر ASGI گونیکورن (جانشین uvicorn.workers حذف‌شده)",
    "whitenoise": "سرو دارایی ایستا با نسخه‌بندی و کش بلندمدت",
    "psycopg": "درایور PostgreSQL (استخر اتصال)",
}


def _reference_sources() -> list[Path]:
    """جاهایی که «ارجاع به ابزار» در آن‌ها معنا دارد: سند، اسکریپت، استقرار."""
    sources = list((REPO_ROOT / "docs").rglob("*.md"))
    sources += [REPO_ROOT / "README.md", REPO_ROOT / "CONTRIBUTING.md", MAKEFILE]
    sources += sorted(REPO_ROOT.glob("scripts/*.sh"))
    sources += sorted(REPO_ROOT.glob("deploy/**/*"))
    return [path for path in sources if path.is_file()]


def _referenced_text() -> str:
    """متن یکپارچهٔ همهٔ منابع ارجاع — برای جست‌وجوی نام ابزارها."""
    return "\n".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in _reference_sources()
    )


def _management_command_names() -> set[str]:
    """نام همهٔ دستورهای مدیریتی موجود در مخزن."""
    names: set[str] = set()
    for path in (REPO_ROOT / "src" / "apps").glob("*/management/commands/*.py"):
        if path.name != "__init__.py":
            names.add(path.stem)
    return names


def _documented_scripts() -> set[str]:
    """نام اسکریپت‌هایی که در مستندات به آن‌ها ارجاع شده است."""
    referenced: set[str] = set()
    sources = [*(REPO_ROOT / "docs").rglob("*.md"), REPO_ROOT / "README.md"]
    for path in sources:
        referenced |= set(SCRIPT_REFERENCE.findall(path.read_text(encoding="utf-8")))
    return referenced


def test_makefile_only_calls_existing_management_commands() -> None:
    """هر هدف Makefile که ``manage.py`` را صدا می‌زند، باید دستور واقعی داشته باشد."""
    available = _management_command_names() | DJANGO_BUILTIN_COMMANDS
    called = set(MANAGE_REFERENCE.findall(MAKEFILE.read_text(encoding="utf-8")))
    missing = sorted(called - available)
    assert not missing, f"دستورهای مدیریتی ناموجود در Makefile: {missing}"


def test_every_management_command_is_reachable_or_documented() -> None:
    """دستور مدیریتی نباید یتیم باشد: یا در Makefile، یا در سند، یا در استقرار."""
    reference = _referenced_text()
    orphans = [
        name
        for name in sorted(_management_command_names())
        if name not in reference and name not in DJANGO_BUILTIN_COMMANDS
    ]
    assert not orphans, f"دستورهای بی‌ارجاع (نه Makefile، نه سند، نه استقرار): {orphans}"


def test_scheduled_commands_have_a_timer() -> None:
    """دستورهایی که در تولید باید **زمان‌بندی** شوند، باید تایمر systemd داشته باشند.

    نمونهٔ واقعی: ``expire_holds`` سانس‌های آزادشده را به موتور نوبت‌انتظار
    خبر می‌دهد؛ بدون تایمر، سانس آزاد می‌شود و هیچ‌کس باخبر نمی‌شود.
    """
    scheduled = (
        "outbox_relay",
        "expire_holds",
        "send_feedback_requests",
        "send_booking_reminders",
        "retention_sweep",
    )
    units = REPO_ROOT / "deploy" / "systemd"
    for command in scheduled:
        hits = [
            path.name
            for path in units.glob("*.service")
            if f"manage.py {command}" in path.read_text(encoding="utf-8")
        ]
        assert hits, f"دستور زمان‌بندی‌شدهٔ {command} هیچ واحد systemd ندارد"
    timers = {path.name for path in units.glob("*.timer")}
    assert {
        "mahdieh-holds.timer",
        "mahdieh-feedback.timer",
        "mahdieh-reminders.timer",
        "mahdieh-backup.timer",
        "mahdieh-drill.timer",
        "mahdieh-retention.timer",
    } <= timers


def test_sms_pattern_request_document_matches_the_command() -> None:
    """برگ درخواست کد الگو، همان چیزی است که فرمان تولید می‌کند (سند نمی‌پوسد).

    چرا؟ این برگ را مدیر برای پشتیبانی پنل می‌فرستد؛ اگر با کاتالوگ فعلی نخواند،
    یعنی سند قدیمی به بیرون می‌رود. بازتولید:
    ``make sms-patterns ARGS="--request" > docs/sms/pattern-request.md``
    """
    from apps.notifications import patterns

    document = (REPO_ROOT / "docs" / "sms" / "pattern-request.md").read_text(encoding="utf-8")
    assert "```text\n" in document, "بلوک متن درخواست در سند نیست"
    embedded = document.split("```text\n", 1)[1].split("\n```", 1)[0]
    # برگ درخواست با **دامنهٔ تولید** ساخته می‌شود (پنل، الگو را پس از دامنه تأیید
    # می‌کند). پس آزمون هم همان دامنهٔ فایل محیط تولید را می‌گذارد تا سند، کد و
    # پیکربندی استقرار یک داستان بگویند.
    with override_settings(SITE_URL=production_site_url()):
        fresh = patterns.request_text()
    assert embedded.strip() == fresh.strip(), (
        'برگ درخواست کهنه است؛ با make sms-patterns ARGS="--request" بازتولید کنید'
    )


def production_site_url() -> str:
    """دامنهٔ تولید از فایل نمونهٔ محیط استقرار — تنها منبع حقیقت، نه حدس."""
    sample = (REPO_ROOT / "deploy" / "env" / "mahdieh.env.example").read_text(encoding="utf-8")
    for line in sample.splitlines():
        if line.startswith("MAHDIEH_SITE_URL="):
            return line.split("=", 1)[1].strip().rstrip("/")
    raise AssertionError("MAHDIEH_SITE_URL در deploy/env/mahdieh.env.example نیست")


def test_pattern_request_letter_carries_the_production_domain() -> None:
    """نامه باید دامنهٔ واقعیِ استقرار را نشان بدهد، نه یک جای‌نگهدار."""
    document = (REPO_ROOT / "docs" / "sms" / "pattern-request.md").read_text(encoding="utf-8")
    assert production_site_url() in document
    assert "⟨دامنهٔ سایت" not in document, "نامه با جای‌نگهدار دامنه فرستاده نشود"


def test_sms_pattern_preview_document_matches_the_command() -> None:
    """پیش‌نمایش «پیامی که زائر می‌گیرد» در برگ درخواست، با کاتالوگ می‌خواند.

    چرا؟ این پیش‌نمایش، سندِ بازبینی محتوایی است: کارفرما و کارکنان یک‌جا
    می‌بینند زائر چه می‌خواند، پیش از آنکه الگو در پنل ساخته شود. اگر با متن
    واقعی نخواند، یعنی کسی متنی را در پنل می‌سازد که با کد ما یکی نیست.
    """
    from apps.notifications import pattern_texts

    document = (REPO_ROOT / "docs" / "sms" / "pattern-request.md").read_text(encoding="utf-8")
    marker = "## پیش" + chr(0x200C) + "نمایش"
    assert marker in document, "بخش پیش‌نمایش در برگ درخواست نیست"
    block = document.split(marker, 1)[1].split("```text\n", 1)[1].split("\n```", 1)[0]
    assert block.strip() == pattern_texts.preview_text().strip(), (
        'پیش‌نمایش کهنه است؛ با make sms-patterns ARGS="--preview" بازتولید کنید'
    )


def test_documented_scripts_exist() -> None:
    """هر ``scripts/…`` که در مستندات آمده، باید در مخزن باشد — سندِ دروغ ممنوع."""
    missing = sorted(
        name for name in _documented_scripts() if not (REPO_ROOT / "scripts" / name).exists()
    )
    assert not missing, f"اسکریپت‌های ارجاع‌شده ولی ناموجود: {missing}"


def test_runbook_commands_are_executable_paths() -> None:
    """اسکریپت اجرایی باید بیت اجرا داشته باشد، یا با ``bash`` صدا زده شود."""
    documented = "\n".join(
        path.read_text(encoding="utf-8") for path in (REPO_ROOT / "docs").rglob("*.md")
    )
    offenders: list[str] = []
    for name in sorted(_documented_scripts()):
        path = REPO_ROOT / "scripts" / name
        if path.suffix != ".sh" or not path.exists():
            continue
        if not (path.stat().st_mode & 0o111) and f"bash scripts/{name}" not in documented:
            offenders.append(name)
    assert not offenders, f"اسکریپت بدون بیت اجرا و بدون پیشوند bash: {offenders}"


@pytest.mark.parametrize(("module", "why"), sorted(REQUIRED_PACKAGES.items()))
def test_deployment_dependencies_are_installed(module: str, why: str) -> None:
    """وابستگی‌های استقرار باید واقعاً نصب باشند — نه فقط در ذهن ما."""
    assert find_spec(module) is not None, f"{module} نصب نیست ({why})"


def test_deployment_dependencies_are_declared_in_pyproject() -> None:
    """همان وابستگی‌ها باید در ``pyproject.toml`` اعلام شده باشند (قفل‌شده در uv.lock)."""
    # نام توزیع ممکن است با خط تیره یا زیرخط نوشته شود (uvicorn-worker/uvicorn_worker).
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").replace("-", "_")
    undeclared = [package for package in REQUIRED_PACKAGES if f'"{package}' not in text]
    assert not undeclared, f"وابستگی اعلام‌نشده در pyproject: {undeclared}"


def test_deployment_command_flags_are_consistent() -> None:
    """دستور اجرا در تصویر و در systemd باید یک قرارداد باشند (timeout/بازیافت)."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    unit = (REPO_ROOT / "deploy" / "systemd" / "mahdieh.service").read_text(encoding="utf-8")
    for flag in ("--timeout 30", "--graceful-timeout 30", "--max-requests 2000"):
        assert flag in dockerfile, f"پرچم {flag} در Dockerfile نیست"
        assert flag in unit, f"پرچم {flag} در mahdieh.service نیست"


def test_ci_docker_job_asserts_entrypoint_effects() -> None:
    """کار «تصویر تولیدی» در CI باید *اثر* نقطهٔ ورود را شرط بگذارد، نه فقط بالا آمدن.

    چرا؟ یک بار واقعاً همین اتفاق افتاد: کانتینر بالا آمد ولی مهاجرت‌ها اجرا نشده
    بودند (نقطهٔ ورود صدا زده نمی‌شد). «سایت بالا آمد» شاهد کافی نیست؛ باید
    مهاجرت، جمع‌آوری دارایی ایستا و دروازهٔ انتشار هم دیده شوند.
    """
    text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "docker-image" in text, "کار تصویر تولیدی در CI وجود ندارد."
    for needle in ("اجرای مهاجرت", "جمع‌آوری دارایی", "دروازهٔ انتشار"):
        assert needle in text, f"آزمون اثر نقطهٔ ورود در CI، «{needle}» را شرط نکرده است."
    assert "health/ready/" in text and '"healthy"' in text, "CI آمادگی سالم را شرط نمی‌کند."


def test_precommit_hooks_use_the_project_environment() -> None:
    """قلاب‌های پیش از کامیت باید با *محیط و نسخهٔ پروژه* اجرا شوند، نه محیط بیگانه.

    دو دردسر واقعی که همین فاز رفع شد: (۱) قلاب mypy در محیط ایزوله، ماژول
    ``config`` را نمی‌شناخت و با ``ModuleNotFoundError`` می‌شکست؛ (۲) قلاب ruff با
    نسخهٔ قدیمی (۰٫۹) قواعدی را گزارش می‌کرد که نسخهٔ پروژه (۰٫۱۷) حذف کرده بود —
    یعنی «قلاب قرمز، CI سبز». این آزمون آن واگرایی را می‌بندد: نسخهٔ قلاب باید با
    نسخهٔ قفل‌شدهٔ مخزن (``uv.lock``) هم‌خوان باشد.
    """
    import tomllib

    text = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "uv run mypy" in text, "قلاب mypy از محیط پروژه استفاده نمی‌کند."

    pin = re.search(r"ruff-pre-commit\s*\n\s*rev:\s*v([\d.]+)", text)
    assert pin is not None, "قلاب ruff نسخهٔ قفل‌شده ندارد."

    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = next(p["version"] for p in lock["package"] if p["name"] == "ruff")
    assert pin.group(1) == locked, (
        f"نسخهٔ قلاب ruff ({pin.group(1)}) با نسخهٔ قفل‌شدهٔ مخزن ({locked}) یکی نیست."
    )


def test_no_migration_drift() -> None:
    """مدلِ بدون مهاجرت ممنوع — و این آزمون، دروازهٔ CI را به خودِ تست می‌آورد.

    چرا این‌جا و نه فقط در CI؟ تجربهٔ تلخ فاز ۱۱: افزودن یک گزینه به
    ``AuditAction`` (به‌عنوان متنِ choices) یک تغییر مدل است و بدون مهاجرت،
    شغل «بررسی جنگو و مهاجرت» قرمز می‌شود — ولی مجموعهٔ تست محلی سبز می‌ماند.
    حالا ``make test`` هم همان را می‌گیرد، نه فقط ``make django-check``.
    """
    result = subprocess.run(
        [sys.executable, "manage.py", "makemigrations", "--check", "--dry-run"],
        cwd=str(Path(settings.BASE_DIR)),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test"},
    )
    assert result.returncode == 0, (
        f"drift مهاجرت: مدلی تغییر کرده و مهاجرتش ساخته نشده است.\n{result.stdout}\n{result.stderr}"
    )
