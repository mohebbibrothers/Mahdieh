#!/usr/bin/env bash
# راه‌اندازی محیط توسعهٔ مهدیه از صفر — یک فرمان، محیط قابل اجرا.
#
# چرا وجود دارد؟ در محیط‌های ظرفی/ابری، ابزارها و سرویس‌ها با هر نشست تازه
# از بین می‌روند (uv، .venv، Postgres، Valkey). این اسکریپت همان کاری را
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

PG_VERSION="${MAHDIEH_PG_VERSION:-17}"
DB_NAME="${MAHDIEH_DB_NAME:-mahdieh}"
DB_USER="${MAHDIEH_DB_USER:-mahdieh}"
DB_PASS="${MAHDIEH_DB_PASS:-mahdieh}"     # فقط محیط توسعهٔ محلی
DB_HOST="${MAHDIEH_DB_HOST:-127.0.0.1}"
DB_PORT="${MAHDIEH_DB_PORT:-5432}"
REDIS_PORT="${MAHDIEH_REDIS_PORT:-6379}"
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

# ── ۲) سرویس‌ها: PostgreSQL و Redis ──────────────────────────────────────────
say "سرویس‌های محلی (PostgreSQL $PG_VERSION + Redis)"
MISSING_PKGS=()
[ -d "/usr/lib/postgresql/$PG_VERSION" ] || MISSING_PKGS+=(postgresql postgresql-contrib)
command -v redis-server >/dev/null || MISSING_PKGS+=(redis-server)
if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
  as_root apt-get update -qq
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING_PKGS[@]}"
  ok "بسته‌ها نصب شدند: ${MISSING_PKGS[*]}"
else
  skip "بسته‌های PostgreSQL و Redis از قبل نصب‌اند"
fi

if PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
      -tAc 'select 1' >/dev/null 2>&1; then
  skip "PostgreSQL در حال اجرا است و پایگاه «$DB_NAME» پاسخ می‌دهد"
else
  if ! pg_isready -h "$DB_HOST" -p "$DB_PORT" >/dev/null 2>&1; then
    as_root pg_ctlcluster "$PG_VERSION" main start >/dev/null 2>&1 || true
    for _ in $(seq 1 20); do
      pg_isready -h "$DB_HOST" -p "$DB_PORT" >/dev/null 2>&1 && break
      sleep 1
    done
  fi
  pg_isready -h "$DB_HOST" -p "$DB_PORT" >/dev/null 2>&1 \
    && ok "خوشهٔ PostgreSQL بالا آمد" || warn "PostgreSQL بالا نیامد — ادامه می‌دهیم"

  if [ "$(as_root -u postgres psql -tAc "select 1 from pg_roles where rolname='$DB_USER'" 2>/dev/null)" != "1" ]; then
    as_root -u postgres psql -qc "create role $DB_USER login superuser password '$DB_PASS';"
    ok "نقش «$DB_USER» ساخته شد"
  fi
  if [ "$(as_root -u postgres psql -tAc "select 1 from pg_database where datname='$DB_NAME'" 2>/dev/null)" != "1" ]; then
    as_root -u postgres createdb -O "$DB_USER" "$DB_NAME"
    ok "پایگاه دادهٔ «$DB_NAME» ساخته شد"
  fi
fi

# قید Exclusion روی بازهٔ زمانی به btree_gist نیاز دارد (مهاجرت‌ها هم می‌سازند).
PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  -qc 'create extension if not exists btree_gist;' >/dev/null 2>&1 \
  && ok "پسوند btree_gist فعال است" || skip "btree_gist از قبل فعال بود"

if redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
  skip "Redis روی درگاه $REDIS_PORT پاسخ می‌دهد"
else
  as_root redis-server --daemonize yes --bind 127.0.0.1 --port "$REDIS_PORT" --save '' >/dev/null
  for _ in $(seq 1 10); do
    redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1 && break
    sleep 1
  done
  redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1 \
    && ok "Redis بالا آمد" || warn "Redis بالا نیامد — کش در حالت کاهشی کار می‌کند"
fi

# ── ۳) محیط پایتون ───────────────────────────────────────────────────────────
say "محیط پایتون (وابستگی‌های قفل‌شده)"
if [ -x .venv/bin/python ]; then
  skip ".venv موجود است"
fi
uv sync --frozen >/dev/null
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
ok "مهاجرت‌ها اعمال شدند"
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
