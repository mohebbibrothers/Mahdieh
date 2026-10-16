# راهنمای استقرار و بهره‌برداری — زائرسرای مهدیه

> مخاطب: اپراتور/مهندس عملیات. هر بخش قابل اجرا و آزمون‌شده است؛ اگر چیزی
> با واقعیت نخواند، **همین سند را اصلاح کنید** — سندِ کهنه از نبودِ سند بدتر است.

---

## ۱) نگاشت معماری اجرا

```
کاربر ──HTTPS──▶ Nginx ──keepalive──▶ gunicorn (۵ کارگر WSGI) ──▶ sync (ADR-0015)
                    │                        │
                    │                        ├─▶ PostgreSQL 17 (استخر اتصال، max_size=8)
                    └─ /static از دیسک        └─▶ Valkey/Redis (کش، نشست، محدودسازی نرخ)
                                              └─▶ صندوق رخداد (Outbox) ⇒ روان‌ساز outbox_relay
```

فایل‌های مرتبط: `deploy/nginx/mahdieh.conf`، `deploy/systemd/*`،
`deploy/env/mahdieh.env.example`، `Dockerfile`، `docker-compose.yml`.

### ظرفیت: چند کارگر، چند هسته؟

دو عدد همه‌چیز را تعیین می‌کند و هر دو اندازه‌گیری‌شده‌اند
(`docs/load/2026-10-16-load-64u-capacity.md`):

| سنجه | مقدار اندازه‌گیری‌شده | معنا |
|---|---|---|
| هزینهٔ CPU هر درخواست | ≈۳۵ میلی‌ثانیه‌هسته | هر درخواست این‌قدر پردازنده می‌خواهد |
| تعداد کارگر | `۲×هسته+۱` | پیش‌فرض تصویر: ۵ |

**سقف توان عبوری = هسته ÷ هزینهٔ درخواست.** پس:

| هستهٔ سرور | سقف توان عبوری | کاربر هم‌زمان با p95 ≤ ۸۰۰ms |
|---|---|---|
| ۲ | ≈۵۷ درخواست/ثانیه | ≈۴۶ |
| ۴ | ≈۱۱۴ درخواست/ثانیه | ≈۹۱ |
| ۸ | ≈۲۲۹ درخواست/ثانیه | ≈۱۸۳ |

سقف را با بودجهٔ اتصال Postgres هم ببندید:
`کارگر × DATABASE_POOL_MAX_SIZE + ۱۰ ≤ ۱۰۰`.

> نکتهٔ صادقانه: این اعداد روی پردازندهٔ اشتراکیِ ۲ هسته‌ایِ محیط آزمون گرفته
> شده‌اند و **محافظه‌کارانه**اند؛ روی سخت‌افزار اختصاصی تولید، همان هسته‌ها
> بیشتر جواب می‌دهند. اگر عدد تولید را می‌خواهید، آزمون را روی همان ماشین
> اجرا کنید: `make load-stress`.

---

## ۲) نصب گام‌به‌گام (اولین استقرار)

```bash
# ۱. کاربر و مسیرها
sudo useradd --system --create-home --home-dir /srv/mahdieh mahdieh
sudo mkdir -p /srv/mahdieh /etc/mahdieh /srv/mahdieh/var/backups
sudo chown -R mahdieh:mahdieh /srv/mahdieh

# ۲. کد
sudo -u mahdieh git clone https://github.com/mohebbibrothers/Mahdieh.git /srv/mahdieh
cd /srv/mahdieh && sudo -u mahdieh uv sync --frozen --no-dev

# ۳. محیط (رازها) — از الگو بسازید و پرش کنید
sudo cp deploy/env/mahdieh.env.example /etc/mahdieh/mahdieh.env
sudo chmod 640 /etc/mahdieh/mahdieh.env && sudo chown root:mahdieh /etc/mahdieh/mahdieh.env
sudo -u mahdieh python -c "import secrets;print('MAHDIEH_SECRET_KEY='+secrets.token_urlsafe(64))"

# ۴. پایگاه‌داده
sudo -u postgres createuser mahdieh --pwprompt
sudo -u postgres createdb -O mahdieh mahdieh

# ۵. مهاجرت، دارایی ایستا، بررسی سلامت (ترتیب مهم است)
cd /srv/mahdieh/src
sudo -u mahdieh env $(grep -v '^#' /etc/mahdieh/mahdieh.env | xargs) \
     /srv/mahdieh/.venv/bin/python manage.py migrate --noinput
sudo -u mahdieh env $(grep -v '^#' /etc/mahdieh/mahdieh.env | xargs) \
     /srv/mahdieh/.venv/bin/python manage.py collectstatic --noinput
sudo -u mahdieh env $(grep -v '^#' /etc/mahdieh/mahdieh.env | xargs) \
     /srv/mahdieh/.venv/bin/python manage.py check --deploy

# ۶. سرویس‌ها
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mahdieh mahdieh-outbox mahdieh-feedback.timer \
     mahdieh-holds.timer mahdieh-reminders.timer mahdieh-backup.timer \
     mahdieh-drill.timer

# ۷. Nginx و TLS
sudo cp deploy/nginx/mahdieh.conf /etc/nginx/sites-available/mahdieh
sudo ln -sf /etc/nginx/sites-available/mahdieh /etc/nginx/sites-enabled/mahdieh
sudo certbot --nginx -d mahdie-shohada.ir -d www.mahdie-shohada.ir
sudo nginx -t && sudo systemctl reload nginx

# ۸. اثبات (نه ادعا): آزمون دودِ استقرار روی همان دستور تصویر تولیدی
sudo -u mahdieh bash /srv/mahdieh/scripts/deploy_smoke.sh
```

---

## ۳) انتشار نسخهٔ جدید (zero-downtime)

```bash
cd /srv/mahdieh
sudo -u mahdieh scripts/db_backup.sh --label pre-release      # ۱) نقطهٔ بازگشت
sudo -u mahdieh git pull --ff-only                            # ۲) کد
sudo -u mahdieh uv sync --frozen --no-dev                     # ۳) وابستگی‌ها
cd src
sudo -u mahdieh python manage.py migrate --noinput             # ۴) مهاجرت (سازگار به عقب)
sudo -u mahdieh python manage.py collectstatic --noinput       # ۵) دارایی نسخه‌دار
sudo systemctl restart mahdieh mahdieh-outbox                  # ۶) راه‌اندازی مجدد کارگرها
sudo -u mahdieh bash ../scripts/deploy_smoke.sh                # ۷) اثبات پس از انتشار
```

**قواعد مهاجرت:** هر مهاجرت باید با نسخهٔ قبلیِ کد هم‌زیستی کند (افزودن ستون
nullable یا با پیش‌فرض، حذف ستون در انتشار بعدی). یک بار «افزودن+پرکردن+حذف»
در سه انتشار انجام دهید، نه در یکی.

**بازگشت (rollback):**

```bash
git checkout <commit قبلی> && cd src && python manage.py migrate <app> <migration قبلی>
sudo systemctl restart mahdieh
```

---

## ۴) فرمول ظرفیت (مهم — خواندنش اجباری است)

درس فاز ۸: با تنظیم نادرست، **۱۶ کاربر هم‌زمان** برای رساندن Postgres به
«`sorry, too many clients already`» کافی بود. سقف‌ها را هم‌خوان نگه دارید:

```
اتصال‌های مصرفی = WEB_CONCURRENCY × DATABASE_POOL_MAX_SIZE
بودجهٔ Postgres = max_connections − superuser_reserved_connections − ۱۰ (مهاجرت/پشتیبان/اپراتور)

۳ × ۸ = ۲۴  ≤  ۱۰۰ − ۳ − ۱۰ = ۸۷        ✅
```

تست `src/tests/test_deployment_contract.py::test_capacity_formula_matches_postgres_budget`
همین نامساوی را در CI قفل می‌کند. اگر کارگرها را زیاد کردید، `max_connections`
را هم بالا ببرید:

```sql
ALTER SYSTEM SET max_connections = 200;   -- نیازمند راه‌اندازی مجدد Postgres
ALTER SYSTEM SET shared_buffers = '2GB';
```

`LOAD_SHEDDING_MAX_CONCURRENCY` (پیش‌فرض ۱۲۰ در هر کارگر) سدِ آخر است: در فشار
غیرعادی، درخواست‌های مازاد بی‌درنگ ۵۰۳ با `Retry-After` می‌گیرند و سامانه
**نمی‌افتد**. مسیرهای `/health/` و `/static/` از این سد معاف‌اند.

---

## ۵) بارسنجی پس از هر تغییر بزرگ

```bash
scripts/load_test.py --base-url https://mahdie-shohada.ir --users 50 --seconds 60 \
                     --report docs/load/$(date +%F)-prod.md
```

معیار پذیرش: خطای ۵xx صفر، نرخ خطا ≤ ۱٪، p95 ≤ ۸۰۰ms (روی ماشین توسعه).
آخرین نتایج مرجع در `docs/load/` نگه‌داری می‌شود (قابل مقایسه در طول زمان).

---

### مرز ظرفیت اندازه‌گیری‌شده (۱۴۰۵/۰۷/۲۴)

| ماشین | کاربران هم‌زمان در بودجهٔ p95 ≤ ۸۰۰ms | توان عبوری |
|---|---|---|
| ۲ هسته (محیط سنجش) | ~۴۰ | ۸۸–۹۲ RPS با صفر خطای ۵xx |
| ۴ هسته (پیشنهاد) | ~۸۰ | ~۱۶۰–۱۸۰ RPS (سنجش مجدد لازم) |

جزئیات و بازتولید در `docs/load/2026-10-16-load.md`. از مرز که بگذرید، سامانه
**نمی‌افتد**؛ پاسخ‌ها کندتر می‌شوند و سدِ ظرفیت ASGI به‌جای صف بی‌کران، ۵۰۳
محترمانه با `Retry-After` می‌دهد.

**هشدار عملیاتی:** `scripts/deploy_smoke.sh` اگر ببیند پورت هدف از قبل اشغال است،
با کد ۲ رد می‌کند — چون در آن حالت نتیجهٔ سرور *قدیمی* سنجیده می‌شود و گزارش
«سبزِ الکی» می‌دهد. سرور قبلی را ببندید یا `PORT=8020` بدهید.

## ۶) پایش و هشدار (حداقل‌های الزامی)

| نشانه | آستانه | اقدام |
| --- | --- | --- |
| `/health/ready/` | ≠ ۲۰۰ برای ۲ دقیقه | بخش ۳ از `incident-response.md` |
| نرخ ۵xx | > ۱٪ در ۵ دقیقه | لاگ `django.request` + `infrastructure_degraded` |
| `shed_total` (رویداد `load_shed`) | > ۰ در ساعت شلوغ | ظرفیت را بالا ببرید یا کندی را ریشه‌یابی کنید |
| زمان مهاجرت | > ۳۰ ثانیه | مهاجرت را به پنجرهٔ کم‌ترافیک منتقل کنید |
| تأخیر p95 | > ۲ ثانیه | بارسنجی و پروفایل کوئری (`django.db.connection.queries`) |
| شکست پشتیبان شبانه | هر بار | `journalctl -u mahdieh-backup -n 50` |
| تمرین DR هفتگی | ناموفق | `docs/drills/` را ببینید و پیش از حادثهٔ واقعی رفع کنید |

لاگ‌ها ساخت‌یافته (JSON) و دارای `request_id` هستند؛ برای پیگیری یک درخواست
کاربر: `journalctl -u mahdieh -o cat | grep <request_id>`.

---

## ۷) کارهای زمان‌بندی‌شده

| سرویس | کار | زمان |
| --- | --- | --- |
| `mahdieh-outbox.service` | روان‌ساز صندوق رخداد (`--loop --interval 5`) | پیوسته |
| `mahdieh-feedback.timer` | پیامک نظرسنجی اقامت‌های پایان‌یافته | هر ساعت ۰۸–۲۲ |
| `mahdieh-holds.timer` | آزادسازی نگه‌داشت‌های منقضی + پیشنهاد سانس به نوبت‌انتظار | هر ۱۰ دقیقه |
| `mahdieh-reminders.timer` | یادآور پیامکی پیش از پذیرش (کاهش عدم‌حضور) | هر ساعت ۰۷–۲۲، دقیقهٔ ۵ |
| `mahdieh-backup.timer` | پشتیبان تأییدشده | شبانه ۰۲:۳۰ |
| `mahdieh-drill.timer` | تمرین بازیابی در قرنطینه | پنجشنبه‌ها ۰۴:۰۰ |

بررسی: `systemctl list-timers 'mahdieh*'` و `journalctl -u mahdieh-feedback -n 20`.
