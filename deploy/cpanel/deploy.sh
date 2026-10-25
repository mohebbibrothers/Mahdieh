#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# استقرار اتمی مهدیه روی میزبان cPanel (CloudLinux · Apache + Passenger · MariaDB)
#
#   deploy.sh fetch [--tag edge|vX.Y.Z] [--force]  دریافت بسته از GitHub Release ⇒ activate
#   deploy.sh local BUNDLE.tar.gz [--force]        همان، از پروندهٔ محلی
#   deploy.sh activate RELEASE_DIR [--skip-backup] فعال‌سازی یک انتشارِ بازشده
#   deploy.sh rollback [RELEASE_ID]                بازگشت به انتشار قبلی (یا داده‌شده)
#   deploy.sh status                               وضعیت، فضای دیسک، انتشارها
#
# برای کار روزمره فرمان یکپارچهٔ «mahdieh» (mahdieh_cli.py) را به کار ببرید:
#   mahdieh update · mahdieh · mahdieh doctor · mahdieh logs · mahdieh rollback
#
# مراحل activate — هر مرحله دروازه است؛ شکستِ پیش از «جابه‌جایی» یعنی سایت دست‌نخورده:
#   ۱ محیط مجازی (بازمصرف با کلید وابستگی‌ها؛ نصب آفلاین از wheelhouse)
#   ۲ پیوندها (‎.env، var، media) ۳ دروازهٔ انتشار (production_check)
#   ۴ پشتیبان پیش از مهاجرت (فقط اگر مهاجرت معلق هست) ۵ مهاجرت + جدول کش
#   ۶ دارایی ایستا ۷ آزمون درون‌فرآیندی انتشار تازه (WSGI، بدون ترافیک واقعی)
#   ۸ جابه‌جایی اتمی current + بازنشانی Passenger
#   ۹ راستی‌آزمایی بیرونی (HTTPS): شناسهٔ انتشار و آمادگی — شکست ⇒ بازگشت خودکار
#  ۱۰ crontab، ابزارها، هرس انتشارها و محیط‌های مجازی بی‌استفاده
#
# مهاجرت‌ها «افزایشی» نوشته می‌شوند (expand → contract)؛ پس بازگشتِ کد روی طرح
# مهاجرت‌یافته امن است. بازگشتِ طرح هرگز خودکار نیست (db_restore.sh، با تصمیم انسان).
# ══════════════════════════════════════════════════════════════════════════════
set -Eeuo pipefail
umask 027

HOME_DIR="${MAHDIEH_HOME:-${HOME}/mahdieh}"
REPO="${MAHDIEH_REPO:-mohebbibrothers/Mahdieh}"
BASE_PY="${MAHDIEH_BASE_PYTHON:-/opt/alt/python312/bin/python3}"
PASSENGER_PY="${MAHDIEH_PASSENGER_PYTHON:-${HOME}/virtualenv/mahdieh/app/3.12/bin/python}"
ENV_SOURCE="${MAHDIEH_ENV_SOURCE:-${HOME}/.mahdieh/mahdieh.env}"
KEEP="${MAHDIEH_KEEP_RELEASES:-3}"
DRILL_DB="${MAHDIEH_DRILL_DB:-mahdiesh_drill}"
RESOLVE_IP="${MAHDIEH_VERIFY_RESOLVE:-}"   # اختیاری: curl --resolve دامنه به این IP
VERIFY_SECONDS="${MAHDIEH_VERIFY_SECONDS:-90}"
# فقط برای آزمون مجموعهٔ استقرار بیرون از میزبان: نشانی پایهٔ جایگزین (HTTP پشت پروکسی)
VERIFY_BASE_URL="${MAHDIEH_VERIFY_BASE_URL:-}"

RELEASES="${HOME_DIR}/releases"
VENVS="${HOME_DIR}/venvs"
SHARED="${HOME_DIR}/shared"
APP="${HOME_DIR}/app"
BIN="${HOME_DIR}/bin"
CURRENT="${HOME_DIR}/current"
LOG_DIR="${SHARED}/var/log"
LOCK_DIR="${SHARED}/var/locks"

c_ok=$'\033[1;32m'; c_bad=$'\033[1;31m'; c_step=$'\033[1;36m'; c_dim=$'\033[0;90m'; c_off=$'\033[0m'
step() { printf '%s▸ %s%s\n' "${c_step}" "$*" "${c_off}"; }
ok()   { printf '  %s✓%s %s\n' "${c_ok}" "${c_off}" "$*"; }
info() { printf '  %s•%s %s\n' "${c_dim}" "${c_off}" "$*"; }
warn() { printf '  %s!%s %s\n' "${c_bad}" "${c_off}" "$*" >&2; }
die()  { printf '%s✗ %s%s\n' "${c_bad}" "$*" "${c_off}" >&2; exit 1; }

on_error() {
  local code=$? line=$1
  (( BASH_SUBSHELL == 0 )) || return "${code}"   # فقط یک گزارش، از پوستهٔ اصلی
  printf '%s✗ استقرار در خط %s با کد %s متوقف شد.%s\n' "${c_bad}" "${line}" "${code}" "${c_off}" >&2
  if [[ "${SWITCHED:-0}" != "1" ]]; then
    printf '  سایت دست‌نخورده است: current هنوز به انتشار قبلی اشاره می‌کند.\n' >&2
  fi
}
trap 'on_error $LINENO' ERR

ensure_layout() {
  mkdir -p "${RELEASES}" "${VENVS}" "${SHARED}/var/backups" "${SHARED}/var/drills" \
    "${SHARED}/media" "${SHARED}/downloads" "${LOG_DIR}" "${LOCK_DIR}" "${APP}/tmp" \
    "${APP}/public" "${BIN}"
  # Apache/Passenger (کاربر وب‌سرور) باید بتواند تا ریشهٔ برنامه «عبور» کند تا
  # passenger_wsgi.py و tmp/restart.txt را ببیند؛ فقط x (۷۱۱)، بی‌اجازهٔ فهرست‌کردن.
  # داده‌ها (shared/var، پشتیبان‌ها) همچنان بسته‌اند.
  chmod 711 "${HOME_DIR}" "${APP}" "${APP}/tmp"
  chmod 750 "${SHARED}" "${SHARED}/var"
  chmod 700 "${SHARED}/var/backups"
}

take_lock() {
  exec 8>"${LOCK_DIR}/deploy.lock"
  flock -n 8 || die "استقرار دیگری در جریان است (${LOCK_DIR}/deploy.lock)."
}

# همهٔ خروجی activate/rollback در deploy.log هم می‌نشیند. بدون تغییر مسیر به جایگزینی فرآیند:
# جایگزینی فرآیند به /dev/fd نیاز دارد و CageFS میزبان آن را ندارد. پس فرمان یک
# بار دیگر با لوله به tee اجرا می‌شود (قفل fd 8 به فرزند به ارث می‌رسد).
run_logged() {  # run_logged CMD ARGS... — هرگز برنمی‌گردد
  ensure_layout
  trap - ERR
  set +e
  MAHDIEH_DEPLOY_LOGGED=1 bash "${BASH_SOURCE[0]}" "$@" 2>&1 | tee -a "${LOG_DIR}/deploy.log"
  exit "${PIPESTATUS[0]}"
}

json_field() {  # json_field FILE KEY
  "${BASE_PY}" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]])' "$1" "$2"
}

current_release() { [[ -L "${CURRENT}" ]] && basename "$(readlink -f "${CURRENT}")" || true; }

site_url() {  # از فایل محیط، بدون source کردن آن (مقادیر فارسیِ بی‌نقل‌قول)
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

http_get() {  # http_get PATH ⇒ بدنه روی stdout
  local url="${SITE_URL}$1" args=(-fsS --max-time 15 --proto '=https' -H 'Cache-Control: no-cache')
  if [[ -n "${VERIFY_BASE_URL}" ]]; then
    url="${VERIFY_BASE_URL}$1"
    args=(-fsS --max-time 15 -H 'Cache-Control: no-cache' -H 'X-Forwarded-Proto: https' -H "Host: ${DOMAIN}")
  elif [[ -n "${RESOLVE_IP}" ]]; then
    args+=(--resolve "${DOMAIN}:443:${RESOLVE_IP}")
  fi
  curl "${args[@]}" "${url}"
}

# ── دریافت و بازکردن بسته ────────────────────────────────────────────────────
# دریافت مقاوم برای اینترنت بین‌الملل کند/ناپایدار (یافتهٔ گام ۸ب: curl ۱۸ پس از ۴ دقیقه).
#   • «--retry» خودِ curl خطای ۱۸ (قطع وسط انتقال) را تکرار نمی‌کند و هر بار از صفر
#     شروع می‌کند ⇒ حلقهٔ خودمان با «-C -» (ادامه از همان بایت).
#   • سقف زمان کل نداریم (روی خط کند، بستهٔ سالم را می‌کُشت)؛ به‌جایش «گیر» را
#     می‌سنجیم: کمتر از ۱ KB/s به مدت ۶۰ ثانیه ⇒ قطع و اتصال دوباره.
#   • ۴۱۶ (فایل از قبل کامل) موفقیت است؛ ۳۳ (سرور Range نمی‌پذیرد) ⇒ از صفر.
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
      22) # curl قدیمی میزبان ممکن است ۴۱۶ «از قبل کامل» را خطا بداند.
          if [[ -s "${dest}" ]] && curl -sSIL --proto '=https' --connect-timeout 20 \
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
  top="$(cut -d/ -f1 <<<"${entries}" | sort -u)"
  [[ "$(wc -l <<<"${top}")" == "1" && "${top}" =~ ^[A-Za-z0-9._-]+$ ]] \
    || die "بسته باید دقیقاً یک پوشهٔ بالایی با نام معتبر داشته باشد."
  if [[ -d "${RELEASES}/${top}" ]]; then
    [[ "${force}" == "1" ]] || die "انتشار ${top} از قبل هست (برای بازسازی: --force)."
    [[ "$(current_release)" != "${top}" ]] || die "انتشار ${top} هم‌اکنون فعال است؛ بازنویسی‌اش مجاز نیست."
    rm -rf "${RELEASES:?}/${top}"
  fi
  tar -xzf "${tarball}" -C "${RELEASES}" --no-same-owner --no-same-permissions
  [[ "$(json_field "${RELEASES}/${top}/BUILD_INFO.json" release)" == "${top}" ]] \
    || die "BUILD_INFO.json با نام پوشهٔ انتشار نمی‌خواند."
  printf '%s\n' "${RELEASES}/${top}"
}

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
  local base="https://github.com/${REPO}/releases/download/${tag}" dl="${SHARED}/downloads"
  step "دریافت بستهٔ «${tag}» از GitHub"
  rm -f "${dl}"/mahdieh-bundle.tar.gz*
  download_asset "${base}/mahdieh-bundle.tar.gz.sha256" "${dl}/mahdieh-bundle.tar.gz.sha256"
  download_asset "${base}/mahdieh-bundle.tar.gz" "${dl}/mahdieh-bundle.tar.gz"
  if ! (cd "${dl}" && sha256sum --quiet -c mahdieh-bundle.tar.gz.sha256 2>/dev/null); then
    # ادامهٔ دانلود روی بایت‌های خراب ⇒ اثرانگشت نمی‌خواند؛ یک بار از صفر.
    warn "اثرانگشت نخواند؛ دریافت دوباره از صفر"
    rm -f "${dl}/mahdieh-bundle.tar.gz"
    download_asset "${base}/mahdieh-bundle.tar.gz" "${dl}/mahdieh-bundle.tar.gz"
    (cd "${dl}" && sha256sum --quiet -c mahdieh-bundle.tar.gz.sha256) \
      || die "اثرانگشت sha256 بسته نمی‌خواند — دانلود ناقص یا دست‌کاری‌شده."
  fi
  ok "دریافت و اثرانگشت درست ($(du -h "${dl}/mahdieh-bundle.tar.gz" | cut -f1))"
  local release
  release="$(unpack_bundle "${dl}/mahdieh-bundle.tar.gz" "${force}")"
  rm -f "${dl}"/mahdieh-bundle.tar.gz*
  ok "باز شد: $(basename "${release}")"
  # منطق فعال‌سازی از **خودِ انتشار تازه** اجرا می‌شود (قفل fd 8 به ارث می‌رسد).
  exec bash "${release}/deploy/cpanel/deploy.sh" activate "${release}" --locked
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
  local release
  release="$(unpack_bundle "${tarball}" "${force}")"
  exec bash "${release}/deploy/cpanel/deploy.sh" activate "${release}" --locked
}

# ── محیط مجازی (بازمصرف با کلید) ──────────────────────────────────────────────
ensure_venv() {  # ensure_venv RELEASE
  local release="$1" key venv wh pin
  key="$(json_field "${release}/BUILD_INFO.json" venv_key)"
  [[ "${key}" =~ ^[a-f0-9]{16}$ ]] || die "کلید محیط مجازی نامعتبر: ${key}"
  venv="${VENVS}/${key}"
  wh="${release}/wheelhouse"
  if [[ -f "${venv}/.complete" ]]; then
    ok "محیط مجازی بازمصرف شد (${key})"
  else
    [[ -d "${wh}" ]] || die "wheelhouse در بسته نیست و محیط ${key} هم ساخته نشده."
    rm -rf "${venv}"
    "${BASE_PY}" -m venv "${venv}"
    "${venv}/bin/python" -m pip install --quiet --disable-pip-version-check --no-index \
      --find-links "${wh}" -r "${release}/requirements.lock"
    if ! "${venv}/bin/python" -c 'import MySQLdb' 2>/dev/null; then
      info "چرخ آمادهٔ mysqlclient روی این میزبان بالا نیامد؛ ساخت از sdist با mysql_config"
      pin="$(grep -E '^mysqlclient==' "${release}/requirements.lock" | cut -d' ' -f1)"
      MYSQLCLIENT_CFLAGS="$(mysql_config --cflags)" MYSQLCLIENT_LDFLAGS="$(mysql_config --libs)" \
        "${venv}/bin/python" -m pip install --quiet --disable-pip-version-check --no-index \
        --find-links "${wh}" --no-binary mysqlclient --force-reinstall --no-deps "${pin}"
    fi
    "${venv}/bin/python" -m pip check >/dev/null || die "pip check: وابستگی‌ها ناسازگارند."
    "${venv}/bin/python" - <<'PY'
import MySQLdb, cryptography, django, rest_framework
print(f"  • Django {django.__version__} · DRF {rest_framework.VERSION} · "
      f"mysqlclient {'.'.join(map(str, MySQLdb.version_info[:3]))} · "
      f"cryptography {cryptography.__version__}")
PY
    touch "${venv}/.complete"
    ok "محیط مجازی تازه ساخته شد (${key})"
  fi
  ln -sfn "../../venvs/${key}" "${release}/.venv"
  rm -rf "${wh}"   # چرخ‌ها دیگر لازم نیستند؛ سهمیهٔ دیسک
}

link_shared() {
  local release="$1"
  [[ -f "${ENV_SOURCE}" ]] || die "فایل محیط نیست: ${ENV_SOURCE}"
  [[ "$(stat -c %a "${ENV_SOURCE}")" == "600" ]] || die "دسترسی ${ENV_SOURCE} باید ۶۰۰ باشد."
  ln -sfn "${ENV_SOURCE}" "${release}/src/.env"
  ln -sfn "${SHARED}/var" "${release}/var"
  rm -rf "${release}/src/media"
  ln -sfn "${SHARED}/media" "${release}/src/media"
  ok "پیوندها: .env · var · media"
}

manage() { (cd "${ACTIVE}/src" && "${ACTIVE}/.venv/bin/python" manage.py "$@"); }

switch_to() {  # switch_to RELEASE_ID
  ln -sfn "releases/$1" "${HOME_DIR}/.current.next"
  mv -Tf "${HOME_DIR}/.current.next" "${CURRENT}"
}

restart_passenger() {
  local release
  release="$(readlink -f "${CURRENT}")"
  install -m 644 "${release}/deploy/cpanel/passenger_wsgi.py" "${APP}/passenger_wsgi.py.next"
  mv -f "${APP}/passenger_wsgi.py.next" "${APP}/passenger_wsgi.py"
  touch "${APP}/tmp/restart.txt"
}

verify_live() {  # verify_live RELEASE_ID ⇒ ۰ اگر دامنه همین انتشار را سالم سرو کند
  local expect="$1" deadline=$(( $(date +%s) + VERIFY_SECONDS )) live ready seen=""
  while (( $(date +%s) < deadline )); do
    if live="$(http_get /health/live/ 2>/dev/null)"; then
      seen="$("${BASE_PY}" -c 'import json,sys; print(json.loads(sys.argv[1]).get("release",""))' "${live}" 2>/dev/null || true)"
      if [[ "${seen}" == "${expect}" ]]; then
        ready="$(http_get /health/ready/ 2>/dev/null || true)"
        if "${BASE_PY}" -c 'import json,sys; sys.exit(0 if json.loads(sys.argv[1])["status"] == "healthy" else 1)' "${ready}" 2>/dev/null; then
          return 0
        fi
      fi
    fi
    sleep 3
  done
  printf '  آخرین شناسهٔ دیده‌شده: «%s» · آخرین آمادگی: %s\n' "${seen}" "${ready:-—}" >&2
  return 1
}

install_tools() {
  local release="$1" src dst mode
  for src in deploy.sh:deploy.sh:750 cron_run.sh:cron_run.sh:750 mahdieh_cli.py:mahdieh_cli.py:640 \
    mahdieh.sh:mahdieh:750; do
    IFS=: read -r src dst mode <<<"${src}"
    install -m "${mode}" "${release}/deploy/cpanel/${src}" "${BIN}/${dst}.next"
    mv -f "${BIN}/${dst}.next" "${BIN}/${dst}"
  done
  ln -sfn deploy.sh "${BIN}/mahdieh-deploy"
  # ابزار استقرار فرانت (ADR-0022) — چرخهٔ جدا، همان قواعد؛ «mahdieh update front» آن را صدا می‌زند.
  local front_home="${MAHDIEH_FRONT_HOME:-${HOME}/mahdieh-front}"
  mkdir -p "${front_home}/bin"
  chmod 700 "${front_home}"
  install -m 750 "${release}/deploy/cpanel/front_deploy.sh" "${front_home}/bin/deploy.sh.next"
  mv -f "${front_home}/bin/deploy.sh.next" "${front_home}/bin/deploy.sh"
  install_shell_path
  ok "فرمان mahdieh نصب شد (mahdieh help) · ابزار فرانت: ${front_home}/bin/deploy.sh"
}

install_shell_path() {  # PATH و تکمیل خودکار «mahdieh» در ‎~/.bashrc — بلوک مدیریت‌شده، تکرارپذیر
  local rc="${HOME}/.bashrc" body block
  block="# >>> mahdieh cli — مدیریت خودکار با deploy.sh؛ دستی ویرایش نکنید >>>
case \":\${PATH}:\" in *\":${BIN}:\"*) ;; *) export PATH=\"${BIN}:\${PATH}\" ;; esac
complete -W \"update status doctor logs rollback manage version help\" mahdieh 2>/dev/null || true
# <<< mahdieh cli <<<"
  body=""
  [[ -f "${rc}" ]] && body="$(awk '/^# >>> mahdieh cli/{skip=1} !skip{print} /^# <<< mahdieh cli/{skip=0}' "${rc}")"
  if [[ -n "${body}" ]]; then printf '%s\n%s\n' "${body}" "${block}"; else printf '%s\n' "${block}"; fi \
    > "${rc}.mahdieh-next"
  if cmp -s "${rc}.mahdieh-next" "${rc}"; then rm -f "${rc}.mahdieh-next"; else mv -f "${rc}.mahdieh-next" "${rc}"; fi
}

install_crontab() {
  local release="$1" rendered current_tab
  rendered="$(sed -e "s|@HOME@|${HOME_DIR}|g" -e "s|@SITE_URL@|${SITE_URL}|g" \
    -e "s|@DRILL_DB@|${DRILL_DB}|g" "${release}/deploy/cpanel/crontab.tmpl")"
  current_tab="$(crontab -l 2>/dev/null || true)"
  {
    awk '/^# >>> mahdieh/{skip=1} !skip{print} /^# <<< mahdieh/{skip=0}' <<<"${current_tab}" \
      | sed '/^[[:space:]]*$/d'
    printf '%s\n' "${rendered}"
  } | crontab -
  ok "crontab: $(grep -cE '^[0-9*]' <<<"${rendered}") کار زمان‌بندی‌شده"
}

prune() {
  local current previous keep_list=() r all=()
  current="$(current_release)"
  previous="${1:-}"
  local listing
  listing="$(find "${RELEASES}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r)"
  if [[ -n "${listing}" ]]; then mapfile -t all <<<"${listing}"; fi
  local kept=0
  for r in "${all[@]}"; do
    if [[ "${r}" == "${current}" || "${r}" == "${previous}" ]] || (( kept < KEEP )); then
      keep_list+=("${r}"); kept=$((kept + 1))
    else
      rm -rf "${RELEASES:?}/${r}"; info "هرس انتشار ${r}"
    fi
  done
  local used=() v
  for r in "${keep_list[@]}"; do
    [[ -L "${RELEASES}/${r}/.venv" ]] && used+=("$(basename "$(readlink "${RELEASES}/${r}/.venv")")")
  done
  for v in "${VENVS}"/*/; do
    v="$(basename "${v}")"
    [[ -d "${VENVS}/${v}" ]] || continue
    if ! printf '%s\n' "${used[@]}" | grep -qx "${v}"; then
      rm -rf "${VENVS:?}/${v}"; info "هرس محیط مجازی ${v}"
    fi
  done
}

# ── فعال‌سازی ────────────────────────────────────────────────────────────────
cmd_activate() {
  local release="${1:?مسیر انتشار لازم است}" skip_backup=0 locked=0
  shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --skip-backup) skip_backup=1; shift ;;
      --locked) locked=1; shift ;;
      *) die "گزینهٔ ناشناخته: $1" ;;
    esac
  done
  release="$(cd "${release}" && pwd -P)"
  [[ "$(dirname "${release}")" == "$(cd "${RELEASES}" && pwd -P)" ]] || die "انتشار باید زیر ${RELEASES} باشد."
  ensure_layout
  (( locked )) || take_lock

  local id previous
  id="$(basename "${release}")"
  previous="$(current_release)"
  ACTIVE="${release}"
  printf '\n%s══ استقرار %s · %s ══%s\n' "${c_step}" "${id}" "$(date -Is)" "${c_off}"
  info "کامیت $(json_field "${release}/BUILD_INFO.json" commit | cut -c1-12) · قبلی: ${previous:-هیچ}"

  step "۱/۱۰ محیط مجازی"
  ensure_venv "${release}"
  step "۲/۱۰ پیوندهای مشترک"
  link_shared "${release}"
  SITE_URL="$(site_url)"
  [[ "${SITE_URL}" =~ ^https://[A-Za-z0-9.-]+$ ]] || die "MAHDIEH_SITE_URL در فایل محیط باید https://دامنه باشد (اکنون: «${SITE_URL}»)."
  DOMAIN="${SITE_URL#https://}"

  step "۳/۱۰ دروازهٔ انتشار"
  manage production_check
  ok "production_check سبز"

  step "۴/۱۰ پشتیبان پیش از مهاجرت"
  local pending=0 plan=""
  manage migrate --check >/dev/null 2>&1 || pending=1
  # فهرست کامل اول خوانده می‌شود: «manage | grep -q» زیر pipefail مسابقهٔ SIGPIPE دارد و گاهی
  # پایگاه پُر را «تازه» می‌پنداشت ⇒ مهاجرت بی‌پشتیبان. (جایگزینی فرایند در میزبان ممنوع است.)
  (( pending == 0 )) || plan="$(manage showmigrations --plan 2>/dev/null || true)"
  if (( pending == 0 )); then
    info "مهاجرت معلقی نیست؛ پشتیبان لازم نیست"
  elif (( skip_backup )); then
    info "به درخواست اپراتور رد شد (--skip-backup)"
  elif grep -q '\[X\]' <<<"${plan}"; then
    MAHDIEH_PYTHON="${release}/.venv/bin/python" "${release}/scripts/db_backup.sh" --label "pre-${id##*-}"
    ok "پشتیبان تأییدشده پیش از مهاجرت"
  else
    info "پایگاه‌داده تازه است؛ پشتیبانی برای گرفتن نیست"
  fi

  step "۵/۱۰ مهاجرت و جدول کش"
  manage migrate --no-input
  manage createcachetable
  ok "طرح پایگاه‌داده به‌روز است"

  step "۶/۱۰ دارایی ایستا"
  manage collectstatic --no-input --verbosity 0
  ok "$(find "${release}/src/staticfiles" -type f | wc -l) پرونده (نسخه‌دار و فشرده)"

  step "۷/۱۰ آزمون درون‌فرآیندی انتشار تازه"
  local probe_py="${PASSENGER_PY}"
  [[ -x "${probe_py}" ]] || probe_py="${BASE_PY}"
  "${probe_py}" "${release}/deploy/cpanel/wsgi_probe.py" "${release}" \
    --host "${DOMAIN}" --expect-release "${id}"

  step "۸/۱۰ جابه‌جایی اتمی و بازنشانی Passenger"
  switch_to "${id}"
  SWITCHED=1
  restart_passenger
  ok "current → ${id}"

  step "۹/۱۰ راستی‌آزمایی بیرونی (${SITE_URL})"
  if verify_live "${id}"; then
    ok "دامنه انتشار ${id} را سالم سرو می‌کند"
  else
    printf '%s✗ راستی‌آزمایی بیرونی شکست خورد.%s\n' "${c_bad}" "${c_off}" >&2
    if [[ -n "${previous}" && -d "${RELEASES}/${previous}" ]]; then
      step "بازگشت خودکار به ${previous}"
      switch_to "${previous}"
      restart_passenger
      if verify_live "${previous}"; then
        ok "سایت به ${previous} برگشت و سالم است"
      else
        printf '%s✗ انتشار قبلی هم سالم پاسخ نداد — بررسی دستی لازم است.%s\n' "${c_bad}" "${c_off}" >&2
      fi
    fi
    trap - ERR
    exit 1
  fi

  step "۱۰/۱۰ crontab، ابزارها و هرس"
  install_crontab "${release}"
  install_tools "${release}"
  prune "${previous}"
  ok "دیسک مهدیه: $(du -sh "${HOME_DIR}" 2>/dev/null | cut -f1)"
  printf '%s✅ انتشار %s فعال است.%s\n' "${c_ok}" "${id}" "${c_off}"
}

cmd_rollback() {
  ensure_layout; take_lock
  local current target="${1:-}"
  current="$(current_release)"
  [[ -n "${current}" ]] || die "انتشار فعالی نیست."
  if [[ -z "${target}" ]]; then
    target="$(find "${RELEASES}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r \
      | awk -v cur="${current}" 'found && $0 < cur {print; exit} $0 == cur {found=1}')"
  fi
  [[ -n "${target}" && -d "${RELEASES}/${target}" ]] || die "انتشار مقصد برای بازگشت یافت نشد."
  [[ -L "${RELEASES}/${target}/.venv" && -f "${RELEASES}/${target}/.venv/.complete" ]] \
    || die "محیط مجازی ${target} سالم نیست."
  SITE_URL="$(site_url)"; DOMAIN="${SITE_URL#https://}"
  printf '\n%s══ بازگشت %s → %s · %s ══%s\n' "${c_step}" "${current}" "${target}" "$(date -Is)" "${c_off}"
  info "طرح پایگاه‌داده بازگردانده نمی‌شود (مهاجرت‌ها افزایشی‌اند)."
  switch_to "${target}"
  SWITCHED=1
  restart_passenger
  verify_live "${target}" || die "انتشار ${target} پس از بازگشت سالم پاسخ نداد."
  ok "سایت روی ${target} است"
}

cmd_status() {
  local current
  current="$(current_release)"
  printf 'انتشار فعال: %s\n' "${current:-هیچ}"
  printf 'انتشارها:\n'
  find "${RELEASES}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort -r \
    | while read -r r; do printf '  %s %s\n' "$([[ "${r}" == "${current}" ]] && echo '●' || echo '○')" "${r}"; done
  printf 'محیط‌های مجازی: %s\n' "$(find "${VENVS}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)"
  printf 'دیسک: %s (مهدیه) · پشتیبان‌ها %s\n' "$(du -sh "${HOME_DIR}" 2>/dev/null | cut -f1)" \
    "$(du -sh "${SHARED}/var/backups" 2>/dev/null | cut -f1)"
  printf 'crontab: %s\n' "$(crontab -l 2>/dev/null | grep -c 'cron_run.sh' || true) کار"
  if [[ -f "${ENV_SOURCE}" ]]; then
    SITE_URL="$(site_url)"; DOMAIN="${SITE_URL#https://}"
    printf 'زنده: %s\n' "$(http_get /health/live/ 2>&1 || true)"
  fi
}

main() {
  local cmd="${1:-}"
  shift || true
  case "${cmd}" in
    fetch) cmd_fetch "$@" ;;
    local) cmd_local "$@" ;;
    activate|rollback)
      [[ "${MAHDIEH_DEPLOY_LOGGED:-0}" == "1" ]] || run_logged "${cmd}" "$@"
      "cmd_${cmd}" "$@" ;;
    status) cmd_status ;;
    *) sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 2 ;;
  esac
}

main "$@"
