"""قرارداد استقرار روی میزبان cPanel — پوستهٔ Passenger، زمان‌بند، و مجموعهٔ استقرار.

این آزمون‌ها ابزار استقرار را **رفتاری** می‌آزمایند، نه فقط وجود پرونده را:
پوستهٔ Passenger واقعاً ``sys.path`` را برای یک انتشار می‌چیند، ``cron_run.sh`` واقعاً
اجرای هم‌پوشان را رد و کار گیرکرده را قطع می‌کند، و ``deploy.sh`` بستهٔ دست‌کاری‌شده
را پیش از هر تغییری پس می‌زند. اجرای سرتاسری (بسته ⇐ محیط مجازی ⇐ مهاجرت ⇐ آزمون
درون‌فرآیندی ⇐ جابه‌جایی ⇐ بازگشت خودکار) در گردش‌کار انتشار CI انجام می‌شود.
"""

from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CPANEL = REPO_ROOT / "deploy" / "cpanel"
HAS_TOOLS = all(shutil.which(tool) for tool in ("bash", "flock", "timeout"))
needs_tools = pytest.mark.skipif(not HAS_TOOLS, reason="bash/flock/timeout در دسترس نیست")


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── manage.py: فایل محیط، تنها منبع ماژول تنظیمات ─────────────────────────────
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("DJANGO_SETTINGS_MODULE=config.settings.production\n", "config.settings.production"),
        (
            'export DJANGO_SETTINGS_MODULE="config.settings.production"\n',
            "config.settings.production",
        ),
        ("# DJANGO_SETTINGS_MODULE=x\nOTHER=1\n", None),
        ("DJANGO_SETTINGS_MODULE=\n", None),
        ("DJANGO_SETTINGS_MODULE=a\nDJANGO_SETTINGS_MODULE=b\n", "b"),
    ],
)
def test_manage_reads_settings_module_from_env_file(
    tmp_path: Path, content: str, expected: str | None
) -> None:
    manage = _load(REPO_ROOT / "src" / "manage.py", "mahdieh_manage")
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")
    assert manage.settings_module_from_env_file(env_file) == expected
    assert manage.settings_module_from_env_file(tmp_path / "missing") is None


# ── پوستهٔ Passenger ────────────────────────────────────────────────────────
def _fake_release(root: Path) -> Path:
    release = root / "releases" / "20261019000000-abc1234"
    version = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    packages = root / "venvs" / "k" / "lib" / version / "site-packages"
    packages.mkdir(parents=True)
    (packages / "mahdieh_probe_pkg.py").write_text("VALUE = 42\n", encoding="utf-8")
    (release / "src").mkdir(parents=True)
    (release / ".venv").symlink_to(root / "venvs" / "k")
    (root / "current").symlink_to(release)
    return release


def test_passenger_shim_pins_the_resolved_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAHDIEH_WSGI_NO_AUTOLOAD", "1")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delenv("MAHDIEH_RELEASE_DIR", raising=False)
    shim = _load(CPANEL / "passenger_wsgi.py", "mahdieh_shim_test")
    release = _fake_release(tmp_path)

    resolved = shim.activate(tmp_path / "current")

    assert resolved == release.resolve()
    assert sys.path[0] == str(release.resolve() / "src")
    assert str(shim.release_site_packages(release.resolve()).resolve()) in {
        str(Path(entry).resolve()) for entry in sys.path[1:3]
    }
    assert os.environ["MAHDIEH_RELEASE_DIR"] == str(release.resolve())
    probe = importlib.util.find_spec("mahdieh_probe_pkg")
    assert probe is not None


def test_passenger_shim_refuses_a_release_without_its_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAHDIEH_WSGI_NO_AUTOLOAD", "1")
    monkeypatch.setattr(sys, "path", list(sys.path))
    shim = _load(CPANEL / "passenger_wsgi.py", "mahdieh_shim_test2")
    (tmp_path / "r" / "src").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="محیط مجازی"):
        shim.activate(tmp_path / "r")


def test_passenger_shim_home_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAHDIEH_WSGI_NO_AUTOLOAD", "1")
    shim = _load(CPANEL / "passenger_wsgi.py", "mahdieh_shim_test3")
    monkeypatch.setenv("MAHDIEH_HOME", "/srv/elsewhere")
    assert shim.mahdieh_home() == Path("/srv/elsewhere")
    monkeypatch.delenv("MAHDIEH_HOME")
    assert shim.mahdieh_home().name == "mahdieh"


# ── زمان‌بند (crontab) ──────────────────────────────────────────────────────
CRON_REQUIRED = (
    "manage outbox_relay",
    "manage expire_holds",
    "manage send_booking_reminders",
    "manage send_feedback_requests",
    "manage healthcheck",
    "manage retention_sweep",
    "manage clearsessions",
    "manage sync_occasions",
    "scripts/db_backup.sh",
    "scripts/dr_drill.sh",
)


def test_crontab_template_schedules_every_production_job() -> None:
    text = (CPANEL / "crontab.tmpl").read_text(encoding="utf-8")
    jobs = [line for line in text.splitlines() if re.match(r"^[0-9*]", line)]
    for needle in CRON_REQUIRED:
        assert any(needle in job for job in jobs), f"کار «{needle}» در crontab نیست"
    names = [re.search(r"cron_run\.sh (\S+) (\d+) -- ", job) for job in jobs]
    assert all(names), "هر کار باید از cron_run.sh با نام و سقف زمان بگذرد"
    assert len({m.group(1) for m in names if m}) == len(jobs), "نام کارها باید یکتا باشد"
    assert text.splitlines()[0].startswith("# >>> mahdieh")
    assert text.rstrip().splitlines()[-1].startswith("# <<< mahdieh")
    assert set(re.findall(r"@([A-Z_]+)@", text)) == {"HOME", "SITE_URL", "DRILL_DB"}


def test_outbox_cron_loop_fits_inside_its_minute() -> None:
    """حلقهٔ رله باید پیش از اجرای دقیقهٔ بعد تمام شود؛ وگرنه flock آن را رد می‌کند."""
    text = (CPANEL / "crontab.tmpl").read_text(encoding="utf-8")
    line = next(job for job in text.splitlines() if "outbox_relay" in job)
    assert "--max-seconds 55" in line
    limit = int(re.search(r"cron_run\.sh outbox (\d+)", line).group(1))  # type: ignore[union-attr]
    assert 55 < limit < 120


def _cron_home(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    home = tmp_path / "mahdieh"
    release = home / "releases" / "r1"
    (release / "src").mkdir(parents=True)
    (release / "scripts").mkdir()
    (release / "scripts" / "nap.sh").write_text('#!/bin/bash\nsleep "$1"\necho done-$1\n')
    (release / "scripts" / "nap.sh").chmod(0o755)
    (home / "current").symlink_to(release)
    env = {**os.environ, "MAHDIEH_HOME": str(home)}
    return home, env


@needs_tools
def test_cron_run_skips_overlap_and_kills_stuck_jobs(tmp_path: Path) -> None:
    home, env = _cron_home(tmp_path)
    runner = str(CPANEL / "cron_run.sh")
    first = subprocess.Popen(  # noqa: S603
        [runner, "nap", "20", "--", "scripts/nap.sh", "2"], env=env
    )
    log = home / "shared" / "var" / "log" / "nap.log"
    for _ in range(50):
        if (home / "shared" / "var" / "locks" / "nap.lock").exists():
            break
        subprocess.run(["sleep", "0.1"], check=True)  # noqa: S607
    subprocess.run(["sleep", "0.3"], check=True)  # noqa: S607
    second = subprocess.run(  # noqa: S603
        [runner, "nap", "20", "--", "scripts/nap.sh", "0"], env=env, check=False
    )
    assert second.returncode == 0
    assert first.wait(timeout=30) == 0
    text = log.read_text(encoding="utf-8")
    assert "رد شد" in text
    assert "done-2" in text
    assert "done-0" not in text

    stuck = subprocess.run(  # noqa: S603
        [runner, "stuck", "1", "--", "scripts/nap.sh", "10"], env=env, check=False
    )
    assert stuck.returncode == 124
    assert "کد خروج 124" in (home / "shared" / "var" / "log" / "stuck.log").read_text("utf-8")


@needs_tools
def test_cron_run_rotates_large_logs_and_validates_input(tmp_path: Path) -> None:
    home, env = _cron_home(tmp_path)
    runner = str(CPANEL / "cron_run.sh")
    log_dir = home / "shared" / "var" / "log"
    log_dir.mkdir(parents=True)
    (log_dir / "nap.log").write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    subprocess.run([runner, "nap", "10", "--", "scripts/nap.sh", "0"], env=env, check=True)  # noqa: S603
    assert (log_dir / "nap.log.1").stat().st_size > 5 * 1024 * 1024
    assert (log_dir / "nap.log").read_text("utf-8").strip() == "done-0"
    bad = subprocess.run(  # noqa: S603
        [runner, "Bad Name", "10", "--", "x"], env=env, check=False, capture_output=True
    )
    assert bad.returncode == 2


# ── deploy.sh: بستهٔ مشکوک پیش از هر تغییری رد می‌شود ─────────────────────────
def _tarball(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


@needs_tools
@pytest.mark.parametrize(
    ("members", "reason"),
    [
        ({"r1/BUILD_INFO.json": b"{}", "r1/../../evil": b"x"}, "«..»"),
        ({"/abs/evil": b"x"}, "مسیر مطلق"),
        ({"r1/a": b"x", "r2/b": b"y"}, "یک پوشهٔ بالایی"),
    ],
)
def test_deploy_rejects_unsafe_bundles(
    tmp_path: Path, members: dict[str, bytes], reason: str
) -> None:
    bundle = _tarball(tmp_path / "bundle.tar.gz", members)
    home = tmp_path / "mahdieh"
    env = {
        **os.environ,
        "MAHDIEH_HOME": str(home),
        "MAHDIEH_BASE_PYTHON": sys.executable,
        "MAHDIEH_ENV_SOURCE": str(tmp_path / "none.env"),
    }
    result = subprocess.run(  # noqa: S603
        ["bash", str(CPANEL / "deploy.sh"), "local", str(bundle)],  # noqa: S607
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert reason in result.stderr
    assert not any((home / "releases").iterdir())
    assert not (home / "current").exists()
    assert not (tmp_path / "evil").exists()


@needs_tools
@pytest.mark.parametrize(
    "script",
    [
        "deploy/cpanel/deploy.sh",
        "deploy/cpanel/cron_run.sh",
        "deploy/cpanel/mahdieh.sh",
        "scripts/build_bundle.sh",
    ],
)
def test_deploy_scripts_parse_and_are_strict(script: str) -> None:
    path = REPO_ROOT / script
    subprocess.run(["bash", "-n", str(path)], check=True)  # noqa: S603, S607
    assert "set -Eeuo pipefail" in path.read_text(encoding="utf-8")


def test_deploy_switches_current_atomically_and_never_sources_env() -> None:
    text = (CPANEL / "deploy.sh").read_text(encoding="utf-8")
    assert 'mv -Tf "${HOME_DIR}/.current.next" "${CURRENT}"' in text
    assert "wsgi_probe.py" in text, "انتشار باید پیش از جابه‌جایی درون‌فرآیندی آزموده شود"
    assert "sha256sum --quiet -c" in text
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"(^|\s)(source|\.)\s+\S*env", code), "فایل محیط نباید source شود"


def test_bundle_excludes_tests_and_dev_dependencies() -> None:
    text = (REPO_ROOT / "scripts" / "build_bundle.sh").read_text(encoding="utf-8")
    assert "--exclude='src/tests'" in text
    assert "--no-dev" in text
    assert "--only-binary=:all:" in text
    assert "--extra server" not in text


HOST_SCRIPTS = (
    "deploy/cpanel/deploy.sh",
    "deploy/cpanel/cron_run.sh",
    "deploy/cpanel/mahdieh.sh",
    "scripts/_db_common.sh",
    "scripts/db_backup.sh",
    "scripts/db_restore.sh",
    "scripts/dr_drill.sh",
    "scripts/sms_golive.sh",
)


@pytest.mark.parametrize("script", HOST_SCRIPTS)
def test_host_scripts_avoid_process_substitution(script: str) -> None:
    """CageFS میزبان /dev/fd ندارد؛ «<(…)» و «>(…)» آنجا می‌شکنند (نخستین استقرار شکست)."""
    code = [
        line
        for line in (REPO_ROOT / script).read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    offending = [line for line in code if "<(" in line or ">(" in line]
    assert not offending, f"{script}: {offending}"


def test_web_server_can_traverse_the_passenger_app_root() -> None:
    """Passenger در Apache ریشهٔ برنامه را با کاربر وب‌سرور stat می‌کند؛ ۷۵۰ یعنی خطای ۵۰۰."""
    text = (REPO_ROOT / "deploy" / "cpanel" / "deploy.sh").read_text(encoding="utf-8")
    assert 'chmod 711 "${HOME_DIR}" "${APP}" "${APP}/tmp"' in text
    assert 'chmod 750 "${HOME_DIR}"' not in text
    assert "install -m 644" in text and "passenger_wsgi.py" in text


def test_rehearsal_runs_the_host_doctor_like_cpanel() -> None:
    text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "chmod 711 /home/mahdiesh" in text
    assert "mahdieh/bin/mahdieh doctor" in text
    assert "mahdieh version" in text


def test_rehearsal_removes_dev_fd_like_cagefs() -> None:
    text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "rm -f /dev/fd" in text


def test_release_workflow_builds_verifies_and_rehearses_the_bundle() -> None:
    text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    for needle in (
        "quay.io/pypa/manylinux_2_28_x86_64",
        "auditwheel repair",
        "scripts/build_bundle.sh",
        "almalinux:9",
        "deploy/cpanel/deploy.sh",
        "mahdieh-bundle.tar.gz.sha256",
        "mariadb:11.4",
    ):
        assert needle in text, f"گردش‌کار انتشار «{needle}» را ندارد"
    assert "workflow_run" in text, "بسته باید فقط پس از CI سبز ساخته شود"
    # کلاینت 3.1 مخزن AlmaLinux 8 با MariaDB 11.4 ناسازگار است (تمرین انتشار شکست خورد).
    assert "mariadb-connector-c-devel" not in text, "کلاینت MariaDB باید از منبعِ پین‌شده ساخته شود"
    assert 'MARIADB_CONNECTOR_C_VERSION: "3.4.' in text
    assert "sha256sum -c" in text, "منبع کلاینت باید با اثرانگشت ثابت راستی‌آزمایی شود"
    # DATABASE_URL با localhost ⇒ سوکت یونیکس؛ پیش‌فرض بالادست (/tmp/mysql.sock) روی میزبان نیست.
    assert "-DMARIADB_UNIX_ADDR=/var/lib/mysql/mysql.sock" in text
