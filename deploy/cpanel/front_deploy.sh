#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# استقرار اتمی فرانت مهدیه (Next.js ایستا) روی میزبان cPanel — ADR-0022
#
#   deploy.sh fetch [--tag edge|vX.Y.Z] [--force]  دریافت بسته از GitHub Release ⇒ activate
#   deploy.sh local BUNDLE.tar.gz [--force]        همان، از پروندهٔ محلی
#   deploy.sh rollback [RELEASE_ID]                بازگشت به انتشار قبلی (یا داده‌شده)
#   deploy.sh status                               انتشار فعال، انتشارها، فضای دیسک
#
# نصب: deploy.sh بک‌اند این پرونده را در ‎~/mahdieh-front/bin/deploy.sh می‌گذارد.
# کار روزمره: «mahdieh update front» · «mahdieh rollback front».
#
# فرانت پروسهٔ جدا ندارد: جنگو (apps.core.frontend) پوشهٔ ‎~/mahdieh-front/current را
# می‌خواند و با هر درخواست مقصد پیوند را می‌سنجد ⇒ جابه‌جایی پیوند = استقرار، بی‌بازنشانی
# Passenger و بی‌قطعی. مراحل — شکستِ پیش از «جابه‌جایی» یعنی سایت دست‌نخورده:
#   ۱ دریافت مقاوم + اثرانگشت sha256 بسته
#   ۲ بازکردن امن (یک پوشهٔ بالایی، بی‌مسیر مطلق/«..»، بی‌پیوند نمادین)
#   ۳ یکپارچگی: هش sha256 تک‌تک پرونده‌ها با BUILD_INFO.json + قرارداد routes.json
#   ۴ پیش‌نیاز: بک‌اند فعال، فرانت را می‌شناسد (کلید front در ‎/health/live/)
#   ۵ جابه‌جایی اتمی current
#   ۶ راستی‌آزمایی بیرونی (HTTPS): ‎/health/live/ و سرآیند X-Mahdieh-Front صفحهٔ اصلی
#     — شکست ⇒ بازگشت خودکار به انتشار قبلی
#   ۷ هرس انتشارهای قدیمی (نگه‌داشت KEEP تا، به‌علاوهٔ فعال و قبلی)
# ══════════════════════════════════════════════════════════════════════════════
set -Eeuo pipefail
umask 077

HOME_DIR="${MAHDIEH_FRONT_HOME:-${HOME}/mahdieh-front}"
REPO="${MAHDIEH_FRONT_REPO:-mohebbibrothers/Mahdieh-Front}"
BASE_PY="${MAHDIEH_BASE_PYTHON:-/opt/alt/python312/bin/python3}"
ENV_SOURCE="${MAHDIEH_ENV_SOURCE:-${HOME}/.mahdieh/mahdieh.env}"
KEEP="${MAHDIEH_KEEP_RELEASES:-3}"
RESOLVE_IP="${MAHDIEH_VERIFY_RESOLVE:-}"
VERIFY_SECONDS="${MAHDIEH_VERIFY_SECONDS:-60}"
# فقط برای آزمون بیرون از میزبان: نشانی پایهٔ جایگزین (HTTP پشت پروکسی)
VERIFY_BASE_URL="${MAHDIEH_VERIFY_BASE_URL:-}"
GITHUB_WEB="${MAHDIEH_GITHUB_WEB:-https://github.com}"
BUNDLE="mahdieh-front.tar.gz"

RELEASES="${HOME_DIR}/releases"
BIN="${HOME_DIR}/bin"
CURRENT="${HOME_DIR}/current"
DOWNLOADS="${HOME_DIR}/downloads"
LOG_DIR="${HOME_DIR}/log"

# رنگ فقط روی ترمینال و بی NO_COLOR (https://no-color.org)؛ گزارش deploy.log تمیز می‌ماند.
if [[ -t 1 && -z "${NO_COLOR:-}" ]] || [[ "${MAHDIEH_FRONT_COLOR:-0}" == "1" && -z "${NO_COLOR:-}" ]]; then
  c_ok=$'\033[1;32m'; c_bad=$'\033[1;31m'; c_step=$'\033[1;36m'; c_dim=$'\033[0;90m'; c_off=$'\033[0m'
else
  c_ok=""; c_bad=""; c_step=""; c_dim=""; c_off=""
fi
step() { printf '%s▸ %s%s\n' "${c_step}" "$*" "${c_off}"; }
ok()   { printf '  %s✓%s %s\n' "${c_ok}" "${c_off}" "$*"; }
info() { printf '  %s•%s %s\n' "${c_dim}" "${c_off}" "$*"; }
warn() { printf '  %s!%s %s\n' "${c_bad}" "${c_off}" "$*" >&2; }
die()  { printf '%s✗ %s%s\n' "${c_bad}" "$*" "${c_off}" >&2; exit 1; }

on_error() {
  local code=$? line=$1
  (( BASH_SUBSHELL == 0 )) || return "${code}"
  printf '%s✗ استقرار فرانت در خط %s با کد %s متوقف شد.%s\n' "${c_bad}" "${line}" "${code}" "${c_off}" >&2
  if [[ "${SWITCHED:-0}" != "1" ]]; then
    printf '  سایت دست‌نخورده است: current هنوز به انتشار قبلی اشاره می‌کند.\n' >&2
  fi
}
trap 'on_error $LINENO' ERR

ensure_layout() {
  mkdir -p "${RELEASES}" "${BIN}" "${DOWNLOADS}" "${LOG_DIR}"
  # فقط فرآیند جنگو (همین حساب کاربری، زیر Passenger) این‌ها را می‌خواند؛ وب‌سرور نه.
  chmod 700 "${HOME_DIR}" "${RELEASES}" "${DOWNLOADS}" "${LOG_DIR}"
}

take_lock() {
  exec 8>"${HOME_DIR}/.deploy.lock"
  flock -n 8 || die "استقرار دیگری از فرانت در جریان است (${HOME_DIR}/.deploy.lock)."
}

# خروجی در log/deploy.log هم می‌نشیند؛ بی‌جایگزینی فرآیند (CageFS میزبان ‎/dev/fd ندارد).
run_logged() {
  ensure_layout
  trap - ERR
  set +e
  local colour=0
  [[ -t 1 ]] && colour=1
  MAHDIEH_FRONT_LOGGED=1 MAHDIEH_FRONT_COLOR="${colour}" bash "${BASH_SOURCE[0]}" "$@" 2>&1 \
    | tee -a "${LOG_DIR}/deploy.log"
  exit "${PIPESTATUS[0]}"
}

json_field() {  # json_field FILE KEY
  "${BASE_PY}" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get(sys.argv[2], ""))' "$1" "$2"
}

current_release() { [[ -L "${CURRENT}" ]] && basename "$(readlink -f "${CURRENT}")" || true; }

site_url() {  # از فایل محیط بک‌اند، بی source کردن آن
  "${BASE_PY}" - "${ENV_SOURCE}" <<'PY'
import re, sys
url = ""
for raw in open(sys.argv[1], encoding="utf-8"):
    m = re.match(r"\s*MAHDIEH_SITE_URL\s*=\s*(.*)$", raw)
    if m:
        url = m.group(1).strip().strip("'\"")
print(url.rstrip("/"))
PY
}

http_args() {  # آرایهٔ مشترک curl برای دامنه (یا جایگزین آزمون)
  HTTP_ARGS=(-fsS --max-time 15 -H 'Cache-Control: no-cache')
  if [[ -n "${VERIFY_BASE_URL}" ]]; then
    HTTP_BASE="${VERIFY_BASE_URL}"
    HTTP_ARGS+=(-H 'X-Forwarded-Proto: https' -H "Host: ${DOMAIN}")
  else
    HTTP_BASE="${SITE_URL}"
    HTTP_ARGS+=(--proto '=https')
    [[ -z "${RESOLVE_IP}" ]] || HTTP_ARGS+=(--resolve "${DOMAIN}:443:${RESOLVE_IP}")
  fi
}

live_field() {  # live_field KEY ⇒ مقدار از ‎/health/live/ (کلید نبود ⇒ «__absent__»)
  local body
  body="$(curl "${HTTP_ARGS[@]}" "${HTTP_BASE}/health/live/" 2>/dev/null)" || return 1
  "${BASE_PY}" -c '
import json, sys
data = json.loads(sys.argv[1])
key = sys.argv[2]
print("__absent__" if key not in data else (data[key] or ""))' "${body}" "$1"
}

home_release() {  # سرآیند X-Mahdieh-Front صفحهٔ اصلی
  curl "${HTTP_ARGS[@]}" -o /dev/null -D - "${HTTP_BASE}/" 2>/dev/null \
    | tr -d '\r' | awk -F': ' 'tolower($1)=="x-mahdieh-front"{print $2}' | tail -n1
}

# ── دریافت مقاوم (هم‌سان deploy.sh بک‌اند؛ اینترنت بین‌الملل کند/ناپایدار) ─────────
DOWNLOAD_ATTEMPTS="${MAHDIEH_DOWNLOAD_ATTEMPTS:-8}"
download_asset() {  # download_asset URL DEST
  local url="$1" dest="$2" attempt=1 rc have
  while :; do
    rc=0
    curl -fsSL --proto '=https' --connect-timeout 20 --speed-limit 1024 --speed-time 60 \
      -C - -o "${dest}" "${url}" || rc=$?
    case "${rc}" in
      0) return 0 ;;
      33) rm -f "${dest}" ;;
      22) if [[ -s "${dest}" ]] && curl -sSIL --proto '=https' --connect-timeout 20 \
              -r "$(stat -c %s "${dest}")-" "${url}" 2>/dev/null | grep -q '^HTTP/[0-9.]* 416'; then
            return 0
          fi ;;
    esac
    (( attempt >= DOWNLOAD_ATTEMPTS )) && die "دریافت $(basename "${dest}") پس از ${attempt} تلاش ناموفق ماند (کد curl ${rc})."
    have="$(stat -c %s "${dest}" 2>/dev/null || echo 0)"
    warn "اتصال قطع شد (کد ${rc}) — تلاش $((attempt + 1)) از ${DOWNLOAD_ATTEMPTS}، ادامه از $((have / 1024)) KB"
    attempt=$((attempt + 1))
    sleep $((attempt * 2))
  done
}

unpack_bundle() {  # unpack_bundle TARBALL FORCE ⇒ مسیر انتشار روی stdout
  local tarball="$1" force="$2" top entries
  entries="$(tar -tzf "${tarball}")" || die "بسته خراب است یا gzip نیست."
  if grep -qE '^/|(^|/)\.\.(/|$)' <<<"${entries}"; then
    die "بسته مسیر مطلق یا «..» دارد — رد شد."
  fi
  # فهرست اول کامل خوانده می‌شود: «tar | grep -q» با pipefail مسابقه دارد (grep زود می‌بندد،
  # tar با SIGPIPE می‌میرد و شرط «نادرست» می‌شود ⇒ پیوند نمادین گاهی رد نمی‌شد).
  local listing
  listing="$(tar -tvzf "${tarball}")" || die "بسته خراب است یا gzip نیست."
  if grep -qE '^[lhcbp]' <<<"${listing}"; then
    die "بسته پیوند نمادین/سخت یا پروندهٔ ویژه دارد — رد شد."
  fi
  top="$(cut -d/ -f1 <<<"${entries}" | sort -u)"
  [[ "$(wc -l <<<"${top}")" == "1" && "${top}" =~ ^[0-9]{14}-[0-9a-f]{7}$ ]] \
    || die "بسته باید دقیقاً یک پوشهٔ بالایی به نام شناسهٔ انتشار داشته باشد."
  if [[ -d "${RELEASES}/${top}" ]]; then
    [[ "${force}" == "1" ]] || die "انتشار ${top} از قبل هست (برای بازسازی: --force)."
    [[ "$(current_release)" != "${top}" ]] || die "انتشار ${top} هم‌اکنون فعال است؛ بازنویسی‌اش مجاز نیست."
    rm -rf "${RELEASES:?}/${top}"
  fi
  tar -xzf "${tarball}" -C "${RELEASES}" --no-same-owner --no-same-permissions
  printf '%s\n' "${RELEASES}/${top}"
}

verify_bundle() {  # verify_bundle RELEASE_DIR — قرارداد postbuild + هش تک‌تک پرونده‌ها
  "${BASE_PY}" - "$1" <<'PY'
import hashlib, json, re, sys
from pathlib import Path

root = Path(sys.argv[1])
def fail(message):
    print(f"  ✗ {message}", file=sys.stderr)
    sys.exit(1)

try:
    info = json.loads((root / "BUILD_INFO.json").read_text(encoding="utf-8"))
    routes = json.loads((root / "routes.json").read_text(encoding="utf-8"))
except (OSError, ValueError) as error:
    fail(f"BUILD_INFO.json/routes.json خوانده نشد: {error}")
if info.get("app") != "mahdieh-front":
    fail(f"این بستهٔ فرانت مهدیه نیست (app={info.get('app')!r})")
if info.get("release") != root.name:
    fail(f"شناسهٔ BUILD_INFO ({info.get('release')}) با نام پوشه ({root.name}) نمی‌خواند")
if routes.get("version") != 1 or "/" not in (routes.get("routes") or {}):
    fail("routes.json نسخهٔ ۱ با مسیر «/» نیست")
if not re.fullmatch(r"__[A-Z_]{4,40}__", str(routes.get("nonce_placeholder", ""))):
    fail("نشانگر nonce در routes.json نامعتبر است")
files = info.get("files")
if not isinstance(files, dict) or not files:
    fail("فهرست هش پرونده‌ها در BUILD_INFO.json نیست")
for rel, digest in files.items():
    path = (root / rel).resolve()
    if not path.is_relative_to(root.resolve()):
        fail(f"مسیر بیرون‌زده در فهرست: {rel}")
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        fail(f"پرونده نیست: {rel}")
    if actual != digest:
        fail(f"هش نمی‌خواند (دست‌کاری یا خرابی): {rel}")
# برعکس هم: هر پروندهٔ بی‌هش (مثلاً ‎.br‎ جعلی که سرور به‌جای اصل می‌دهد) ⇒ رد.
listed = set(files) | {"BUILD_INFO.json"}
extra = sorted(
    p.relative_to(root).as_posix() for p in root.rglob("*")
    if p.is_file() and p.relative_to(root).as_posix() not in listed
)
if extra:
    fail(f"پروندهٔ بی‌هش در بسته ({len(extra)}): {', '.join(extra[:3])}")
for rel in routes["routes"].values():
    if not (root / "pages" / rel).is_file():
        fail(f"صفحهٔ مسیر نیست: pages/{rel}")
print(f"  ✓ یکپارچگی: {len(files)} پرونده با هش · {len(routes['routes'])} مسیر · Next {info.get('next', '—')}")
PY
}

switch_to() {  # switch_to RELEASE_ID — rename اتمی پیوند
  ln -sfn "releases/$1" "${HOME_DIR}/.current.next"
  mv -Tf "${HOME_DIR}/.current.next" "${CURRENT}"
}

verify_live() {  # verify_live RELEASE_ID ⇒ ۰ اگر دامنه همین انتشار را سرو کند
  local expect="$1" deadline=$(( $(date +%s) + VERIFY_SECONDS )) seen="" header=""
  while (( $(date +%s) < deadline )); do
    seen="$(live_field front 2>/dev/null || true)"
    if [[ "${seen}" == "${expect}" ]]; then
      header="$(home_release || true)"
      [[ "${header}" == "${expect}" ]] && return 0
    fi
    sleep 2
  done
  printf '  آخرین شناسهٔ دیده‌شده: سلامت «%s» · صفحهٔ اصلی «%s»\n' "${seen}" "${header}" >&2
  return 1
}

prune() {  # prune PREVIOUS — نگه‌داشت KEEP جدیدترین + فعال + قبلی
  local current previous="${1:-}" kept=0 r listing all=()
  current="$(current_release)"
  listing="$(find "${RELEASES}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r)"
  [[ -z "${listing}" ]] || mapfile -t all <<<"${listing}"
  for r in "${all[@]}"; do
    if [[ "${r}" == "${current}" || "${r}" == "${previous}" ]] || (( kept < KEEP )); then
      kept=$((kept + 1))
    else
      rm -rf "${RELEASES:?}/${r}"; info "هرس انتشار ${r}"
    fi
  done
}

prepare_site() {
  [[ -f "${ENV_SOURCE}" ]] || die "فایل محیط بک‌اند نیست: ${ENV_SOURCE}"
  SITE_URL="$(site_url)"
  [[ "${SITE_URL}" =~ ^https://[A-Za-z0-9.-]+$ ]] || die "MAHDIEH_SITE_URL باید https://دامنه باشد (اکنون: «${SITE_URL}»)."
  DOMAIN="${SITE_URL#https://}"
  http_args
}

# ── فرمان‌ها ──────────────────────────────────────────────────────────────────
cmd_fetch() {
  local tag="edge" force=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tag) tag="${2:?}"; shift 2 ;;
      --force) force=1; shift ;;
      *) die "گزینهٔ ناشناخته: $1" ;;
    esac
  done
  [[ "${tag}" =~ ^[A-Za-z0-9._-]+$ ]] || die "برچسب نامعتبر: ${tag}"
  ensure_layout; take_lock
  local base="${GITHUB_WEB}/${REPO}/releases/download/${tag}"
  step "۱/۷ دریافت بستهٔ فرانت «${tag}» از GitHub"
  rm -f "${DOWNLOADS}/${BUNDLE}"*
  download_asset "${base}/${BUNDLE}.sha256" "${DOWNLOADS}/${BUNDLE}.sha256"
  download_asset "${base}/${BUNDLE}" "${DOWNLOADS}/${BUNDLE}"
  if ! (cd "${DOWNLOADS}" && sha256sum --quiet -c "${BUNDLE}.sha256" 2>/dev/null); then
    warn "اثرانگشت نخواند؛ دریافت دوباره از صفر"
    rm -f "${DOWNLOADS:?}/${BUNDLE}"
    download_asset "${base}/${BUNDLE}" "${DOWNLOADS}/${BUNDLE}"
    (cd "${DOWNLOADS}" && sha256sum --quiet -c "${BUNDLE}.sha256") \
      || die "اثرانگشت sha256 بسته نمی‌خواند — دانلود ناقص یا دست‌کاری‌شده."
  fi
  ok "دریافت و اثرانگشت درست ($(du -h "${DOWNLOADS}/${BUNDLE}" | cut -f1))"
  activate "${DOWNLOADS}/${BUNDLE}" "${force}"
  rm -f "${DOWNLOADS}/${BUNDLE}"*
}

cmd_local() {
  local tarball="${1:?مسیر بسته لازم است}" force=0
  shift
  [[ "${1:-}" == "--force" ]] && force=1
  [[ -f "${tarball}" ]] || die "بسته یافت نشد: ${tarball}"
  if [[ -f "${tarball}.sha256" ]]; then
    (cd "$(dirname "${tarball}")" && sha256sum --quiet -c "$(basename "${tarball}").sha256") \
      || die "اثرانگشت sha256 بسته نمی‌خواند."
  fi
  ensure_layout; take_lock
  step "۱/۷ بستهٔ محلی ${tarball}"
  activate "${tarball}" "${force}"
}

activate() {  # activate TARBALL FORCE
  local tarball="$1" force="$2" release id previous
  prepare_site
  step "۲/۷ بازکردن امن"
  release="$(unpack_bundle "${tarball}" "${force}")"
  id="$(basename "${release}")"
  previous="$(current_release)"
  printf '\n%s══ استقرار فرانت %s · %s ══%s\n' "${c_step}" "${id}" "$(date -Is)" "${c_off}"
  info "کامیت $(json_field "${release}/BUILD_INFO.json" commit | cut -c1-12) · قبلی: ${previous:-هیچ (نصب نخست)}"

  step "۳/۷ یکپارچگی و قرارداد بسته"
  if ! verify_bundle "${release}"; then
    rm -rf "${release:?}"
    die "بسته رد شد و پاک شد؛ سایت دست‌نخورده است."
  fi
  chmod -R u=rwX,go= "${release}"

  step "۴/۷ پیش‌نیاز بک‌اند"
  local backend_front
  backend_front="$(live_field front)" || die "${SITE_URL}/health/live/ پاسخ سالم نداد؛ اول بک‌اند را سالم کنید."
  [[ "${backend_front}" != "__absent__" ]] \
    || die "بک‌اند فعال هنوز سرو فرانت را نمی‌شناسد — نخست: mahdieh update back"
  ok "بک‌اند آماده است (فرانت فعلی از دید دامنه: ${backend_front:-هیچ})"

  if [[ "${id}" == "${previous}" ]]; then
    info "همین انتشار از قبل فعال است"
  fi

  step "۵/۷ جابه‌جایی اتمی"
  switch_to "${id}"
  SWITCHED=1
  ok "current → ${id} (بی‌بازنشانی Passenger؛ جنگو پیوند را در درخواست بعدی می‌بیند)"

  step "۶/۷ راستی‌آزمایی بیرونی (${SITE_URL})"
  if verify_live "${id}"; then
    ok "دامنه فرانت ${id} را سالم سرو می‌کند (سلامت + سرآیند صفحهٔ اصلی)"
  else
    printf '%s✗ راستی‌آزمایی بیرونی شکست خورد.%s\n' "${c_bad}" "${c_off}" >&2
    if [[ -n "${previous}" && -d "${RELEASES}/${previous}" && "${previous}" != "${id}" ]]; then
      step "بازگشت خودکار به ${previous}"
      switch_to "${previous}"
      if verify_live "${previous}"; then
        ok "فرانت به ${previous} برگشت و سالم است"
      else
        warn "انتشار قبلی هم سالم دیده نشد — mahdieh doctor"
      fi
    else
      step "نصب نخست بود؛ پیوند برداشته می‌شود تا جنگو صفحه‌های قدیمی را سرو کند"
      rm -f "${CURRENT}"
    fi
    trap - ERR
    exit 1
  fi

  step "۷/۷ هرس"
  prune "${previous}"
  ok "دیسک فرانت: $(du -sh "${HOME_DIR}" 2>/dev/null | cut -f1)"
  printf '%s✅ فرانت %s فعال است.%s\n' "${c_ok}" "${id}" "${c_off}"
}

cmd_rollback() {
  ensure_layout; take_lock
  local current target="${1:-}"
  current="$(current_release)"
  [[ -n "${current}" ]] || die "انتشار فعالی برای بازگشت نیست."
  if [[ -z "${target}" ]]; then
    target="$(find "${RELEASES}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r \
      | awk -v cur="${current}" '$0 < cur {print; exit}')"
    [[ -n "${target}" ]] || die "انتشار قدیمی‌تری از ${current} نیست."
  fi
  [[ "${target}" =~ ^[0-9]{14}-[0-9a-f]{7}$ && -d "${RELEASES}/${target}" ]] || die "انتشار ${target} نیست."
  [[ "${target}" != "${current}" ]] || die "${target} همین حالا فعال است."
  prepare_site
  verify_bundle "${RELEASES}/${target}" || die "بستهٔ ${target} سالم نیست؛ بازگشت انجام نشد."
  step "بازگشت فرانت: ${current} → ${target}"
  switch_to "${target}"
  SWITCHED=1
  if verify_live "${target}"; then
    ok "فرانت روی ${target} است"
  else
    warn "دامنه هنوز ${target} را نشان نمی‌دهد — mahdieh doctor"
    exit 1
  fi
}

cmd_status() {
  local current r
  current="$(current_release)"
  printf 'فرانت فعال: %s\n' "${current:-هیچ}"
  if [[ -d "${RELEASES}" ]]; then
    while IFS= read -r r; do
      [[ -n "${r}" ]] || continue
      printf '  %s %s\n' "$([[ "${r}" == "${current}" ]] && echo '●' || echo '○')" "${r}"
    done < <(find "${RELEASES}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r)
  fi
  printf 'دیسک: %s\n' "$(du -sh "${HOME_DIR}" 2>/dev/null | cut -f1)"
}

main() {
  local command="${1:-status}"
  [[ $# -gt 0 ]] && shift
  case "${command}" in
    fetch|local|rollback)
      if [[ "${MAHDIEH_FRONT_LOGGED:-0}" != "1" ]]; then run_logged "${command}" "$@"; fi
      "cmd_${command}" "$@" ;;
    status) cmd_status ;;
    -h|--help|help) sed -n '2,24p' "${BASH_SOURCE[0]}" ;;
    *) die "فرمان ناشناخته: ${command} (fetch · local · rollback · status)" ;;
  esac
}

main "$@"
