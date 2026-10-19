#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# بازیابی پایگاه‌دادهٔ مهدیه (MariaDB) — «بازیابیِ امن، تأییدشده و بازگشت‌پذیر».
#
# چرا این‌قدر محافظه‌کارانه؟ چون بازیابی روی پایگاه‌دادهٔ زنده یک عملیات
# برگشت‌ناپذیر است. این اسکریپت پیش از هر تغییری:
#   • هش SHA-256 فایل را با بیانیهٔ پشتیبان تطبیق می‌دهد (ضد خرابی/دست‌کاری)
#   • یکپارچگی آرشیو و کامل‌بودن dump را می‌سنجد
#   • اگر هدف، پایگاه‌دادهٔ تولید باشد، نخست پشتیبان نجات (rescue) می‌گیرد
# سپس همهٔ جدول‌های هدف را پاک و آرشیو را بازیابی می‌کند و در پایان شمارش
# ردیف‌های کلیدی، تریگرهای نگهبان و صحت زنجیرهٔ حسابرسی را گزارش می‌دهد.
#
# استفاده:
#   scripts/db_restore.sh --file var/backups/mahdieh-20261016T...Z-manual.sql.gz
#   scripts/db_restore.sh --latest --yes
#   scripts/db_restore.sh --latest --target mahdiesh_drill   # بی‌تماس با تولید
#
# نکته: روی میزبان cPanel کاربر اجازهٔ CREATE DATABASE ندارد؛ پایگاه‌دادهٔ هدف
# باید از پیش (با uapi) ساخته شده باشد. هرگز روی تولید تمرین نکنید.
# ─────────────────────────────────────────────────────────────────────────────
set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

: "${MAHDIEH_BACKUP_DIR:=${REPO_ROOT}/var/backups}"

DUMP_FILE=""
TARGET_DB=""
EXPECT_SHA=""
ASSUME_YES=0
USE_LATEST=0

log() { printf '%s [restore] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { printf '%s [restore][خطا] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --file)          DUMP_FILE="${2:?--file به مسیر نیاز دارد}"; shift 2 ;;
    --latest)        USE_LATEST=1; shift ;;
    --target)        TARGET_DB="${2:?--target به نام پایگاه‌داده نیاز دارد}"; shift 2 ;;
    --expect-sha256) EXPECT_SHA="${2:?}"; shift 2 ;;
    -y|--yes)        ASSUME_YES=1; shift ;;
    -h|--help)       sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "آرگومان ناشناخته: $1" ;;
  esac
done

[[ -n "${DUMP_FILE}" || ${USE_LATEST} -eq 1 ]] || die "یکی از --file یا --latest لازم است."

# shellcheck source=scripts/_db_common.sh
source "${SCRIPT_DIR}/_db_common.sh"
[[ -n "${MARIADB_BIN}" ]] || die "کلاینت mariadb روی این میزبان نصب نیست."
readonly PROD_DB="${DB_NAME}"
TARGET_DB="${TARGET_DB:-${PROD_DB}}"
[[ "${TARGET_DB}" =~ ^[A-Za-z0-9_]+$ ]] || die "نام پایگاه‌دادهٔ هدف نامعتبر است: ${TARGET_DB}"

if ((USE_LATEST)); then
  DUMP_FILE="$(find "${MAHDIEH_BACKUP_DIR}" -maxdepth 1 -name 'mahdieh-*.sql.gz' -printf '%T@ %p\n' \
                | sort -nr | head -1 | cut -d' ' -f2-)"
  [[ -n "${DUMP_FILE}" ]] || die "هیچ پشتیبانی در ${MAHDIEH_BACKUP_DIR} پیدا نشد."
  log "تازه‌ترین پشتیبان: $(basename "${DUMP_FILE}")"
fi

[[ -f "${DUMP_FILE}" ]] || die "فایل پشتیبان وجود ندارد: ${DUMP_FILE}"
MANIFEST="${DUMP_FILE}.json"
[[ -f "${MANIFEST}" ]] || die "بیانیهٔ پشتیبان پیدا نشد: ${MANIFEST}"
manifest() { "${PYTHON_BIN}" -c 'import json,sys;print(json.load(open(sys.argv[1]))[sys.argv[2]])' "${MANIFEST}" "$1"; }

# ── ۱) تطبیق هش: خرابی یا دست‌کاری فایل در همین لحظه کشف می‌شود ──
readonly ACTUAL_SHA="$(sha256sum "${DUMP_FILE}" | awk '{print $1}')"
[[ "${ACTUAL_SHA}" == "$(manifest sha256)" ]] \
  || die "هش فایل با بیانیه نمی‌خواند! فایل پشتیبان خراب یا دست‌کاری شده است."
if [[ -n "${EXPECT_SHA}" && "${ACTUAL_SHA}" != "${EXPECT_SHA}" ]]; then
  die "هش فایل با مقدار انتظار (--expect-sha256) نمی‌خواند."
fi
log "هش تأیید شد: ${ACTUAL_SHA:0:16}…"

# ── ۲) بررسی آرشیو پیش از لمس پایگاه‌داده ──
gzip -t "${DUMP_FILE}" || die "آرشیو gzip خراب است."
gzip -dc "${DUMP_FILE}" | tail -n 3 | grep -q -- "-- Dump completed" || die "dump ناقص است."
if gzip -dc "${DUMP_FILE}" | grep -qiE '^(CREATE DATABASE|USE `)'; then
  die "آرشیو به پایگاه‌دادهٔ مشخصی گره خورده است (CREATE DATABASE/USE) — بازیابی امن نیست."
fi
log "آرشیو سالم و کامل است."

echo
echo "  هدف بازیابی : ${TARGET_DB}$([[ "${TARGET_DB}" == "${PROD_DB}" ]] && echo '  ⚠ پایگاه‌دادهٔ تولید')"
echo "  فایل        : $(basename "${DUMP_FILE}")"
echo "  ساخت        : $(manifest created_at)"
echo "  نسخهٔ کد    : $(manifest release)"
echo
if ((!ASSUME_YES)); then
  read -r -p "برای ادامه «restore» را بنویسید: " reply
  [[ "${reply}" == "restore" ]] || die "توسط کاربر لغو شد."
fi

# ── ۳) پشتیبان نجات: اگر بازیابی بد پیش برود، همین فایل نجات‌مان می‌دهد ──
if [[ "${TARGET_DB}" == "${PROD_DB}" ]]; then
  log "پشتیبان نجات از وضعیت فعلی پیش از بازیابی…"
  "${SCRIPT_DIR}/db_backup.sh" --label "pre-restore" >/dev/null \
    || die "پشتیبان نجات شکست خورد — بازیابی متوقف شد (قاعدهٔ ایمنی)."
fi

# ── ۴) خالی‌سازی هدف: همهٔ جدول‌ها (و تریگرهایشان) حذف می‌شوند ──
db_sql "${TARGET_DB}" "SELECT 1" >/dev/null || die "پایگاه‌دادهٔ هدف در دسترس نیست: ${TARGET_DB}"
mapfile -t EXISTING < <(db_sql "${TARGET_DB}" \
  "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()")
if ((${#EXISTING[@]})); then
  log "خالی‌سازی ${#EXISTING[@]} جدول در ${TARGET_DB}…"
  DROP_SQL="SET FOREIGN_KEY_CHECKS=0;"
  for table in "${EXISTING[@]}"; do DROP_SQL+=" DROP TABLE IF EXISTS \`${table}\`;"; done
  db_sql "${TARGET_DB}" "${DROP_SQL} SET FOREIGN_KEY_CHECKS=1;" || die "خالی‌سازی هدف شکست خورد."
fi

# ── ۵) بازیابی؛ هر خطا کل عملیات را شکست می‌دهد (نه «موفقیت جزئی») ──
log "شروع بازیابی در ${TARGET_DB}…"
gzip -dc "${DUMP_FILE}" \
  | "${MARIADB_BIN}" --defaults-extra-file="${DB_DEFAULTS}" --database="${TARGET_DB}" \
  || die "بازیابی شکست خورد (ناقص است؛ از پشتیبان نجات استفاده کنید)."
log "بازیابی کامل شد."

# ── ۶) تأیید پس از بازیابی: شمارش ردیف‌ها + تریگرها + زنجیرهٔ حسابرسی ──
log "شمارش دقیق ردیف‌های کلیدی:"
db_sql "${TARGET_DB}" "${KEY_COUNTS_SQL}" | sed 's/^/    /'
TRIGGERS="$(db_sql "${TARGET_DB}" "SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME LIKE '%no_overlap_b%'")"
[[ "${TRIGGERS}" -ge 4 ]] || die "تریگرهای نگهبان بازیابی نشدند (${TRIGGERS}/4)."
log "✅ هر چهار تریگر نگهبان حاضرند."

log "بررسی زنجیرهٔ حسابرسی روی پایگاه‌دادهٔ بازیابی‌شده:"
AUDIT_LOG="$(mktemp "${TMPDIR:-/tmp}/mahdieh-audit.XXXXXX")"
_DB_TEMP_FILES+=("${AUDIT_LOG}")
if run_manage "$(db_url_for "${TARGET_DB}")" "${AUDIT_LOG}" audit_verify; then
  sed 's/^/    /' "${AUDIT_LOG}"
  log "✅ زنجیرهٔ حسابرسی در نسخهٔ بازیابی‌شده سالم است."
else
  sed 's/^/    /' "${AUDIT_LOG}"
  die "زنجیرهٔ حسابرسی تأیید نشد — پیش از اعلام موفقیت، این را بررسی کنید."
fi
log "تمام."
