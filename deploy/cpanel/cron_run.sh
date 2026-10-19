#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# اجرای یک کار زمان‌بندی‌شده روی میزبان cPanel — جایگزین واحدهای systemd.
#
# هر خط crontab فقط همین را صدا می‌زند؛ این پوسته سه تضمین می‌دهد:
#   ۱) تک‌نمونگی: flock غیرمسدود — اگر اجرای قبلی هنوز تمام نشده، این یکی رد می‌شود
#      (هرگز دو رلهٔ صندوق خروجی یا دو پشتیبان هم‌زمان؛ سقف ۵۰ فرآیند LVE).
#   ۲) سقف زمان: timeout — کار گیرکرده تا ابد ظرفیت را اشغال نمی‌کند.
#   ۳) گزارش مهارشده: یک پروندهٔ لاگ برای هر کار با چرخش در ۵ مگابایت
#      (logrotate در دسترس کاربر نیست؛ سهمیهٔ دیسک ۱ گیگ است).
#
# استفاده:  cron_run.sh NAME TIMEOUT_SECONDS -- COMMAND [ARGS...]
#   COMMAND نسبی به پوشهٔ انتشار جاری اجرا می‌شود؛ «manage» = manage.py با مفسر انتشار.
# ══════════════════════════════════════════════════════════════════════════════
set -Eeuo pipefail

HOME_DIR="${MAHDIEH_HOME:-${HOME}/mahdieh}"
CURRENT="${HOME_DIR}/current"
LOG_DIR="${HOME_DIR}/shared/var/log"
LOCK_DIR="${HOME_DIR}/shared/var/locks"
MAX_LOG_BYTES=$((5 * 1024 * 1024))

[[ $# -ge 4 && "$3" == "--" ]] || { echo "استفاده: $0 NAME TIMEOUT -- COMMAND..." >&2; exit 2; }
NAME="$1"; LIMIT="$2"; shift 3
[[ "${NAME}" =~ ^[a-z0-9_-]+$ ]] || { echo "نام کار نامعتبر: ${NAME}" >&2; exit 2; }
[[ "${LIMIT}" =~ ^[0-9]+$ ]] || { echo "سقف زمان نامعتبر: ${LIMIT}" >&2; exit 2; }

mkdir -p "${LOG_DIR}" "${LOCK_DIR}"
LOG="${LOG_DIR}/${NAME}.log"
if [[ -f "${LOG}" ]] && (( $(stat -c %s "${LOG}") > MAX_LOG_BYTES )); then
  mv -f "${LOG}" "${LOG}.1"
fi

# پوشهٔ انتشار یک بار حل می‌شود: اگر وسط کار استقرار تازه‌ای رخ دهد، این اجرا
# تا پایان روی همان نسخه می‌ماند.
RELEASE="$(readlink -f "${CURRENT}")" || { echo "انتشار جاری نیست" >> "${LOG}"; exit 1; }
PY="${RELEASE}/.venv/bin/python"

if [[ "$1" == "manage" ]]; then
  shift
  set -- "${PY}" "${RELEASE}/src/manage.py" "$@"
else
  first="$1"; shift
  set -- "${RELEASE}/${first}" "$@"
fi

exec 9>"${LOCK_DIR}/${NAME}.lock"
if ! flock -n 9; then
  printf '%s [%s] رد شد: اجرای قبلی هنوز در جریان است\n' "$(date -Is)" "${NAME}" >> "${LOG}"
  exit 0
fi

cd "${RELEASE}/src"
export MAHDIEH_PYTHON="${PY}"
started="$(date +%s)"
set +e
timeout --kill-after=30 "${LIMIT}" "$@" >> "${LOG}" 2>&1
code=$?
set -e
elapsed=$(( $(date +%s) - started ))
if (( code != 0 )); then
  printf '%s [%s] ✗ کد خروج %s پس از %ss (انتشار %s)\n' \
    "$(date -Is)" "${NAME}" "${code}" "${elapsed}" "$(basename "${RELEASE}")" >> "${LOG}"
fi
exit "${code}"
