#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# پشتیبان‌گیری پایگاه‌دادهٔ مهدیه (MariaDB) — «پشتیبانِ تأییدشده»، نه فقط پشتیبان.
#
# فلسفه: پشتیبانی که بازیابی نشده باشد، پشتیبان نیست؛ یک آرزو است.
# این اسکریپت سه کار می‌کند:
#   ۱) mariadb-dump سازگار (--single-transaction: بی‌قفل و بی‌توقف سایت) +
#      تریگرهای نگهبان، فشرده با gzip
#   ۲) هش SHA-256 برای تشخیص خرابی/دست‌کاری فایل
#   ۳) صحت‌سنجی: یکپارچگی gzip، نشانهٔ پایان dump، شمار جدول‌ها و تریگرها
# و در پایان یک «بیانیهٔ پشتیبان» (manifest JSON) می‌نویسد که در بازیابی
# بررسی می‌شود — پس هیچ‌وقت نمی‌فهمیم پشتیبان خراب بوده *بعد* از حادثه.
#
# استفاده:
#   scripts/db_backup.sh                     # پشتیبان کامل در var/backups
#   MAHDIEH_BACKUP_DIR=~/backups scripts/db_backup.sh --label nightly
#   scripts/db_backup.sh --no-verify         # فقط در شرایط اضطراری
# ─────────────────────────────────────────────────────────────────────────────
set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

: "${MAHDIEH_BACKUP_DIR:=${REPO_ROOT}/var/backups}"
: "${MAHDIEH_RETENTION_DAYS:=14}"
: "${MAHDIEH_PREFIX:=mahdieh}"

VERIFY=1
LABEL="${MAHDIEH_BACKUP_LABEL:-manual}"

log()  { printf '%s [backup] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die()  { printf '%s [backup][خطا] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --no-verify) VERIFY=0; shift ;;
    --label) LABEL="${2:?--label به یک مقدار نیاز دارد}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "آرگومان ناشناخته: $1" ;;
  esac
done

# shellcheck source=scripts/_db_common.sh
source "${SCRIPT_DIR}/_db_common.sh"
[[ -n "${MARIADB_DUMP_BIN}" ]] || die "mariadb-dump روی این میزبان نصب نیست."
for binary in gzip sha256sum; do
  command -v "${binary}" >/dev/null 2>&1 || die "${binary} روی این میزبان نصب نیست."
done

# ── نام فایل با مُهر زمانی UTC + برچسب (برای تفکیک پشتیبان دستی/زمان‌بند) ──
readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
# نسخهٔ کد: متغیر صریح ⇐ BUILD_INFO.json بستهٔ انتشار (میزبان بدون git) ⇐ git ⇐ unknown
_release_from_bundle() {
  [[ -f "${REPO_ROOT}/BUILD_INFO.json" ]] || return 1
  "${PYTHON_BIN}" -c 'import json,sys; print(json.load(open(sys.argv[1]))["release"])' \
    "${REPO_ROOT}/BUILD_INFO.json" 2>/dev/null
}
readonly RELEASE="${MAHDIEH_RELEASE:-$(_release_from_bundle || (cd "${REPO_ROOT}" && git rev-parse --short HEAD 2>/dev/null) || echo 'unknown')}"
mkdir -p "${MAHDIEH_BACKUP_DIR}"
chmod 700 "${MAHDIEH_BACKUP_DIR}"
readonly BASENAME="${MAHDIEH_PREFIX}-${STAMP}-${LABEL}"
readonly DUMP_PATH="${MAHDIEH_BACKUP_DIR}/${BASENAME}.sql.gz"
readonly MANIFEST_PATH="${DUMP_PATH}.json"
readonly PARTIAL_PATH="${DUMP_PATH}.partial"
_DB_TEMP_FILES+=("${PARTIAL_PATH}")

log "شروع پشتیبان‌گیری از «${DB_NAME}» (نسخهٔ کد: ${RELEASE})"

# ── ۱) خودِ پشتیبان ──
# --single-transaction: تصویر سازگار InnoDB بدون قفل جدول (سایت بالا می‌ماند).
# --triggers: نگهبان‌های عدم‌هم‌پوشانی جزء طرح‌واره‌اند و باید برگردند.
# --no-tablespaces: کاربر میزبان اشتراکی مجوز PROCESS ندارد.
# نام پایگاه‌داده عمداً بدون --databases داده می‌شود: dump هیچ «CREATE DATABASE/USE»
# ندارد، پس بازیابی در قرنطینهٔ تمرین هرگز به پایگاه‌دادهٔ تولید نمی‌نویسد.
# فایل نیمه‌کاره با پسوند .partial نوشته و فقط پس از موفقیت جابه‌جا می‌شود.
"${MARIADB_DUMP_BIN}" --defaults-extra-file="${DB_DEFAULTS}" \
    --single-transaction --quick --triggers --routines --hex-blob \
    --no-tablespaces --default-character-set=utf8mb4 --dump-date \
    "${DB_NAME}" \
  | gzip -6 > "${PARTIAL_PATH}" \
  || die "mariadb-dump شکست خورد."

# ── ۲) صحت‌سنجی ساختاری روی فایل نیمه‌کاره: سالم، کامل، جدول‌ها و تریگرها ──
# پشتیبانِ ردشده هرگز نام نهایی نمی‌گیرد (و با خروج اسکریپت پاک می‌شود).
TABLES=""
if ((VERIFY)); then
  gzip -t "${PARTIAL_PATH}" || die "آرشیو gzip خراب است."
  gzip -dc "${PARTIAL_PATH}" | tail -n 3 | grep -q -- "-- Dump completed" \
    || die "نشانهٔ پایان dump پیدا نشد — پشتیبان ناقص است."
  TABLES="$(gzip -dc "${PARTIAL_PATH}" | sed -n 's/^CREATE TABLE `\([^`]*\)`.*/\1/p' | sort | paste -sd, -)"
  TABLES_COUNT="$(tr ',' '\n' <<<"${TABLES}" | grep -c . || true)"
  TRIGGERS_COUNT="$(gzip -dc "${PARTIAL_PATH}" | grep -cE 'TRIGGER `?[a-z_]*no_overlap_b[iu]`? ' || true)"
  [[ "${TABLES_COUNT}" -gt 0 ]] || die "آرشیو هیچ جدولی ندارد — پشتیبان بی‌ارزش است."
  [[ "${TRIGGERS_COUNT}" -ge 4 ]] || die "تریگرهای نگهبان در آرشیو نیستند (${TRIGGERS_COUNT}/4)."
  log "صحت ساختاری تأیید شد: ${TABLES_COUNT} جدول، ${TRIGGERS_COUNT} تریگر نگهبان."
else
  log "هشدار: صحت‌سنجی با --no-verify غیرفعال شد (فقط در اضطرار)."
fi
mv "${PARTIAL_PATH}" "${DUMP_PATH}"

readonly SIZE_BYTES="$(stat -c '%s' "${DUMP_PATH}")"
readonly SHA="$(sha256sum "${DUMP_PATH}" | awk '{print $1}')"
log "آرشیو ساخته شد: $(basename "${DUMP_PATH}") ($(numfmt --to=iec "${SIZE_BYTES}" 2>/dev/null || echo "${SIZE_BYTES} بایت"))"
log "SHA-256: ${SHA}"

# ── ۳) بیانیهٔ پشتیبان: بازیابی، این فایل را می‌خواند و هش را بررسی می‌کند ──
"${PYTHON_BIN}" - "${MANIFEST_PATH}" "${BASENAME}" "${SIZE_BYTES}" "${SHA}" "${RELEASE}" "${VERIFY}" "${TABLES}" "${DB_NAME}" <<'PY'
import datetime
import json
import sys

manifest_path, name, size, sha, release, verified, tables, database = sys.argv[1:9]
payload = {
    "schema_version": 2,
    "engine": "mariadb",
    "name": name,
    "file": name + ".sql.gz",
    "database": database,
    "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
    "size_bytes": int(size),
    "sha256": sha,
    "release": release,
    "structurally_verified": bool(int(verified)),
    "tables": [t for t in tables.split(",") if t],
    "restore_command": (
        f"scripts/db_restore.sh --file var/backups/{name}.sql.gz --expect-sha256 {sha}"
    ),
}
with open(manifest_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
log "بیانیهٔ پشتیبان نوشته شد: $(basename "${MANIFEST_PATH}")"

# ── ۴) چرخش پشتیبان‌های کهنه (پیش‌فرض ۱۴ روز) ──
DELETED=0
while IFS= read -r stale; do
  [[ -n "${stale}" ]] || continue
  rm -f "${stale}" "${stale}.json"
  DELETED=$((DELETED + 1))
done < <(find "${MAHDIEH_BACKUP_DIR}" -maxdepth 1 -name "${MAHDIEH_PREFIX}-*.sql.gz" \
           -mtime "+${MAHDIEH_RETENTION_DAYS}" -print)
((DELETED > 0)) && log "چرخش نگه‌داری: ${DELETED} پشتیبان قدیمی حذف شد."
log "تمام. مسیر: ${DUMP_PATH}"
