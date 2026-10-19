#!/usr/bin/env bash
# راه‌اندازی محیط توسعهٔ مهدیه از صفر — یک فرمان، محیط قابل اجرا.
#
# چرا وجود دارد؟ در محیط‌های ظرفی/ابری، ابزارها و سرویس‌ها با هر نشست تازه
# از بین می‌روند (uv، .venv، MariaDB). این اسکریپت همان کاری را
# می‌کند که یک مهندس با دست انجام می‌داد، ولی **بی‌اثرپذیر (idempotent)**:
# هر بار اجرا شود، فقط چیزهای غایب را می‌سازد و بقیه را دست نمی‌زند.
#
# استفاده:
#   ./scripts/dev_bootstrap.sh              # محیط + مهاجرت + دادهٔ واحدها
#   ./scripts/dev_bootstrap.sh --demo       # + دادهٔ نمایشی (رزروهای نمونه)
#   ./scripts/dev_bootstrap.sh --no-serve   # بدون راهنمای اجرای سرور
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DB_NAME="${MAHDIEH_DB_NAME:-mahdieh}"
DB_USER="${MAHDIEH_DB_USER:-mahdieh}"
DB_PASS="${MAHDIEH_DB_PASS:-mahdieh}"     # فقط محیط توسعهٔ محلی
DB_HOST="${MAHDIEH_DB_HOST:-127.0.0.1}"
DB_PORT="${MAHDIEH_DB_PORT:-3306}"
WITH_DEMO=0
SERVE=1

for arg in "$@"; do
  case "$arg" in
    --demo) WITH_DEMO=1 ;;
    --no-serve) SERVE=0 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "گزینهٔ ناشناخته: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m▸ %s\033[0m\n' "$1"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$1"; }
skip() { printf '  \033[1;90m•\033[0m %s\n' "$1"; }
warn() { printf '  \033[1;33m!\033[0m %s\n' "$1"; }

have_sudo() { command -v sudo >/dev/null && sudo -n true 2>/dev/null; }
as_root() { if have_sudo; then sudo "$@"; else "$@"; fi; }

# ── ۱) ابزار uv ───────────────────────────────────────────────────────────────
say "ابزار uv"
if command -v uv >/dev/null; then
  ok "uv موجود است ($(uv --version))"
else
  as_root python3 -m pip install --break-system-packages -q uv
  ok "uv نصب شد ($(uv --version))"
fi

# ── ۲) سرویس: MariaDB (همان موتور میزبان تولید؛ کش هم روی همین است) ─────────
say "سرویس محلی (MariaDB + ابزار ساخت mysqlclient)"
MISSING_PKGS=()
command -v mariadbd >/dev/null || [ -x /usr/sbin/mariadbd ] || MISSING_PKGS+=(mariadb-server)
[ -f /usr/include/mariadb/mysql.h ] || MISSING_PKGS+=(libmariadb-dev)
command -v pkg-config >/dev/null || MISSING_PKGS+=(pkg-config)
command -v gcc >/dev/null || MISSING_PKGS+=(build-essential)
if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
  as_root apt-get update -qq
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING_PKGS[@]}"
  ok "بسته‌ها نصب شدند: ${MISSING_PKGS[*]}"
else
  skip "MariaDB و ابزار ساخت از قبل نصب‌اند"
fi

db_ping() { mariadb -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASS" -e 'select 1' "$DB_NAME" >/dev/null 2>&1; }
if db_ping; then
  skip "MariaDB در حال اجرا است و پایگاه «$DB_NAME» پاسخ می‌دهد"
else
  if ! as_root mariadb -e 'select 1' >/dev/null 2>&1; then
    # بدون systemd (ظرف): خود سرور در پس‌زمینه. collation و جداسازی همانند تولید.
    as_root mkdir -p /run/mysqld && as_root chown mysql:mysql /run/mysqld 2>/dev/null || true
    as_root -u mysql nohup mariadbd --character-set-server=utf8mb4 \
      --collation-server=utf8mb4_uca1400_as_cs --transaction-isolation=READ-COMMITTED \
      --innodb-snapshot-isolation=OFF >/tmp/mariadbd.log 2>&1 &
    for _ in $(seq 1 30); do as_root mariadb -e 'select 1' >/dev/null 2>&1 && break; sleep 1; done
  fi
  as_root mariadb -e 'select 1' >/dev/null 2>&1 && ok "سرور MariaDB بالا آمد" || warn "MariaDB بالا نیامد"
  as_root mariadb <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_uca1400_as_cs;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
CREATE USER IF NOT EXISTS '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost', '$DB_USER'@'127.0.0.1';
GRANT ALL PRIVILEGES ON \`test\\_$DB_NAME%\`.* TO '$DB_USER'@'localhost', '$DB_USER'@'127.0.0.1';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\\_drill\`.* TO '$DB_USER'@'localhost', '$DB_USER'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
  db_ping && ok "پایگاه «$DB_NAME» و کاربر «$DB_USER» آماده‌اند" || warn "اتصال به «$DB_NAME» برقرار نشد"
fi

# ── ۳) محیط پایتون ───────────────────────────────────────────────────────────
say "محیط پایتون (وابستگی‌های قفل‌شده)"
if [ -x .venv/bin/python ]; then
  skip ".venv موجود است"
fi
uv sync --frozen --all-extras >/dev/null
ok "وابستگی‌ها هم‌راستا با uv.lock"

if [ ! -f src/.env ]; then
  cp .env.example src/.env
  ok "src/.env از نمونه ساخته شد (برای محیط توسعه کافی است)"
else
  skip "src/.env موجود است"
fi

# ── ۳.۵) مخزن git ────────────────────────────────────────────────────────────
# در محیط‌های ظرفی، فایل `.git/config` (که ورودی اعتبارنامه‌ها را نگه می‌دارد)
# بازنشانی می‌شود و ریموت گم می‌شود؛ اینجا فقط نشانی عمومی بازگردانده می‌شود.
say "مخزن git"
if git -C "$ROOT" remote get-url origin >/dev/null 2>&1; then
  skip "ریموت origin موجود است ($(git -C "$ROOT" remote get-url origin))"
else
  git -C "$ROOT" remote add origin https://github.com/mohebbibrothers/Mahdieh.git
  ok "ریموت origin بازگردانده شد (توکن را جدا از فایل نگه دارید)"
fi

# ── ۴) پایگاه داده: مهاجرت + دادهٔ پایه ─────────────────────────────────────
say "مهاجرت و دادهٔ پایه"
export DJANGO_SETTINGS_MODULE=config.settings.development
uv run python src/manage.py migrate --no-input >/dev/null
uv run python src/manage.py createcachetable >/dev/null
ok "مهاجرت‌ها و جدول کش اعمال شدند"
uv run python src/manage.py seed_inventory >/dev/null
ok "۳ طبقه و ۵ واحد و امکانات آماده است"
if [ "$WITH_DEMO" = "1" ]; then
  uv run python src/manage.py seed_demo >/dev/null
  ok "دادهٔ نمایشی (زائران نمونه و رزروها) بارگذاری شد"
fi
uv run python src/manage.py check >/dev/null
ok "بررسی سیستم بی‌ایراد"

# ── ۵) خلاصه ─────────────────────────────────────────────────────────────────
echo
printf '\033[1;32m✅ محیط توسعه آماده است.\033[0m\n'
if [ "$SERVE" = "1" ]; then
  cat <<'HINT'

گام بعدی — اجرای سرور:
  uv run python src/manage.py runserver 0.0.0.0:8000

سپس این نشانی‌ها را ببینید:
  http://127.0.0.1:8000/            صفحهٔ اصلی (زائر)
  http://127.0.0.1:8000/calendar/   تقویم نیم‌روز همهٔ واحدها
  http://127.0.0.1:8000/demo/guest/ ورود نمایشی زائر (فقط در توسعه)
  http://127.0.0.1:8000/demo/staff/ ورود نمایشی کارمند پذیرش (فقط در توسعه)

بازرسی چشمی خودکار (نیازمند playwright):
  uv pip install playwright && .venv/bin/python -m playwright install chromium
  uv run python scripts/visual_check.py --base-url http://127.0.0.1:8000
HINT
fi
