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
from pathlib import Path
import re
from typing import Final

from django.conf import settings
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
    scheduled = ("outbox_relay", "expire_holds")
    units = REPO_ROOT / "deploy" / "systemd"
    for command in scheduled:
        hits = [
            path.name
            for path in units.glob("*.service")
            if f"manage.py {command}" in path.read_text(encoding="utf-8")
        ]
        assert hits, f"دستور زمان‌بندی‌شدهٔ {command} هیچ واحد systemd ندارد"
    timers = {path.name for path in units.glob("*.timer")}
    assert {"mahdieh-holds.timer", "mahdieh-feedback.timer", "mahdieh-backup.timer"} <= timers


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
