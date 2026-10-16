#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# تمرین بازیابی فاجعه (DR drill) — بازیابی **واقعی و تأییدشده** در پایگاه‌دادهٔ قرنطینه.
#
# این اسکریپت به یک پرسش پاسخ می‌دهد: «اگر همین حالا پایگاه‌داده از دست برود،
# چند دقیقه بعد سامانه برمی‌گردد و آیا داده سالم است؟»
#
# مراحل:
#   ۱) پشتیبان تازه می‌گیرد (یا از --file استفاده می‌کند)
#   ۲) پایگاه‌دادهٔ قرنطینه می‌سازد (هرگز به پایگاه‌دادهٔ prod دست نمی‌زند)
#   ۳) بازیابی می‌کند و **شمارش ردیف‌های کلیدی + زنجیرهٔ حسابرسی** را مقایسه می‌کند
#   ۴) گزارش تمرین را در docs/drills/ می‌نویسد (شاهد قابل‌استناد، نه ادعا)
#   ۵) قرنطینه را پاک می‌کند
#
# استفاده:
#   scripts/dr_drill.sh                 # پشتیبان تازه + تمرین کامل
#   scripts/dr_drill.sh --keep          # قرنطینه را برای بازرسی نگه دار
#   MAHDIEH_DRILL_DB=mahdieh_drill scripts/dr_drill.sh
# ─────────────────────────────────────────────────────────────────────────────
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly DRILL_DIR="${REPO_ROOT}/docs/drills"

: "${MAHDIEH_BACKUP_DIR:=${REPO_ROOT}/var/backups}"
: "${MAHDIEH_DRILL_DB:=mahdieh_drill}"

DUMP_FILE=""
KEEP=0
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_SECONDS="${SECONDS}"

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

log() { printf '%s [drill] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { printf '%s [drill][خطا] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --file) DUMP_FILE="${2:?}"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "آرگومان ناشناخته: $1" ;;
  esac
done

if [[ -n "${DATABASE_URL:-}" ]]; then
  export PGURL="${DATABASE_URL}"
else
  : "${DATABASE_HOST:=127.0.0.1}"
  : "${DATABASE_PORT:=5432}"
  : "${DATABASE_NAME:=mahdieh}"
  : "${DATABASE_USER:=mahdieh}"
  export PGPASSWORD="${DATABASE_PASSWORD:-}"
  export PGURL="postgresql://${DATABASE_USER}@${DATABASE_HOST}:${DATABASE_PORT}/${DATABASE_NAME}"
fi

readonly ADMIN_URL="${PGURL%/*}/postgres"
readonly PROD_URL="${PGURL}"
readonly DRILL_URL="${PGURL%/*}/${MAHDIEH_DRILL_DB}"

# ── ۰) پاک‌سازی هر قرنطینهٔ جامانده و ساخت قرنطینهٔ تازه ──
psql "${ADMIN_URL}" -v ON_ERROR_STOP=1 -qc "DROP DATABASE IF EXISTS \"${MAHDIEH_DRILL_DB}\"" \
  || die "پاک‌سازی قرنطینهٔ قبلی شکست خورد."
psql "${ADMIN_URL}" -v ON_ERROR_STOP=1 -qc "CREATE DATABASE \"${MAHDIEH_DRILL_DB}\"" \
  || die "ساخت قرنطینه شکست خورد."
log "قرنطینهٔ ${MAHDIEH_DRILL_DB} آماده است."

# ── ۱) پشتیبان (تازه یا داده‌شده) ──
if [[ -z "${DUMP_FILE}" ]]; then
  log "گرفتن پشتیبان تازه برای تمرین…"
  MAHDIEH_BACKUP_LABEL=drill "${SCRIPT_DIR}/db_backup.sh" --label drill >/dev/null \
    || die "پشتیبان‌گیری شکست خورد."
  DUMP_FILE="$(find "${MAHDIEH_BACKUP_DIR}" -maxdepth 1 -name '*drill.dump' -printf '%T@ %p\n' \
                | sort -nr | head -1 | cut -d' ' -f2-)"
fi
[[ -f "${DUMP_FILE}" ]] || die "فایل پشتیبان پیدا نشد."

# ── ۲) بازیابی در قرنطینه ──
log "بازیابی در قرنطینه…"
MAHDIEH_BACKUP_DIR="${MAHDIEH_BACKUP_DIR}" \
  "${SCRIPT_DIR}/db_restore.sh" --file "${DUMP_FILE}" --target "${MAHDIEH_DRILL_DB}" --yes >/dev/null \
  || die "بازیابی در قرنطینه شکست خورد — این یک یافتهٔ بحرانی است."
log "بازیابی انجام شد."

# ── ۳) مقایسهٔ تولید و قرنطینه: شمارش ردیف‌ها + فراداده‌های حیاتی ──
readonly COMPARE_SQL="
  SELECT 'reservations' AS metric, count(*)::text AS value FROM reservation_reservation
  UNION ALL SELECT 'users', count(*)::text FROM identity_user
  UNION ALL SELECT 'units', count(*)::text FROM inventory_unit
  UNION ALL SELECT 'audit_records', count(*)::text FROM audit_auditrecord
  UNION ALL SELECT 'outbox_events', count(*)::text FROM core_outboxevent
  UNION ALL SELECT 'feedback', count(*)::text FROM reservation_guestfeedback
  UNION ALL SELECT 'waitlist', count(*)::text FROM waitlist_waitlistentry
  UNION ALL SELECT 'sms_deliveries', count(*)::text FROM notifications_smsdelivery
  UNION ALL SELECT 'migrations', count(*)::text FROM django_migrations
  ORDER BY metric;"

psql "${PROD_URL}"  -At -F'|' -c "${COMPARE_SQL}" > /tmp/drill_prod.txt
psql "${DRILL_URL}" -At -F'|' -c "${COMPARE_SQL}" > /tmp/drill_restored.txt

MISMATCHES="$(diff <(sort /tmp/drill_prod.txt) <(sort /tmp/drill_restored.txt) || true)"
if [[ -n "${MISMATCHES}" ]]; then
  log "⚠️ اختلاف بین تولید و نسخهٔ بازیابی‌شده:"
  printf '%s\n' "${MISMATCHES}" | sed 's/^/    /'
else
  log "✅ شمارش ردیف‌های تولید و نسخهٔ بازیابی‌شده یکسان است."
fi

# ── ۴) زنجیرهٔ حسابرسی روی نسخهٔ بازیابی‌شده باید سالم باشد ──
CHAIN_OUTPUT="یکپارچگی بررسی نشد"
CHAIN_OK="unknown"
if run_manage "${DRILL_URL}" /tmp/drill_audit.txt audit_verify; then
  CHAIN_OK="true"
  CHAIN_OUTPUT="$(tail -3 /tmp/drill_audit.txt | tr '\n' ' ')"
  log "✅ زنجیرهٔ حسابرسی در نسخهٔ بازیابی‌شده معتبر است."
else
  CHAIN_OK="false"
  CHAIN_OUTPUT="$(tail -3 /tmp/drill_audit.txt 2>/dev/null | tr '\n' ' ')"
  log "⚠️ بررسی زنجیرهٔ حسابرسی موفق نبود: ${CHAIN_OUTPUT}"
fi

# ── ۵) گزارش تمرین (شاهد قابل‌استناد) ──
readonly ELAPSED=$((SECONDS - START_SECONDS))
mkdir -p "${DRILL_DIR}"
readonly REPORT="${DRILL_DIR}/$(date -u +%Y-%m-%d)-drill.md"
{
  echo "# گزارش تمرین بازیابی فاجعه — $(date -u +%Y-%m-%d)"
  echo
  echo "| سنجه | مقدار |"
  echo "| --- | --- |"
  echo "| شروع | ${STARTED_AT} |"
  echo "| مدت کل (ثانیه) | ${ELAPSED} |"
  echo "| فایل پشتیبان | \`$(basename "${DUMP_FILE}")\` |"
  echo "| پایگاه‌دادهٔ قرنطینه | \`${MAHDIEH_DRILL_DB}\` |"
  echo "| یکسانی شمارش ردیف‌ها | $([[ -z "${MISMATCHES}" ]] && echo 'بله ✅' || echo 'خیر ❌') |"
  echo "| سلامت زنجیرهٔ حسابرسی | ${CHAIN_OK} |"
  echo
  echo "## شمارش ردیف‌ها (تولید در برابر بازیابی‌شده)"
  echo
  echo '```'
  paste -d'|' /tmp/drill_prod.txt /tmp/drill_restored.txt
  echo '```'
  echo
  echo "## خروجی بررسی حسابرسی"
  echo
  echo '```'
  echo "${CHAIN_OUTPUT}"
  echo '```'
  echo
  echo "> این گزارش به‌صورت خودکار با \`scripts/dr_drill.sh\` ساخته شده است."
} > "${REPORT}"
log "گزارش تمرین: ${REPORT#"${REPO_ROOT}/"}"

# ── ۶) پاک‌سازی قرنطینه ──
if ((KEEP)); then
  log "قرنطینه به‌درخواست بازرسی نگه داشته شد: ${MAHDIEH_DRILL_DB}"
else
  psql "${ADMIN_URL}" -v ON_ERROR_STOP=1 -qc "DROP DATABASE \"${MAHDIEH_DRILL_DB}\"" || true
  log "قرنطینه پاک شد."
fi

if [[ -n "${MISMATCHES}" || "${CHAIN_OK}" != "true" ]]; then
  die "تمرین بازیابی ناسالم است — پیش از حادثهٔ واقعی رفع کنید."
fi
log "تمرین بازیابی با موفقیت تمام شد (مدت: ${ELAPSED} ثانیه)."
