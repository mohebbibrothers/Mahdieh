"""استقرار اتمی فرانت (``deploy/cpanel/front_deploy.sh``) — رفتاری، روی هاست ساختگی.

یک سرور HTTP محلی نقش دامنه را بازی می‌کند و **مثل جنگو** (apps.core.frontend) انتشار
فعال را از مقصد پیوند ``current`` می‌خواند؛ پس جابه‌جایی اتمی، راستی‌آزمایی بیرونی،
بازگشت خودکار و ردّ بستهٔ دست‌کاری‌شده واقعاً اجرا می‌شوند، نه شبیه‌سازی.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import threading
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "cpanel" / "front_deploy.sh"
BASH = shutil.which("bash") or "/bin/bash"
HAS_TOOLS = all(shutil.which(tool) for tool in ("bash", "flock", "tar", "sha256sum", "curl"))
pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(not HAS_TOOLS, reason="ابزار پوسته در دسترس نیست"),
]

R1, R2, R3, R4 = (
    "20261020100000-aaaaaaa",
    "20261020110000-bbbbbbb",
    "20261020120000-ccccccc",
    "20261020130000-ddddddd",
)


class FakeSite:
    """دامنهٔ ساختگی: ‎/health/live/ و سرآیند X-Mahdieh-Front از پیوند current."""

    def __init__(self, front_home: Path) -> None:
        self.front_home = front_home
        self.knows_front = True
        self.broken_release: str | None = None  # این انتشار را «نمی‌بیند» (شکست راستی‌آزمایی)

    def serving(self) -> str | None:
        current = self.front_home / "current"
        if not current.is_symlink():
            return None
        release = Path(os.path.realpath(current)).name
        return None if release == self.broken_release else release

    def handler(self) -> type[BaseHTTPRequestHandler]:
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                release = site.serving()
                if self.path == "/health/live/":
                    body: dict[str, Any] = {"status": "alive", "release": "back"}
                    if site.knows_front:
                        body["front"] = release
                    payload = json.dumps(body).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                else:
                    payload = b"<!doctype html>"
                    self.send_response(200)
                    if release:
                        self.send_header("X-Mahdieh-Front", release)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: Any) -> None:
                del args

        return Handler


@pytest.fixture
def host(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".mahdieh").mkdir(parents=True)
    env_file = home / ".mahdieh" / "mahdieh.env"
    env_file.write_text("MAHDIEH_SITE_URL=https://mahdieh.test\n", encoding="utf-8")
    env_file.chmod(0o600)
    return home


@pytest.fixture
def site(host: Path) -> Iterator[tuple[FakeSite, str]]:
    fake = FakeSite(host / "mahdieh-front")
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def bundle(
    out: Path,
    release: str,
    *,
    tamper: bool = False,
    mutate: Callable[[Path], None] | None = None,
    dynamic: dict[str, str] | None = None,
    pages: tuple[str, ...] = (),
) -> Path:
    """بسته‌ای با همان قرارداد postbuild (BUILD_INFO.files = هش تک‌تک پرونده‌ها)."""
    stage = out / f"stage-{release}" / release
    (stage / "pages").mkdir(parents=True)
    (stage / "public" / "_next" / "static").mkdir(parents=True)
    (stage / "pages" / "index.html").write_text(
        f"<html nonce=__CSP_NONCE__>{release}</html>", encoding="utf-8"
    )
    for rel in pages:
        (stage / "pages" / rel).parent.mkdir(parents=True, exist_ok=True)
        (stage / "pages" / rel).write_text("<main>unit</main>", encoding="utf-8")
    (stage / "public" / "_next" / "static" / "app.js").write_text("1", encoding="utf-8")
    manifest: dict[str, Any] = {
        "version": 1,
        "nonce_placeholder": "__CSP_NONCE__",
        "routes": {"/": "index.html"},
    }
    if dynamic is not None:
        manifest["dynamic"] = dynamic
    (stage / "routes.json").write_text(json.dumps(manifest), encoding="utf-8")
    files = {
        str(p.relative_to(stage)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(stage.rglob("*"))
        if p.is_file()
    }
    info = {
        "app": "mahdieh-front",
        "release": release,
        "commit": "f" * 40,
        "next": "16.4.2",
        "files": files,
    }
    (stage / "BUILD_INFO.json").write_text(json.dumps(info), encoding="utf-8")
    if tamper:
        (stage / "public" / "_next" / "static" / "app.js").write_text("evil()", encoding="utf-8")
    if mutate:
        mutate(stage)
    tarball = out / f"{release}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(stage, arcname=release)
    return tarball


@pytest.fixture
def run(host: Path, site: tuple[FakeSite, str]) -> Callable[..., subprocess.CompletedProcess[str]]:
    _, base = site

    def go(*argv: str) -> subprocess.CompletedProcess[str]:
        env = {
            "HOME": str(host),
            "PATH": os.environ["PATH"],
            "MAHDIEH_BASE_PYTHON": sys.executable,
            "MAHDIEH_VERIFY_BASE_URL": base,
            "MAHDIEH_VERIFY_SECONDS": "3",
            "NO_COLOR": "1",
        }
        # خروجی فرمان‌های استقرار از tee می‌گذرد (stderr در stdout)؛ آزمون‌ها همان را می‌خوانند.
        return subprocess.run(  # noqa: S603 — اسکریپت مخزن، آرگومان‌های آزمون
            [BASH, str(SCRIPT), *argv],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    return go


def current(host: Path) -> str | None:
    link = host / "mahdieh-front" / "current"
    return Path(os.path.realpath(link)).name if link.is_symlink() else None


def test_first_install_switches_verifies_and_logs(run: Any, host: Path, tmp_path: Path) -> None:
    result = run("local", str(bundle(tmp_path, R1)))
    assert result.returncode == 0, result.stdout
    assert current(host) == R1
    assert "یکپارچگی: 3 پرونده با هش" in result.stdout
    assert f"فرانت {R1} فعال است" in result.stdout
    assert (host / "mahdieh-front" / "log" / "deploy.log").read_text(encoding="utf-8").count(
        R1
    ) >= 2
    assert oct((host / "mahdieh-front").stat().st_mode & 0o777) == "0o700"


def test_dynamic_unit_pages_are_validated_and_counted(run: Any, host: Path, tmp_path: Path) -> None:
    """الگوی پویای صفحهٔ واحد: قالبش باید در بسته باشد؛ شمارش در خروجی دیده می‌شود."""
    unit_pages = {"/units/[code]/": "units/_/index.html"}
    missing = run("local", str(bundle(tmp_path, R1, dynamic=unit_pages)))
    assert missing.returncode == 1
    assert "صفحهٔ مسیر نیست: pages/units/_/index.html" in missing.stdout
    assert current(host) is None
    bad = run("local", str(bundle(tmp_path, R2, dynamic={"/units/": "index.html"})))
    assert bad.returncode == 1
    assert "الگوی مسیر پویا نامعتبر" in bad.stdout
    ok = run(
        "local",
        str(bundle(tmp_path, R3, dynamic=unit_pages, pages=("units/_/index.html",))),
    )
    assert ok.returncode == 0, ok.stdout
    assert "1 مسیر + 1 الگوی پویا" in ok.stdout
    assert current(host) == R3


def test_tampered_bundle_is_rejected_before_any_change(
    run: Any, host: Path, tmp_path: Path
) -> None:
    assert run("local", str(bundle(tmp_path, R1))).returncode == 0
    result = run("local", str(bundle(tmp_path, R2, tamper=True)))
    assert result.returncode == 1
    assert "هش نمی‌خواند" in result.stdout
    assert current(host) == R1
    assert not (host / "mahdieh-front" / "releases" / R2).exists()  # بستهٔ رد شده پاک شد


def test_unlisted_files_are_refused(run: Any, host: Path, tmp_path: Path) -> None:
    """پروندهٔ بیرون از فهرست هش (مثلاً ‎.br‎ جعلی که سرور ترجیحاً می‌دهد) ⇒ رد کل بسته."""

    def add_precompressed(stage: Path) -> None:
        (stage / "public" / "_next" / "static" / "app.js.br").write_bytes(b"evil")

    assert run("local", str(bundle(tmp_path, R1))).returncode == 0
    result = run("local", str(bundle(tmp_path, R2, mutate=add_precompressed)))
    assert result.returncode == 1
    assert "پروندهٔ بی‌هش" in result.stdout
    assert "app.js.br" in result.stdout
    assert current(host) == R1
    assert not (host / "mahdieh-front" / "releases" / R2).exists()


def test_symlinks_inside_the_bundle_are_refused(run: Any, host: Path, tmp_path: Path) -> None:
    def add_link(stage: Path) -> None:
        (stage / "public" / "passwd").symlink_to("/etc/passwd")

    result = run("local", str(bundle(tmp_path, R1, mutate=add_link)))
    assert result.returncode == 1
    assert "پیوند نمادین" in result.stdout
    assert current(host) is None


def test_symlink_is_refused_even_in_a_large_bundle(run: Any, host: Path, tmp_path: Path) -> None:
    """پیوند در ابتدای فهرستی بزرگ: «tar | grep -q» زیر pipefail این را گاهی رد نمی‌کرد (SIGPIPE)."""

    def add_link_then_bulk(stage: Path) -> None:
        (stage / "public" / "a-link").symlink_to("/etc/passwd")
        bulk = stage / "public" / "zz-bulk"
        bulk.mkdir()
        for index in range(4000):
            (bulk / f"chunk-{index:04d}.txt").write_text("x", encoding="utf-8")

    result = run("local", str(bundle(tmp_path, R1, mutate=add_link_then_bulk)))
    assert result.returncode == 1
    assert "پیوند نمادین" in result.stdout
    assert current(host) is None


def test_backend_without_front_support_is_refused(
    run: Any, host: Path, tmp_path: Path, site: tuple[FakeSite, str]
) -> None:
    site[0].knows_front = False
    result = run("local", str(bundle(tmp_path, R1)))
    assert result.returncode == 1
    assert "mahdieh update back" in result.stdout
    assert current(host) is None


def test_failed_verification_rolls_back_automatically(
    run: Any, host: Path, tmp_path: Path, site: tuple[FakeSite, str]
) -> None:
    assert run("local", str(bundle(tmp_path, R1))).returncode == 0
    site[0].broken_release = R2
    result = run("local", str(bundle(tmp_path, R2)))
    assert result.returncode == 1
    assert f"بازگشت خودکار به {R1}" in result.stdout
    assert current(host) == R1


def test_failed_first_install_leaves_legacy_pages(
    run: Any, host: Path, tmp_path: Path, site: tuple[FakeSite, str]
) -> None:
    site[0].broken_release = R1
    result = run("local", str(bundle(tmp_path, R1)))
    assert result.returncode == 1
    assert current(host) is None  # جنگو صفحه‌های قدیمی را سرو می‌کند


def test_rollback_and_pruning(run: Any, host: Path, tmp_path: Path) -> None:
    for release in (R1, R2, R3, R4):
        assert run("local", str(bundle(tmp_path, release))).returncode == 0
    releases = sorted(p.name for p in (host / "mahdieh-front" / "releases").iterdir())
    assert releases == [R2, R3, R4]  # KEEP=3
    result = run("rollback")
    assert result.returncode == 0, result.stderr
    assert current(host) == R3
    assert run("rollback", R4).returncode == 0
    assert current(host) == R4
    status = run("status")
    assert f"● {R4}" in status.stdout


def test_active_release_cannot_be_overwritten(run: Any, tmp_path: Path) -> None:
    tarball = bundle(tmp_path, R1)
    assert run("local", str(tarball)).returncode == 0
    result = run("local", str(tarball), "--force")
    assert result.returncode == 1
    assert "هم‌اکنون فعال است" in result.stdout


def test_backend_deploy_installs_the_front_tool() -> None:
    script = (REPO_ROOT / "deploy" / "cpanel" / "deploy.sh").read_text(encoding="utf-8")
    assert 'deploy/cpanel/front_deploy.sh" "${front_home}/bin/deploy.sh.next"' in script
    assert os.access(SCRIPT, os.X_OK)


def test_script_reads_the_postbuild_contract_keys() -> None:
    """کلیدهایی که اسکریپت می‌خواند، همان‌هایی‌اند که postbuild فرانت می‌نویسد."""
    text = SCRIPT.read_text(encoding="utf-8")
    for key in ('info.get("files")', 'info.get("app")', "nonce_placeholder", "x-mahdieh-front"):
        assert key in text
