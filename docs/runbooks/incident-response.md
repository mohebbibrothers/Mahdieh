# راهنمای پاسخ به حادثه — مهدیه

> اصل راهنما: **اول آرام‌سازی کاربر، بعد ریشه‌یابی، آخر گزارش.**
> زائر باید حتی در بدترین حالت، پیام روشن فارسی ببیند — نه صفحهٔ سفید.

---

## ۰) سه خطِ اولِ همیشه

```bash
systemctl status mahdieh mahdieh-outbox --no-pager        # سرویس‌ها زنده‌اند؟
curl -fsS https://mahdieh.ir/health/ready/ | head -c 400  # وابستگی‌ها سالم‌اند؟
journalctl -u mahdieh -o cat -n 100                        # آخرین رخدادها (JSON)
```

`/health/ready/` چه می‌گوید:

| خروجی | معنا | بخش مربوطه |
| --- | --- | --- |
| `200` با همهٔ اجزا `healthy` | سامانه سالم است | — |
| `503` و `database: unhealthy` | پایگاه‌داده در دسترس نیست | بخش ۲ |
| `503` و `migrations: pending` | مهاجرت اجرانشده (استقرار نیمه‌کاره) | بخش ۴ |
| `503` و `cache: degraded` | کش نخوابیده اما ناسالم | بخش ۳ |
| `200` ولی کاربران شکایت دارند | کندی/عملکرد | بخش ۵ |

---

## ۱) تشخیص سریع با `request_id`

هر پاسخ خطا (صفحهٔ ۵۰۰/۵۰۳ و بدنهٔ API) شناسهٔ ردیابی دارد. کاربر همان را
می‌خواند؛ شما:

```bash
journalctl -u mahdieh -o cat | grep '01a1449aec0e7aa783559a651d47a5c6'
journalctl -u mahdieh -o cat | grep infrastructure_degraded | tail -20
journalctl -u mahdieh -o cat | grep load_shed | tail -20
```

---

## ۲) پایگاه‌داده در دسترس نیست (`sorry, too many clients` / `connection refused`)

**علائم:** صفحهٔ «همین حالا درخواست‌ها زیاد است» (۵۰۳)، لاگ `infrastructure_degraded`،
یا در بدترین حالت پیش از اصلاح: `django.request` با `OperationalError`.

```bash
# ۱. چند اتصال باز است و سقف چقدر است؟
sudo -u postgres psql -c "SELECT count(*) FROM pg_stat_activity;"
sudo -u postgres psql -c "SHOW max_connections;"
sudo -u postgres psql -c "SELECT state, count(*) FROM pg_stat_activity GROUP BY 1 ORDER BY 2 DESC;"

# ۲. اگر «too many clients» است: مصرف‌کنندهٔ اصلی را پیدا کن
sudo -u postgres psql -c "SELECT application_name, state, count(*)
                          FROM pg_stat_activity GROUP BY 1,2 ORDER BY 3 DESC LIMIT 10;"

# ۳. اتصال‌های بی‌کار و قدیمی را ببند (احتیاط: idleness_in_transaction را ببندید)
sudo -u postgres psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                          WHERE state='idle' AND state_change < now() - interval '30 minutes';"
```

**ریشه‌های رایج و راه‌حل:**

| ریشه | نشانه | راه‌حل |
| --- | --- | --- |
| کارگرها × استخر > بودجهٔ اتصال | `too many clients` | فرمول بخش ۴ `deploy.md`؛ `DATABASE_POOL_MAX_SIZE` یا `max_connections` را تنظیم کنید |
| کوئری سرگردان | `state='active'` طولانی | `DATABASE_STATEMENT_TIMEOUT_MS` (پیش‌فرض ۵۰۰۰) فعال است؛ کندی را با `pg_stat_statements` ریشه‌یابی کنید |
| مهاجرت طولانی | قفل روی جدول | پنجرهٔ کم‌ترافیک؛ مهاجرت‌های قفل‌گیر را با `lock_timeout` اجرا کنید |
| دیسک پر | Postgres در حالت read-only | `df -h`؛ فضای آزاد کنید، سپس `systemctl restart postgresql` |
| Valkey نخوابیده ولی ناسالم | `cache: degraded` | `redis-cli ping`؛ سایت باید سالم بماند (کش بهینه‌سازی است، نه منبع حقیقت) |

پس از رفع: `curl -fsS https://mahdieh.ir/health/ready/` و سپس
`scripts/deploy_smoke.sh` و در پایان یک بار بارسنجی سبک.

---

## ۳) قطعی کش (Valkey/Redis)

سامانه **باید سالم بماند** (بازخوانی از پایگاه‌داده) با دو استثنا:

* ورود با پیامک **بسته شکست می‌خورد (۵۰۳)**: بدون شمارندهٔ کش نمی‌توان سقف
  پیامک را تضمین کرد؛ باز گذاشتن درِ ارسال یعنی هزینهٔ مالی و سوءاستفاده.
* محدودسازی نرخ لبهٔ Nginx سر جایش می‌ماند.

```bash
systemctl status redis-server || systemctl status valkey
redis-cli -n 0 ping
sudo systemctl restart valkey        # یا redis-server
```

---

## ۴) استقرار نیمه‌کاره (مهاجرت اجرانشده)

`/health/ready/` مقدار `migrations: pending` می‌دهد؛ یعنی کد جدید بالا آمده
ولی پایگاه‌داده عقب است. **این خطرناک‌ترین حالت است** (خطای ستون/جدول در نماها).

```bash
cd /srv/mahdieh/src
sudo systemctl stop mahdieh mahdieh-outbox             # توقف نوشتن
sudo -u mahdieh scripts/db_backup.sh --label pre-migrate   # نقطهٔ بازگشت
sudo -u mahdieh /srv/mahdieh/.venv/bin/python manage.py migrate --noinput
sudo systemctl start mahdieh mahdieh-outbox
bash /srv/mahdieh/scripts/deploy_smoke.sh
```

اگر مهاجرت شکست خورد: پشتیبان `pre-migrate` را بازیابی کنید و کد را به نسخهٔ
سازگار برگردانید (جزئیات در `backup-restore.md`).

---

## ۵) سامانه کند است (بدون خطا)

```bash
# لاگ‌های کند: رخداد http_request زمان اجرا را دارد
journalctl -u mahdieh -o cat | grep http_request | tail -200 | jq -r '.path' | sort | uniq -c | sort -rn | head

# بارسنجی هدفمند برای اندازه‌گیری دقیق
scripts/load_test.py --base-url https://mahdieh.ir --users 20 --seconds 30 \
                     --report /tmp/now.md
```

سپس در پوستهٔ جنگو، کوئری‌های کند را پیدا کنید:

```bash
sudo -u postgres psql -c "SELECT query, mean_exec_time, calls FROM pg_stat_statements
                          ORDER BY mean_exec_time DESC LIMIT 10;"
```

---

## ۶) سوءاستفاده یا حمله

| نشانه | اقدام |
| --- | --- |
| سیل درخواست به `/login/` (حملهٔ پیامکی) | `limit_req` لبه خودکار مهار می‌کند؛ در صورت نیاز `rate` را در `deploy/nginx/mahdieh.conf` کاهش دهید و `systemctl reload nginx` |
| درخواست‌های زیاد با ۵۰۳ (سد ظرفیت) | `load_shed` در لاگ ⇒ حمله یا ظرفیت؛ اگر حمله است آی‌پی را در Nginx ببندید |
| هشدار «هش با بیانیه نمی‌خواند» یا زنجیرهٔ حسابرسی نامعتبر | **رخداد امنیتی**: سرویس را متوقف کنید، `manage.py audit_verify --json` بگیرید، لنگرها را با نسخهٔ بیرونی مقایسه کنید و به کارفرما اطلاع دهید |
| تلاش برای دسترسی به `/django-admin/` | مسیر غیرمعمول است و ورود لازم دارد؛ لاگ‌ها را بررسی کنید (چند ۳۰۲/۴۰۳ پشت سر هم = اسکن خودکار) |

---

## ۷) بازگشت آرام به حالت عادی (postmortem)

1. تأیید سلامتی: `/health/ready/` ۲۰۰ + `scripts/deploy_smoke.sh` موفق
2. یک بار بارسنجی سبک (`--users 16 --seconds 20`) بدون خطا
3. پشتیبان فوری پس از حادثه: `scripts/db_backup.sh --label post-incident`
4. نوشتن گزارش کوتاه در `docs/incidents/YYYY-MM-DD-<موضوع>.md` با این قالب:
   * چه شد (خط زمانی)
   * اثر بر کاربر (چند نفر، چند دقیقه)
   * ریشهٔ فنی
   * چه چیزی مانع کشف سریع‌تر شد (نقش پایش)
   * کنش‌های پیشگیرانه با **مالک** و **تاریخ**
   * آیا تستی هست که جلوی تکرار را بگیرد؟ اگر نه، همان را اضافه کنید
