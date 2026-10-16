#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# بازیابی پایگاه‌دادهٔ مهدیه — «بازیابیِ امن، تأییدشده و بازگشت‌پذیر».
#
# چرا این‌قدر محافظه‌کارانه؟ چون بازیابی روی پایگاه‌دادهٔ زنده یک عملیات
# برگشت‌ناپذیر است. این اسکریپت پیش از هر تغییری:
#   • هش SHA-256 فایل را با بیانیهٔ پشتیبان تطبیق می‌دهد (ضد خرابی/دست‌کاری)
#   • از پایگاه‌دادهٔ فعلی یک پشتیبان نجات (rescue) می‌گیرد
#   • محتوای آرشیو را بررسی می‌کند
# سپس با --clean --if-exists بازیابی می‌کند و در پایان شمارش ردیف‌های کلیدی،
# صحت زنجیرهٔ حسابرسی و سلامت سلامت‌سنجی را گزارش می‌دهد.
#
# استفاده:
#   scripts/db_restore.sh --file var/backups/mahdieh-20261016T...Z-manual.dump
#   scripts/db_restore.sh --latest --yes
#   scripts/db_restore.sh --latest --target mahdieh_drill   # رهگیری بدون تماس با prod
#
# نکتهٔ حیاتی: `--target` را برای تمرین DR به کار ببرید؛ هرگز روی prod تمرین نکنید.
# ─────────────────────────────────────────────────────────────────────────────
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

: "${MAHDIEH_BACKUP_DIR:=${REPO_ROOT}/var/backups}"

DUMP_FILE=""
TARGET_DB="${DATABASE_NAME:-mahdieh}"
EXPECT_SHA=""
ASSUME_YES=0
USE_LATEST=0
DROP_TARGET=0

# ── اجرای فرمان‌های Django با مفسرِ درست ─────────────────────────────────────
# درس ۶۰۳: `.venv` مخزن ممکن است تنها جایی باشد که Django نصب است؛ فراخوانی
# خام `python3` در محیط تولید/تمرین به ImportError می‌خورد. این تابع مسیر را
# قطعی می‌کند: venv محلی ⇒ uv ⇒ python3.
run_manage() {  # run_manage <database_url> <log_file> <command> [args…]
  local url="$1" log_file="$2"; shift 2
  local -a runner
  if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    runner=("${REPO_ROOT}/.venv/bin/python")
  elif command -v uv >/dev/null 2>&1; then
    runner=("$(command -v uv)" run --project "${REPO_ROOT}" python)
  else
    runner=(python3)
  fi
  ( cd "${REPO_ROOT}/src" \
      && DATABASE_URL="${url}" DJANGO_SETTINGS_MODULE=config.settings.development \
         "${runner[@]}" manage.py "$@" ) >"${log_file}" 2>&1
}

log() { printf '%s [restore] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { printf '%s [restore][خطا] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --file)       DUMP_FILE="${2:?--file به مسیر نیاز دارد}"; shift 2 ;;
    --latest)     USE_LATEST=1; shift ;;
    --target)     TARGET_DB="${2:?--target به نام پایگاه‌داده نیاز دارد}"; shift 2 ;;
    --expect-sha256) EXPECT_SHA="${2:?}"; shift 2 ;;
    --drop-target) DROP_TARGET=1; shift ;;
    -y|--yes)     ASSUME_YES=1; shift ;;
    -h|--help)    sed -n '2,23p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "آرگومان ناشناخته: $1" ;;
  esac
done

[[ -n "${DUMP_FILE}" || ${USE_LATEST} -eq 1 ]] || die "یکی از --file یا --latest لازم است."

if [[ -n "${DATABASE_URL:-}" ]]; then
  export PGURL="${DATABASE_URL}"
else
  : "${DATABASE_HOST:=127.0.0.1}"
  : "${DATABASE_PORT:=5432}"
  : "${DATABASE_USER:=mahdieh}"
  : "${DATABASE_PASSWORD:=}"
  export PGPASSWORD="${DATABASE_PASSWORD}"
  export PGURL="postgresql://${DATABASE_USER}@${DATABASE_HOST}:${DATABASE_PORT}/${DATABASE_NAME:-mahdieh}"
fi

if ((USE_LATEST)); then
  DUMP_FILE="$(find "${MAHDIEH_BACKUP_DIR}" -maxdepth 1 -name 'mahdieh-*.dump' -printf '%T@ %p\n' \
                | sort -nr | head -1 | cut -d' ' -f2-)"
  [[ -n "${DUMP_FILE}" ]] || die "هیچ پشتیبانی در ${MAHDIEH_BACKUP_DIR} پیدا نشد."
  log "تازه‌ترین پشتیبان: $(basename "${DUMP_FILE}")"
fi

[[ -f "${DUMP_FILE}" ]] || die "فایل پشتیبان وجود ندارد: ${DUMP_FILE}"

MANIFEST="${DUMP_FILE%.dump}.dump.json"
[[ -f "${MANIFEST}" ]] || die "بیانیهٔ پشتیبان پیدا نشد: ${MANIFEST}"

# ── ۱) تطبیق هش: خرابی یا دست‌کاری فایل در همین لحظه کشف می‌شود ──
readonly ACTUAL_SHA="$(sha256sum "${DUMP_FILE}" | awk '{print $1}')"
readonly MANIFEST_SHA="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["sha256"])' "${MANIFEST}")"
[[ "${ACTUAL_SHA}" == "${MANIFEST_SHA}" ]] \
  || die "هش فایل با بیانیه نمی‌خواند! فایل پشتیبان خراب یا دست‌کاری شده است."
if [[ -n "${EXPECT_SHA}" && "${ACTUAL_SHA}" != "${EXPECT_SHA}" ]]; then
  die "هش فایل با مقدار انتظار (--expect-sha256) نمی‌خواند."
fi
log "هش تأیید شد: ${ACTUAL_SHA:0:16}…"

# ── ۲) بررسی محتوای آرشیو قبل از لمس پایگاه‌داده ──
pg_restore --list "${DUMP_FILE}" >/dev/null || die "آرشیو خوانده نمی‌شود."
log "آرشیو خوانده شد."

echo
echo "  هدف بازیابی : ${TARGET_DB}"
echo "  فایل        : $(basename "${DUMP_FILE}")"
echo "  ساخت        : $(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["created_at"])' "${MANIFEST}")"
echo "  نسخهٔ کد    : $(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["release"])' "${MANIFEST}")"
echo
if ((!ASSUME_YES)); then
  read -r -p "برای ادامه «restore» را بنویسید: " reply
  [[ "${reply}" == "restore" ]] || die "توسط کاربر لغو شد."
fi

# ── ۳) پشتیبان نجات: اگر بازیابی بد پیش برود، همین فایل نجات‌مان می‌دهد ──
if [[ "${TARGET_DB}" == "${DATABASE_NAME:-mahdieh}" ]]; then
  log "پشتیبان نجات از وضعیت فعلی پیش از بازیابی…"
  "${SCRIPT_DIR}/db_backup.sh" --label "pre-restore" >/dev/null \
    || die "پشتیبان نجات شکست خورد — بازیابی متوقف شد (قاعدهٔ ایمنی)."
fi

# ── ۴) ساخت پایگاه‌دادهٔ هدف در صورت نبود ──
readonly ADMIN_URL="${PGURL%/*}/postgres"
if ! psql "${ADMIN_URL}" -Atqc "SELECT 1 FROM pg_database WHERE datname='${TARGET_DB}'" | grep -q 1; then
  log "ساخت پایگاه‌دادهٔ ${TARGET_DB}…"
  psql "${ADMIN_URL}" -v ON_ERROR_STOP=1 -qc "CREATE DATABASE \"${TARGET_DB}\""
fi
if ((DROP_TARGET)); then
  log "خالی‌سازی کامل ${TARGET_DB} (--drop-target)…"
  psql "${PGURL%/*}/${TARGET_DB}" -v ON_ERROR_STOP=1 -qc "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
fi

# ── ۵) بازیابی. --exit-on-error: شکست جزئی «موفقیت» شمرده نمی‌شود ──
TARGET_URL="${PGURL%/*}/${TARGET_DB}"
log "شروع بازیابی در ${TARGET_DB}…"
pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error \
           --dbname="${TARGET_URL}" "${DUMP_FILE}" \
  || die "pg_restore شکست خورد (بازیابی ناقص است؛ از پشتیبان نجات استفاده کنید)."
log "بازیابی کامل شد."

# ── ۶) تأیید پس از بازیابی: شمارش ردیف‌ها + زنجیرهٔ حسابرسی ──
log "شمارش دقیق ردیف‌های کلیدی (count(*) واقعی، نه آمار تقریبی):"
psql "${TARGET_URL}" -At -F' | ' -c "
  SELECT 'رزروها      ', count(*)::text FROM reservation_reservation
  UNION ALL SELECT 'کاربران     ', count(*)::text FROM identity_user
  UNION ALL SELECT 'واحدها      ', count(*)::text FROM inventory_unit
  UNION ALL SELECT 'حسابرسی     ', count(*)::text FROM audit_auditrecord
  UNION ALL SELECT 'رخداد صندوق ', count(*)::text FROM core_outboxevent
  UNION ALL SELECT 'نوبت‌ها     ', count(*)::text FROM waitlist_waitlistentry
  UNION ALL SELECT 'نظرسنجی‌ها  ', count(*)::text FROM reservation_guestfeedback
  UNION ALL SELECT 'پیامک‌ها    ', count(*)::text FROM notifications_smsdelivery
  ORDER BY 1;" | sed 's/^/    /'

log "بررسی زنجیرهٔ حسابرسی روی پایگاه‌دادهٔ بازیابی‌شده:"
if run_manage "${TARGET_URL}" /tmp/restore_audit.log audit_verify; then
  sed 's/^/    /' /tmp/restore_audit.log
  log "✅ زنجیرهٔ حسابرسی در نسخهٔ بازیابی‌شده سالم است."
else
  sed 's/^/    /' /tmp/restore_audit.log
  log "⚠️ زنجیرهٔ حسابرسی تأیید نشد — پیش از اعلام موفقیت، این را بررسی کنید."
  exit 1
fi

log "تمام. برای اجرای سامانه روی این نسخه:"
log "    DATABASE_URL=${TARGET_URL} … gunicorn config.wsgi"
