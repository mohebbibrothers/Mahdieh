"""دریافت مقاوم بستهٔ انتشار روی اینترنت ناپایدار میزبان (``download_asset`` در deploy.sh).

درس واقعی (گام ۸ب): روی میزبان، curl پس از ۴ دقیقه با کد ۱۸ (قطع وسط انتقال) متوقف
شد. ``--retry`` خودِ curl این خطا را تکرار نمی‌کند و هر بار از صفر شروع می‌کند. این
آزمون‌ها تابع واقعی deploy.sh را با یک curl ساختگی می‌رانند که اتصال را قطع می‌کند.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy" / "cpanel" / "deploy.sh"

# curl ساختگی: هر فراخوانی، تکهٔ بعدی را می‌نویسد. «-C -» باید باشد وگرنه از صفر می‌نویسد.
FAKE_CURL = r"""#!/usr/bin/env bash
dest="" resume=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) dest="$2"; shift 2 ;;
    -C) resume=1; shift 2 ;;
    *) shift ;;
  esac
done
n=$(( $(cat "$STATE/count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STATE/count"
echo "resume=$resume" >> "$STATE/calls"
chunk="$(sed -n "${n}p" "$STATE/plan")"
code="${chunk%%:*}"; data="${chunk#*:}"
if [[ "$resume" == 1 ]]; then printf '%s' "$data" >> "$dest"; else printf '%s' "$data" > "$dest"; fi
exit "$code"
"""


def _harness(
    tmp_path: Path, plan: list[str], attempts: int = 8
) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash
    state = tmp_path / "state"
    state.mkdir()
    (state / "plan").write_text("\n".join(plan) + "\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(FAKE_CURL, encoding="utf-8")
    curl.chmod(0o755)
    source = DEPLOY.read_text(encoding="utf-8")
    function = re.search(r"^download_asset\(\) \{.*?^\}\n", source, re.DOTALL | re.MULTILINE)
    assert function, "download_asset در deploy.sh پیدا نشد"
    script = "\n".join(
        [
            "set -Eeuo pipefail",
            'die() { echo "DIE: $*" >&2; exit 1; }',
            'warn() { echo "WARN: $*" >&2; }',
            "sleep() { :; }",
            f"DOWNLOAD_ATTEMPTS={attempts}",
            function.group(0),
            f'download_asset "https://example.invalid/b.tgz" "{tmp_path}/b.tgz"',
            "echo DONE",
        ]
    )
    return subprocess.run(  # noqa: S603 — اسکریپت آزمون خودمان
        [bash, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "STATE": str(state)},
        timeout=30,
    )


def test_resumes_after_a_mid_transfer_drop(tmp_path: Path) -> None:
    result = _harness(tmp_path, ["18:AAAA", "18:BBBB", "0:CCCC"])
    assert result.returncode == 0, result.stderr
    assert "DONE" in result.stdout
    assert (tmp_path / "b.tgz").read_text() == "AAAABBBBCCCC"
    calls = (tmp_path / "state" / "calls").read_text().split()
    assert calls == ["resume=1"] * 3  # هر تلاش ادامه می‌دهد، نه از صفر
    assert "تلاش 2 از 8" in result.stderr
    assert "تلاش 3 از 8" in result.stderr


def test_gives_up_with_a_clear_message_after_the_attempt_budget(tmp_path: Path) -> None:
    result = _harness(tmp_path, ["18:A", "18:B", "18:C"], attempts=3)
    assert result.returncode == 1
    assert "DONE" not in result.stdout
    assert "DIE: دریافت b.tgz پس از 3 تلاش ناموفق ماند (کد curl 18)" in result.stderr


def test_range_not_supported_restarts_from_scratch(tmp_path: Path) -> None:
    result = _harness(tmp_path, ["18:JUNK", "33:", "0:FULL"])
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "b.tgz").read_text() == "FULL"


def test_fetch_uses_the_resilient_downloader_without_a_total_timeout() -> None:
    source = DEPLOY.read_text(encoding="utf-8")
    fetch = re.search(r"^cmd_fetch\(\) \{.*?^\}\n", source, re.DOTALL | re.MULTILINE)
    assert fetch
    body = fetch.group(0)
    assert body.count("download_asset ") >= 2
    assert "--max-time" not in body  # روی خط کند، بستهٔ سالم را می‌کُشت
    assert "sha256sum" in body
    downloader = re.search(r"^download_asset\(\) \{.*?^\}\n", source, re.DOTALL | re.MULTILINE)
    assert downloader
    assert "-C -" in downloader.group(0)
    assert "--speed-limit" in downloader.group(0)
