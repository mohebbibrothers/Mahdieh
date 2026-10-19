#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# ساخت «بستهٔ انتشار» برای میزبان cPanel — یک فایل، خودبسنده، قابل راستی‌آزمایی.
#
# چرا بسته؟ میزبان به PyPI دسترسی ندارد ولی دریافت از GitHub Release کار می‌کند.
# پس همه‌چیز (کد + چرخ‌های پایتون ۳.۱۲ برای manylinux x86_64) یک‌جا ساخته و با
# sha256 منتشر می‌شود؛ میزبان فقط دانلود، راستی‌آزمایی و نصب آفلاین می‌کند.
#
# محتوای بسته (پوشهٔ بالایی = شناسهٔ انتشار):
#   src/ scripts/ deploy/ pyproject.toml uv.lock   ← از git archive (بدون آزمون و سند)
#   requirements.lock                              ← وابستگی‌های تولید (بدون dev/server)
#   wheelhouse/                                    ← چرخ‌ها + sdist mysqlclient (پشتیبان)
#   BUILD_INFO.json                                ← شناسه، کامیت، کلید محیط مجازی
#
# استفاده:
#   scripts/build_bundle.sh [--out DIR] [--release ID] [--mysqlclient-wheels DIR] [--tree T]
#   متغیرها: BUNDLE_PIP_PYTHON (مفسر ۳.۱۲ دارای pip؛ پیش‌فرض python3.12)
# ══════════════════════════════════════════════════════════════════════════════
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

OUT="dist"
RELEASE=""
MYSQLCLIENT_WHEELS=""
TREEISH="HEAD"
TARGET_PY="3.12"
PIP_PY="${BUNDLE_PIP_PYTHON:-python3.12}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --release) RELEASE="$2"; shift 2 ;;
    --mysqlclient-wheels) MYSQLCLIENT_WHEELS="$2"; shift 2 ;;
    --tree) TREEISH="$2"; shift 2 ;;   # آزمون محلی: درخت کار بی‌کامیت (git write-tree)
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "گزینهٔ ناشناخته: $1" >&2; exit 2 ;;
  esac
done

log() { printf '▸ %s\n' "$*"; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }

command -v uv >/dev/null || die "uv در دسترس نیست"
"${PIP_PY}" -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version" \
  || die "BUNDLE_PIP_PYTHON باید پایتون ۳.۱۲ باشد (نشانگرهای محیطی وابستگی‌ها با همین ارزیابی می‌شوند)"
"${PIP_PY}" -m pip --version >/dev/null || die "pip در ${PIP_PY} نیست"

COMMIT="$(git rev-parse HEAD)"
SHORT="$(git rev-parse --short=7 HEAD)"
[[ -n "${RELEASE}" ]] || RELEASE="$(date -u +%Y%m%d%H%M%S)-${SHORT}"
[[ "${RELEASE}" =~ ^[A-Za-z0-9._-]+$ ]] || die "شناسهٔ انتشار نامعتبر: ${RELEASE}"
if [[ "${TREEISH}" == "HEAD" ]] && ! git diff --quiet HEAD -- src scripts deploy pyproject.toml uv.lock 2>/dev/null; then
  log "⚠ تغییر کامیت‌نشده هست — بسته فقط از HEAD (${SHORT}) ساخته می‌شود"
fi

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
STAGE="${WORK}/${RELEASE}"
mkdir -p "${STAGE}/wheelhouse" "${OUT}"

log "کد از ${TREEISH} (${SHORT})"
git archive --format=tar "${TREEISH}" -- src scripts deploy pyproject.toml uv.lock README.md LICENSE \
  | tar -x -C "${STAGE}" --exclude='src/tests' --exclude='*/__pycache__'

log "فهرست وابستگی‌های تولید (قفل‌شده، بدون dev و server)"
uv export --frozen --no-dev --no-hashes --no-emit-project --no-header \
  --output-file "${STAGE}/requirements.lock" >/dev/null
MYSQLCLIENT_PIN="$(grep -E '^mysqlclient==' "${STAGE}/requirements.lock" | cut -d' ' -f1)"
[[ -n "${MYSQLCLIENT_PIN}" ]] || die "mysqlclient در فهرست قفل نیست"
grep -vE '^mysqlclient==' "${STAGE}/requirements.lock" > "${WORK}/binary-reqs.txt"

log "چرخ‌های باینری cp312 · manylinux x86_64 (بدون کامپایل روی میزبان)"
"${PIP_PY}" -m pip download --quiet --disable-pip-version-check \
  --only-binary=:all: --implementation cp --python-version "${TARGET_PY}" \
  --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 \
  --platform manylinux2014_x86_64 \
  -r "${WORK}/binary-reqs.txt" -d "${STAGE}/wheelhouse"

log "پشتیبانِ ساخت: sdist ${MYSQLCLIENT_PIN} + setuptools/wheel"
"${PIP_PY}" -m pip download --quiet --disable-pip-version-check --no-deps \
  --no-binary=:all: "${MYSQLCLIENT_PIN}" -d "${STAGE}/wheelhouse"
"${PIP_PY}" -m pip download --quiet --disable-pip-version-check --only-binary=:all: \
  --implementation py --python-version "${TARGET_PY}" "setuptools>=75" "wheel>=0.44" \
  -d "${STAGE}/wheelhouse"

if [[ -n "${MYSQLCLIENT_WHEELS}" ]]; then
  shopt -s nullglob
  wheels=("${MYSQLCLIENT_WHEELS}"/mysqlclient-*-cp312-cp312-manylinux*_x86_64.whl)
  shopt -u nullglob
  [[ ${#wheels[@]} -eq 1 ]] || die "دقیقاً یک چرخ manylinux mysqlclient انتظار می‌رفت (${#wheels[@]} یافت شد)"
  cp "${wheels[0]}" "${STAGE}/wheelhouse/"
  log "چرخ آمادهٔ mysqlclient: $(basename "${wheels[0]}")"
else
  log "⚠ چرخ آمادهٔ mysqlclient داده نشد — میزبان از sdist می‌سازد (نیازمند gcc و سرآیندها)"
fi

# کلید محیط مجازی: تا وقتی فهرست وابستگی و نسخهٔ مفسر عوض نشده، استقرارها محیط
# مجازی را بازمصرف می‌کنند (سریع، و بی‌هزینهٔ دیسک روی سهمیهٔ ۱ گیگ).
VENV_KEY="$(cat "${STAGE}/requirements.lock" <(printf 'cp%s' "${TARGET_PY}") \
  <(ls "${STAGE}/wheelhouse" | grep -E '^mysqlclient-.*\.whl$' || true) \
  | sha256sum | cut -c1-16)"

"${PIP_PY}" - "${STAGE}/BUILD_INFO.json" "${RELEASE}" "${COMMIT}" "${VENV_KEY}" "${TARGET_PY}" <<'PY'
import json, os, sys
from datetime import UTC, datetime
path, release, commit, venv_key, python = sys.argv[1:6]
info = {
    "schema_version": 1,
    "release": release,
    "commit": commit,
    "ref": os.environ.get("GITHUB_REF_NAME", ""),
    "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "python": python,
    "platform": "manylinux_x86_64",
    "venv_key": venv_key,
    "ci_run": os.environ.get("GITHUB_RUN_ID", ""),
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(info, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY

log "بسته‌بندی و اثرانگشت"
TARBALL="${OUT}/mahdieh-bundle.tar.gz"
tar -C "${WORK}" --owner=0 --group=0 --numeric-owner -czf "${TARBALL}" "${RELEASE}"
( cd "${OUT}" && sha256sum mahdieh-bundle.tar.gz > mahdieh-bundle.tar.gz.sha256 )
cp "${STAGE}/BUILD_INFO.json" "${OUT}/BUILD_INFO.json"

COUNT="$(find "${STAGE}/wheelhouse" -type f | wc -l)"
SIZE="$(du -h "${TARBALL}" | cut -f1)"
log "✅ ${TARBALL} (${SIZE}) · انتشار ${RELEASE} · ${COUNT} بستهٔ پایتون · کلید محیط ${VENV_KEY}"
