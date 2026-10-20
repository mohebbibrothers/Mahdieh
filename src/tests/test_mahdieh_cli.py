"""فرمان یکپارچهٔ میزبان «mahdieh» — رفتاری، روی هاست و GitHub ساختگی.

یک سرور HTTP محلی هم نقش GitHub (دارایی انتشار + API) را بازی می‌کند و هم نقش دامنهٔ
سایت (``/health/*``)؛ هاست ساختگی همان چیدمان ``~/mahdieh`` است و ``deploy.sh`` آن فقط
آرگومان‌هایش را ثبت می‌کند. پس «صبر برای ساختِ در جریان»، «توقف با شکست CI»،
«تنزل آرام با سقف نرخ GitHub» و «معاینهٔ دسترسی‌ها» واقعاً اجرا می‌شوند.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import threading
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "deploy" / "cpanel" / "mahdieh_cli.py"
REPO = "mohebbibrothers/Mahdieh"
OLD, NEW = "a" * 40, "b" * 40
OLD_ID, NEW_ID = "20261019130010-aaaaaaa", "20261020090000-bbbbbbb"


# ── GitHub + دامنهٔ ساختگی ─────────────────────────────────────────────────────


class FakeWeb:
    def __init__(self) -> None:
        self.routes: dict[str, Callable[[], tuple[int, Any, dict[str, str]]]] = {}
        self.hits: list[str] = []
        self.hosts: list[str] = []

    def set(
        self, path: str, body: Any, status: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self.routes[path] = lambda: (status, body, headers or {})

    def handler(self) -> type[BaseHTTPRequestHandler]:
        web = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                web.hits.append(self.path)
                web.hosts.append(self.headers.get("Host", ""))
                route = web.routes.get(self.path)
                status, body, headers = route() if route else (404, {"message": "Not Found"}, {})
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: Any) -> None:
                del args

        return Handler


@pytest.fixture
def web() -> Iterator[tuple[FakeWeb, str]]:
    fake = FakeWeb()
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


# ── هاست ساختگی ──────────────────────────────────────────────────────────────


def _release(home: Path, release: str, commit: str) -> Path:
    root = home / "mahdieh" / "releases" / release
    (root / ".venv").mkdir(parents=True)
    (root / ".venv" / ".complete").touch()
    (root / "deploy" / "cpanel").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "deploy" / "cpanel" / "crontab.tmpl", root / "deploy" / "cpanel")
    info = {"release": release, "commit": commit, "built_at": "2026-10-19T13:00:10Z"}
    (root / "BUILD_INFO.json").write_text(json.dumps(info), encoding="utf-8")
    return root


@pytest.fixture
def host(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    base = home / "mahdieh"
    _release(home, OLD_ID, OLD)
    (base / "current").symlink_to(f"releases/{OLD_ID}")
    (base / "app" / "tmp").mkdir(parents=True)
    (base / "app" / "passenger_wsgi.py").write_text("# shim\n", encoding="utf-8")
    (base / "shared" / "var" / "log").mkdir(parents=True)
    (base / "shared" / "var" / "backups").mkdir(parents=True)
    (base / "shared" / "var" / "backups" / "nightly.sql.gz").write_bytes(b"x" * 2048)
    (base / "shared" / "var" / "backups" / "nightly.sql.gz.json").write_text("{}", encoding="utf-8")
    (base / "bin").mkdir()
    deploy = base / "bin" / "deploy.sh"
    deploy.write_text(
        '#!/usr/bin/env bash\necho "$*" >> "$(dirname "$0")/calls"\nexit "${FAKE_DEPLOY_CODE:-0}"\n',
        encoding="utf-8",
    )
    deploy.chmod(0o755)
    for path in (home, base, base / "app", base / "app" / "tmp"):
        path.chmod(0o711)
    (base / "app" / "passenger_wsgi.py").chmod(0o644)
    secrets = home / ".mahdieh"
    secrets.mkdir()
    (secrets / "mahdieh.env").write_text(
        "MAHDIEH_SITE_URL=https://mahdieh.test\n", encoding="utf-8"
    )
    (secrets / "mahdieh.env").chmod(0o600)
    tools = tmp_path / "tools"
    tools.mkdir()
    jobs = sum(
        1
        for line in (REPO_ROOT / "deploy/cpanel/crontab.tmpl").read_text().splitlines()
        if line[:1] in "0123456789*"
    )
    crontab = tools / "crontab"
    crontab.write_text(
        "#!/usr/bin/env bash\n"
        + "".join(f"echo '* * * * * cron_run.sh job{i}'\n" for i in range(jobs)),
        encoding="utf-8",
    )
    crontab.chmod(0o755)
    return home


@pytest.fixture
def cli(
    host: Path, web: tuple[FakeWeb, str], monkeypatch: pytest.MonkeyPatch
) -> Callable[..., tuple[int, str]]:
    fake, base = web
    monkeypatch.setenv("HOME", str(host))
    monkeypatch.setenv("PATH", f"{host.parent / 'tools'}{os.pathsep}{os.environ['PATH']}")
    for key in ("MAHDIEH_HOME", "MAHDIEH_FRONT_HOME", "MAHDIEH_ENV_SOURCE", "MAHDIEH_OFFLINE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAHDIEH_GITHUB_WEB", base)
    monkeypatch.setenv("MAHDIEH_GITHUB_API", base)
    monkeypatch.setenv("MAHDIEH_VERIFY_BASE_URL", base)
    monkeypatch.setenv("MAHDIEH_POLL_SECONDS", "0.01")
    monkeypatch.setenv("MAHDIEH_API_EVERY_SECONDS", "0")
    monkeypatch.setenv("MAHDIEH_WAIT_SECONDS", "20")
    fake.set("/health/live/", {"status": "alive", "release": OLD_ID})
    fake.set(
        "/health/ready/",
        {
            "status": "healthy",
            "checks": {"database": {"latency_ms": 4.9}, "migrations": {"pending": 0}},
        },
    )

    def run(*argv: str) -> tuple[int, str]:
        module = load_cli()
        out = io.StringIO()
        code = module.main(list(argv), module.Ui(out))
        return code, out.getvalue()

    return run


def load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mahdieh_cli_under_test", CLI)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass‌ها ماژول را از sys.modules می‌خوانند
    spec.loader.exec_module(module)
    return module


def calls(host: Path) -> list[str]:
    path = host / "mahdieh" / "bin" / "calls"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def publish(fake: FakeWeb, release: str, commit: str) -> None:
    fake.set(
        f"/{REPO}/releases/download/edge/BUILD_INFO.json", {"release": release, "commit": commit}
    )


# ── update ───────────────────────────────────────────────────────────────────


def test_update_is_a_noop_when_the_latest_release_is_active(cli: Any, host: Path, web: Any) -> None:
    fake, _ = web
    publish(fake, OLD_ID, OLD)
    fake.set(f"/repos/{REPO}/commits/main", {"sha": OLD})
    code, out = cli("update")
    assert code == 0
    assert "به‌روز است" in out
    assert "هنوز راه‌اندازی نشده" in out  # فرانت تا مرحلهٔ ۲ آرام رد می‌شود
    assert calls(host) == []


def test_update_waits_for_the_inflight_build_lists_changes_and_deploys(
    cli: Any, host: Path, web: Any
) -> None:
    fake, _ = web
    publish(fake, OLD_ID, OLD)
    fake.set(f"/repos/{REPO}/commits/main", {"sha": NEW})
    fake.set(
        f"/repos/{REPO}/actions/runs?head_sha={NEW}&per_page=30",
        {
            "workflow_runs": [
                {"name": "Release bundle", "status": "in_progress", "conclusion": None}
            ]
        },
    )
    fake.set(
        f"/repos/{REPO}/compare/{OLD}...{NEW}",
        {
            "total_commits": 2,
            "commits": [
                {"commit": {"message": "feat: one\n\nbody"}},
                {"commit": {"message": "fix: two"}},
            ],
        },
    )
    polls = {"n": 0}

    def build_info() -> tuple[int, Any, dict[str, str]]:
        polls["n"] += 1
        commit, release = (NEW, NEW_ID) if polls["n"] > 3 else (OLD, OLD_ID)
        return 200, {"release": release, "commit": commit}, {}

    fake.routes[f"/{REPO}/releases/download/edge/BUILD_INFO.json"] = build_info
    code, out = cli("update", "back")
    assert code == 0, out
    assert "صبر می‌کنم" in out
    assert "منتشر شد" in out
    assert "fix: two" in out
    assert "feat: one" in out
    assert "body" not in out  # فقط سطر نخست پیام کامیت
    assert calls(host) == ["fetch --tag edge"]
    assert "به‌روز شد" in out


def test_update_stops_waiting_when_github_rejects_the_change(
    cli: Any, host: Path, web: Any
) -> None:
    fake, _ = web
    publish(fake, OLD_ID, OLD)
    fake.set(f"/repos/{REPO}/commits/main", {"sha": NEW})
    fake.set(
        f"/repos/{REPO}/actions/runs?head_sha={NEW}&per_page=30",
        {"workflow_runs": [{"name": "CI", "status": "completed", "conclusion": "failure"}]},
    )
    code, out = cli("update", "back")
    assert code == 0
    assert "رد شد" in out
    assert "به‌روز است" in out
    assert calls(host) == []


def test_update_degrades_gracefully_when_the_api_is_rate_limited(
    cli: Any, host: Path, web: Any
) -> None:
    fake, _ = web
    publish(fake, NEW_ID, NEW)
    fake.set(
        f"/repos/{REPO}/commits/main",
        {"message": "rate limited"},
        403,
        {"X-RateLimit-Remaining": "0"},
    )
    code, out = cli("update", "back")
    assert code == 0, out
    assert "سقف درخواست" in out
    assert calls(host) == ["fetch --tag edge"]
    compare_hits = [hit for hit in fake.hits if "/compare/" in hit]
    assert compare_hits == []  # پس از سقف، API دیگر صدا زده نمی‌شود


def test_update_check_only_reports(cli: Any, host: Path, web: Any) -> None:
    fake, _ = web
    publish(fake, NEW_ID, NEW)
    code, out = cli("update", "back", "--check", "--no-wait")
    assert code == 0
    assert NEW_ID in out
    assert "mahdieh update" in out
    assert calls(host) == []


def test_update_reports_a_failed_deploy(
    cli: Any, host: Path, web: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, _ = web
    publish(fake, NEW_ID, NEW)
    monkeypatch.setenv("FAKE_DEPLOY_CODE", "1")
    code, out = cli("update", "back", "--no-wait")
    assert code == 1
    assert "نسخهٔ قبلی سالم" in out
    assert "mahdieh logs deploy" in out


def test_update_reinstalls_a_release_left_over_from_a_rollback(
    cli: Any, host: Path, web: Any
) -> None:
    fake, _ = web
    _release(host, NEW_ID, NEW)
    publish(fake, NEW_ID, NEW)
    code, _ = cli("update", "back", "--no-wait")
    assert code == 0
    assert calls(host) == ["fetch --tag edge --force"]


def test_update_never_downgrades_to_an_older_published_release(
    cli: Any, host: Path, web: Any
) -> None:
    fake, _ = web
    publish(fake, "20261001000000-ccccccc", "c" * 40)
    code, out = cli("update", "back", "--no-wait")
    assert code == 0
    assert "تازه‌تر است" in out
    assert calls(host) == []
    _, out = cli("doctor")
    assert "آماده است" not in out


def test_update_without_github_fails_clearly(cli: Any, host: Path) -> None:
    code, out = cli("update", "back", "--no-wait")
    assert code == 1
    assert "GitHub" in out
    assert calls(host) == []


def test_invalid_input_is_reported_in_persian(cli: Any) -> None:
    code, out = cli("bogus")
    assert code == 2
    assert "نامعتبر" in out
    assert "invalid choice" not in out
    code, _ = cli("update", "--tag", "../etc")
    assert code == 2


# ── doctor / status ──────────────────────────────────────────────────────────


def test_doctor_is_green_on_a_healthy_host(cli: Any, web: Any) -> None:
    fake, _ = web
    publish(fake, OLD_ID, OLD)
    code, out = cli("doctor")
    assert code == 0, out
    assert "همه‌چیز سالم است" in out
    assert "mahdieh.test" in web[0].hosts  # بررسی با هدر Host دامنهٔ واقعی


def test_doctor_catches_a_web_server_that_cannot_traverse_the_app_root(
    cli: Any, host: Path
) -> None:
    (host / "mahdieh").chmod(0o750)  # همان ایراد نخستین استقرار
    try:
        code, out = cli("doctor")
    finally:
        (host / "mahdieh").chmod(0o711)
    assert code == 1
    assert "دسترسی وب‌سرور" in out
    assert "ایراد" in out


def test_doctor_catches_a_stale_release_and_open_secrets(cli: Any, host: Path, web: Any) -> None:
    fake, _ = web
    fake.set("/health/live/", {"status": "alive", "release": "something-else"})
    (host / ".mahdieh" / "mahdieh.env").chmod(0o644)
    code, out = cli("doctor")
    assert code == 1
    assert "something-else" in out
    assert "۶۰۰" in out


def test_doctor_reports_recent_job_failures(cli: Any, host: Path) -> None:
    from datetime import UTC, datetime
    import time

    stamp = datetime.fromtimestamp(time.time(), tz=UTC).isoformat()
    log = host / "mahdieh" / "shared" / "var" / "log" / "backup.log"
    log.write_text(f"{stamp} [backup] ✗ کد خروج 1 پس از 3s (انتشار x)\n", encoding="utf-8")
    _, out = cli("doctor")
    assert "خطای کارها" in out
    assert "backup" in out


def test_doctor_reports_the_dump_not_its_newer_manifest(cli: Any, host: Path) -> None:
    """درس واقعی: doctor اندازهٔ بیانیهٔ ۱٫۶KB را به‌جای پشتیبان ۱۸KB نشان می‌داد."""
    import time

    backups = host / "mahdieh" / "shared" / "var" / "backups"
    for old in backups.iterdir():
        old.unlink()
    dump = backups / "mahdieh-20261019T212304Z-pre-bbc39b0.sql.gz"
    dump.write_bytes(b"x" * 18 * 1024)
    manifest = backups / f"{dump.name}.json"
    manifest.write_text("{" + " " * 1600 + "}", encoding="utf-8")
    now = time.time()
    os.utime(dump, (now - 10, now - 10))
    os.utime(manifest, (now, now))  # بیانیه همیشه کمی جدیدتر است
    _, out = cli("doctor")
    line = next(row for row in out.splitlines() if "آخرین پشتیبان" in row)
    assert "۱۸" in line, line
    assert "۱٫۶" not in line, line


def test_doctor_warns_when_only_a_manifest_or_only_a_dump_remains(cli: Any, host: Path) -> None:
    backups = host / "mahdieh" / "shared" / "var" / "backups"
    for old in backups.iterdir():
        old.unlink()
    (backups / "lost.sql.gz.json").write_text("{}", encoding="utf-8")
    _, out = cli("doctor")
    assert "هنوز پشتیبانی نیست" in out  # بیانیهٔ تنها، پشتیبان نیست
    (backups / "orphan.sql.gz").write_bytes(b"x" * 4096)
    _, out = cli("doctor")
    assert "بیانیهٔ اثرانگشت (.json) ندارد" in out


def test_status_dashboard(cli: Any, web: Any) -> None:
    fake, _ = web
    publish(fake, NEW_ID, NEW)
    code, out = cli()
    assert code == 0, out
    assert "داشبورد" in out
    assert NEW_ID in out  # نسخهٔ تازه اعلام می‌شود
    assert "mahdieh update" in out


def test_help_and_version(cli: Any) -> None:
    code, out = cli("help")
    assert code == 0
    assert "mahdieh update" in out
    code, out = cli("version")
    assert code == 0
    assert OLD_ID in out


def test_rollback_front_is_a_friendly_noop_until_installed(cli: Any) -> None:
    code, out = cli("rollback", "front")
    assert code == 0
    assert "mahdieh update back" in out  # راه نصب ابزار فرانت را دقیق می‌گوید


# ── فرانت (ADR-0022) ─────────────────────────────────────────────────────────

FRONT_REPO = "mohebbibrothers/Mahdieh-Front"
FRONT_OLD_ID, FRONT_NEW_ID = "20261020100000-ccccccc", "20261020110000-ddddddd"


def _front_tools(host: Path) -> Path:
    """همان چیزی که install_tools بک‌اند می‌گذارد: ‎~/mahdieh-front/bin/deploy.sh."""
    base = host / "mahdieh-front"
    (base / "bin").mkdir(parents=True)
    deploy = base / "bin" / "deploy.sh"
    deploy.write_text(
        '#!/usr/bin/env bash\necho "$*" >> "$(dirname "$0")/calls"\n', encoding="utf-8"
    )
    deploy.chmod(0o755)
    return base


def _front_release(base: Path, release: str) -> None:
    root = base / "releases" / release
    root.mkdir(parents=True)
    (root / "routes.json").write_text('{"version": 1}', encoding="utf-8")
    info = {
        "app": "mahdieh-front",
        "release": release,
        "commit": "c" * 40,
        "built_at": "2026-10-20T10:00:00Z",
    }
    (root / "BUILD_INFO.json").write_text(json.dumps(info), encoding="utf-8")
    current = base / "current"
    current.unlink(missing_ok=True)
    current.symlink_to(f"releases/{release}")


def front_calls(host: Path) -> list[str]:
    path = host / "mahdieh-front" / "bin" / "calls"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_update_front_performs_the_first_install(cli: Any, host: Path, web: Any) -> None:
    """ابزار نصب است ولی current نیست ⇒ نصب نخست، نه «رد شد»."""
    fake, _ = web
    _front_tools(host)
    fake.set(
        f"/{FRONT_REPO}/releases/download/edge/BUILD_INFO.json",
        {"release": FRONT_NEW_ID, "commit": "d" * 40},
    )
    code, out = cli("update", "front", "--no-wait")
    assert code == 0, out
    assert "نصب نخست" in out
    assert front_calls(host) == ["fetch --tag edge"]
    assert calls(host) == []  # بک‌اند دست نخورد


def test_doctor_checks_the_served_front_release(cli: Any, host: Path, web: Any) -> None:
    fake, _ = web
    base = _front_tools(host)
    _front_release(base, FRONT_OLD_ID)
    fake.set("/health/live/", {"status": "alive", "release": OLD_ID, "front": FRONT_OLD_ID})
    code, out = cli("doctor")
    assert "فرانت · انتشار فعال" in out
    assert "دامنه همان انتشار فعال فرانت را سرو می‌کند" in out
    assert code == 0, out
    fake.set("/health/live/", {"status": "alive", "release": OLD_ID, "front": None})
    code, out = cli("doctor")
    assert code == 1
    assert "فرانت «هیچ»" in out


def test_doctor_flags_a_backend_that_predates_the_front(cli: Any, host: Path) -> None:
    _front_release(_front_tools(host), FRONT_OLD_ID)
    code, out = cli("doctor")  # /health/live/ ساختگی کلید front ندارد
    assert "mahdieh update back" in out
    assert code == 0  # هشدار، نه ایراد


def test_update_all_hands_off_to_the_freshly_installed_cli(
    cli: Any, host: Path, web: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """بک‌اند فرمان تازه‌ای نصب کرد ⇒ بخش فرانت با همان فرمان تازه ادامه می‌یابد (execv)."""
    fake, _ = web
    publish(fake, NEW_ID, NEW)
    fake.set(f"/repos/{REPO}/commits/main", {"sha": NEW})
    installed = host / "mahdieh" / "bin" / "mahdieh_cli.py"
    installed.write_text("# old\n", encoding="utf-8")
    deploy = host / "mahdieh" / "bin" / "deploy.sh"
    deploy.write_text(
        '#!/usr/bin/env bash\necho "$*" >> "$(dirname "$0")/calls"\necho "# new cli" >> "$(dirname "$0")/mahdieh_cli.py"\n',
        encoding="utf-8",
    )
    seen: list[list[str]] = []

    def fake_execv(path: str, argv: list[str]) -> None:
        seen.append([path, *argv])
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", fake_execv)
    with pytest.raises(SystemExit):
        cli("update", "--no-wait")
    assert calls(host) == ["fetch --tag edge"]
    assert seen
    assert seen[0][2:] == [str(installed), "update", "front", "--tag", "edge", "--no-wait"]


# ── قرارداد نصب ──────────────────────────────────────────────────────────────


def test_deploy_installs_the_cli_and_a_managed_path_block() -> None:
    text = (REPO_ROOT / "deploy" / "cpanel" / "deploy.sh").read_text(encoding="utf-8")
    assert "mahdieh_cli.py:mahdieh_cli.py:640" in text
    assert "mahdieh.sh:mahdieh:750" in text
    assert "# >>> mahdieh cli" in text
    assert "# <<< mahdieh cli <<<" in text
    assert 'cmp -s "${rc}.mahdieh-next" "${rc}"' in text  # بی‌تغییر ⇒ ‎.bashrc دست نمی‌خورد


def test_cli_uses_only_the_standard_library() -> None:
    import ast

    tree = ast.parse(CLI.read_text(encoding="utf-8"))
    roots = {
        (node.module or "").split(".")[0]
        if isinstance(node, ast.ImportFrom)
        else alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    roots.discard("__future__")
    assert roots <= set(sys.stdlib_module_names), roots - set(sys.stdlib_module_names)
