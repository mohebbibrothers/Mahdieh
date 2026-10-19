#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# تمرین بازیابی فاجعه (DR drill) — بازیابی **واقعی و تأییدشده** در پایگاه‌دادهٔ قرنطینه.
#
# این اسکریپت به یک پرسش پاسخ می‌دهد: «اگر همین حالا پایگاه‌داده از دست برود،
# چند دقیقه بعد سامانه برمی‌گردد و آیا داده سالم است؟»
#
# مراحل:
#   ۱) پشتیبان تازه می‌گیرد (یا از --file استفاده می‌کند)
#   ۲) آن را در پایگاه‌دادهٔ قرنطینه بازیابی می‌کند (هرگز به تولید دست نمی‌زند)
#   ۳) **شمارش ردیف‌های کلیدی، تریگرها و زنجیرهٔ حسابرسی** را مقایسه می‌کند
#   ۴) گزارش تمرین را در var/drills/ می‌نویسد (شاهد قابل‌استناد، نه ادعا)
#   ۵) جدول‌های قرنطینه را پاک می‌کند (خود پایگاه‌داده برای تمرین بعد می‌ماند)
#
# استفاده:
#   MAHDIEH_DRILL_DB=mahdiesh_drill scripts/dr_drill.sh
#   scripts/dr_drill.sh --keep          # قرنطینه را برای بازرسی نگه دار
#
# روی cPanel پایگاه‌دادهٔ قرنطینه یک بار با uapi ساخته می‌شود (CREATE DATABASE
# برای کاربر میزبان مجاز نیست) و همان کاربر برنامه روی آن دسترسی دارد.
# ─────────────────────────────────────────────────────────────────────────────
set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

: "${MAHDIEH_BACKUP_DIR:=${REPO_ROOT}/var/backups}"
: "${MAHDIEH_DRILL_DIR:=${REPO_ROOT}/var/drills}"

DUMP_FILE=""
KEEP=0
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_SECONDS="${SECONDS}"

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

# shellcheck source=scripts/_db_common.sh
source "${SCRIPT_DIR}/_db_common.sh"
: "${MAHDIEH_DRILL_DB:?MAHDIEH_DRILL_DB تعیین نشده است (نام پایگاه‌دادهٔ قرنطینه)}"
readonly PROD_DB="${DB_NAME}"
[[ "${MAHDIEH_DRILL_DB}" != "${PROD_DB}" ]] || die "قرنطینه نمی‌تواند همان پایگاه‌دادهٔ تولید باشد!"

# ── ۱) پشتیبان تازه (یا فایل داده‌شده) ──
if [[ -z "${DUMP_FILE}" ]]; then
  log "پشتیبان تازه برای تمرین…"
  "${SCRIPT_DIR}/db_backup.sh" --label drill >/dev/null || die "پشتیبان‌گیری شکست خورد."
  DUMP_FILE="$(find "${MAHDIEH_BACKUP_DIR}" -maxdepth 1 -name 'mahdieh-*-drill.sql.gz' \
                 -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
fi
[[ -f "${DUMP_FILE}" ]] || die "فایل پشتیبان پیدا نشد."
log "فایل تمرین: $(basename "${DUMP_FILE}")"

# ── ۲) بازیابی در قرنطینه ──
"${SCRIPT_DIR}/db_restore.sh" --file "${DUMP_FILE}" --target "${MAHDIEH_DRILL_DB}" --yes >/dev/null \
  || die "بازیابی در قرنطینه شکست خورد — این یک یافتهٔ بحرانی است."
log "بازیابی انجام شد."

# ── ۳) مقایسهٔ تولید و قرنطینه ──
# پشتیبان «تازه» است ولی سایت زنده است: در فاصلهٔ چند ثانیه‌ای، رخداد تازه‌ای
# ممکن است ثبت شود. پس تنها وقتی فایل تازه است مقایسهٔ دقیق معنا دارد؛ در
# حالت --file فقط وجود و سلامت سنجیده می‌شود.
PROD_COUNTS="$(db_sql "${PROD_DB}" "${KEY_COUNTS_SQL}")"
DRILL_COUNTS="$(db_sql "${MAHDIEH_DRILL_DB}" "${KEY_COUNTS_SQL}")"
# پرونده‌های موقت به‌جای جایگزینی فرآیند: CageFS میزبان /dev/fd ندارد.
PROD_SORTED="$(mktemp "${TMPDIR:-/tmp}/mahdieh-drill-prod.XXXXXX")"
DRILL_SORTED="$(mktemp "${TMPDIR:-/tmp}/mahdieh-drill-copy.XXXXXX")"
_DB_TEMP_FILES+=("${PROD_SORTED}" "${DRILL_SORTED}")
sort <<<"${PROD_COUNTS}" >"${PROD_SORTED}"
sort <<<"${DRILL_COUNTS}" >"${DRILL_SORTED}"
MISMATCHES="$(diff "${PROD_SORTED}" "${DRILL_SORTED}" || true)"
if [[ -n "${MISMATCHES}" ]]; then
  log "⚠️ اختلاف بین تولید و نسخهٔ بازیابی‌شده:"
  printf '%s\n' "${MISMATCHES}" | sed 's/^/    /'
else
  log "✅ شمارش ردیف‌ها و تریگرهای تولید و نسخهٔ بازیابی‌شده یکسان است."
fi

# ── ۴) زنجیرهٔ حسابرسی روی نسخهٔ بازیابی‌شده ──
AUDIT_LOG="$(mktemp "${TMPDIR:-/tmp}/mahdieh-drill-audit.XXXXXX")"
_DB_TEMP_FILES+=("${AUDIT_LOG}")
if run_manage "$(db_url_for "${MAHDIEH_DRILL_DB}")" "${AUDIT_LOG}" audit_verify; then
  CHAIN_OK="true"
  log "✅ زنجیرهٔ حسابرسی در نسخهٔ بازیابی‌شده معتبر است."
else
  CHAIN_OK="false"
  log "⚠️ بررسی زنجیرهٔ حسابرسی موفق نبود."
fi
CHAIN_OUTPUT="$(tail -3 "${AUDIT_LOG}" 2>/dev/null | tr '\n' ' ')"

# ── ۵) گزارش تمرین (شاهد قابل‌استناد) ──
readonly ELAPSED=$((SECONDS - START_SECONDS))
mkdir -p "${MAHDIEH_DRILL_DIR}"
readonly REPORT="${MAHDIEH_DRILL_DIR}/$(date -u +%Y-%m-%d)-drill.md"
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
  echo "## شمارش ردیف‌ها (تولید | بازیابی‌شده)"
  echo
  echo '```'
  paste "${PROD_SORTED}" "${DRILL_SORTED}"
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
log "گزارش تمرین: ${REPORT}"

# ── ۶) پاک‌سازی قرنطینه (جدول‌ها؛ خود پایگاه‌داده برای تمرین بعد می‌ماند) ──
if ((KEEP)); then
  log "قرنطینه به‌درخواست بازرسی نگه داشته شد: ${MAHDIEH_DRILL_DB}"
else
  LEFT_RAW="$(db_sql "${MAHDIEH_DRILL_DB}" \
    "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()")"
  LEFT=()
  if [[ -n "${LEFT_RAW}" ]]; then mapfile -t LEFT <<<"${LEFT_RAW}"; fi
  if ((${#LEFT[@]})); then
    DROP_SQL="SET FOREIGN_KEY_CHECKS=0;"
    for table in "${LEFT[@]}"; do DROP_SQL+=" DROP TABLE IF EXISTS \`${table}\`;"; done
    db_sql "${MAHDIEH_DRILL_DB}" "${DROP_SQL}" || log "⚠️ پاک‌سازی قرنطینه کامل نشد."
  fi
  log "قرنطینه پاک شد."
fi

if [[ -n "${MISMATCHES}" || "${CHAIN_OK}" != "true" ]]; then
  die "تمرین بازیابی ناسالم است — پیش از حادثهٔ واقعی رفع کنید."
fi
log "تمرین بازیابی با موفقیت تمام شد (مدت: ${ELAPSED} ثانیه)."
