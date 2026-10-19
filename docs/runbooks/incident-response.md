# راهنمای پاسخ به حادثه — مهدیه

> اصل راهنما: **اول آرام‌سازی کاربر، بعد ریشه‌یابی، آخر گزارش.**
> زائر باید حتی در بدترین حالت، پیام روشن فارسی ببیند — نه صفحهٔ سفید.

---

## ۰) سه خطِ اولِ همیشه

```bash
systemctl status mahdieh mahdieh-outbox --no-pager        # سرویس‌ها زنده‌اند؟
curl -fsS https://mahdie-shohada.ir/health/ready/ | head -c 400  # وابستگی‌ها سالم‌اند؟
journalctl -u mahdieh -o cat -n 100                        # آخرین رخدادها (JSON)
```

پایشگر دوره‌ای هر ۵ دقیقه همین داوری را می‌گیرد و **کد خروجی** می‌دهد:
`۰` سالم · `۱` افت‌کرده (کش/ذخیره‌سازی) · `۲` ناسالم (پایگاه‌داده/مهاجرت).
برای دیدن وضعیت کنونی بدون تماس دستی: `cat /srv/mahdieh/var/healthcheck.state`
و برای فرمان دستی: `python manage.py healthcheck --base-url http://127.0.0.1:8000 --behind-proxy`
(جزئیات: `docs/runbooks/monitoring.md`).

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

## ۲) پایگاه‌داده در دسترس نیست (`Too many connections` / `Can't connect`)

**علائم:** صفحهٔ «همین حالا درخواست‌ها زیاد است» (۵۰۳)، لاگ `infrastructure_degraded`،
یا `OperationalError (1040|2002|2013)`.

```bash
# اعتبار اپراتور: میزبان cPanel ⇒ ~/.mahdieh/my.cnf ؛ سرور اختصاصی ⇒ sudo mariadb
DB="mariadb --defaults-file=$HOME/.mahdieh/my.cnf"

# ۱. سهم ما از اتصال‌ها و سقف سرور
$DB -e "SHOW GLOBAL VARIABLES LIKE 'max_connections'; SHOW STATUS LIKE 'Threads_connected';"
$DB -e "SELECT command, state, COUNT(*) n FROM information_schema.processlist
        WHERE user = CURRENT_USER() OR user = SUBSTRING_INDEX(CURRENT_USER(),'@',1)
        GROUP BY 1,2 ORDER BY n DESC;"

# ۲. پرس‌وجوهای طولانی (بیش از ۳۰ ثانیه)
$DB -e "SELECT id, time, state, LEFT(info, 120) q FROM information_schema.processlist
        WHERE command <> 'Sleep' AND time > 30 ORDER BY time DESC;"

# ۳. پایان‌دادن به یک پرس‌وجوی گیرکرده (فقط پرس‌وجو، نه اتصال)
$DB -e "KILL QUERY <id>;"
```

**ریشه‌های رایج و راه‌حل:**

| ریشه | نشانه | راه‌حل |
| --- | --- | --- |
| فرآیندها × ۲ اتصال > بودجه | `Too many connections` | فرمول بخش ۶ `hosting.md`؛ سرور اشتراکی است، سهم ما باید ≤ یک‌سوم سقف بماند |
| پرس‌وجوی سرگردان | `time` بزرگ در processlist | `DATABASE_STATEMENT_TIMEOUT_SECONDS` (پیش‌فرض ۵) فعال است؛ پرس‌وجو را با `EXPLAIN` ریشه‌یابی کنید |
| مهاجرت طولانی | `Waiting for table metadata lock` | پنجرهٔ کم‌ترافیک؛ cron را موقتاً با `crontab -l > /tmp/c && crontab -r` متوقف و با `crontab /tmp/c` بازگردانید |
| سهمیهٔ دیسک پر (LVE) | خطای نوشتن / `1021` | `~/mahdieh/bin/mahdieh-deploy status`؛ پشتیبان‌های قدیمی و `shared/var/log/*.1` را پاک کنید |
| کش ناسالم | `cache: degraded` در `/health/ready/` | جدول `django_cache` را بررسی کنید (`manage.py createcachetable`)؛ سایت سالم می‌ماند |

پس از رفع: `curl -fsS https://mahdei.n-cpanel.xyz/health/ready/` و `mahdieh-deploy status`.

---

## ۳) قطعی کش (جدول `django_cache`)

کش روی همان پایگاه‌داده است (ADR-0021)؛ پس «قطعی کش بدون قطعی پایگاه‌داده» تقریباً
فقط وقتی رخ می‌دهد که جدول کش نباشد یا خراب شود. سامانه **باید سالم بماند** با یک استثنا:

* ورود با پیامک **بسته شکست می‌خورد (۵۰۳)**: بدون شمارندهٔ کش نمی‌توان سقف
  پیامک را تضمین کرد؛ باز گذاشتن درِ ارسال یعنی هزینهٔ مالی و سوءاستفاده.

```bash
~/mahdieh/current/.venv/bin/python ~/mahdieh/current/src/manage.py createcachetable
$DB -e "CHECK TABLE django_cache;"
# در صورت خرابی: جدول را خالی کنید (فقط کش است، دادهٔ اصلی نیست)
$DB -e "TRUNCATE TABLE django_cache;"
```

---

## ۴) استقرار نیمه‌کاره (مهاجرت اجرانشده)

`/health/ready/` مقدار `migrations: pending` می‌دهد؛ یعنی کد جدید بالا آمده
ولی پایگاه‌داده عقب است. با `deploy.sh` این حالت رخ نمی‌دهد (مهاجرت پیش از جابه‌جایی
و آزمون درون‌فرآیندی پس از آن)، مگر کسی دستی کد را عوض کرده باشد.

```bash
# میزبان cPanel: سریع‌ترین راه، بازگشت به انتشار سالم قبلی
~/mahdieh/bin/mahdieh-deploy rollback
# یا تکمیل همان انتشار (پشتیبان پیش از مهاجرت خودکار است)
~/mahdieh/bin/mahdieh-deploy activate ~/mahdieh/releases/<id>
```

اگر مهاجرت شکست خورد: پشتیبان `pre-…` را بازیابی کنید و به انتشار سازگار برگردید
(جزئیات در `backup-restore.md`).

---

## ۵) سامانه کند است (بدون خطا)

```bash
# لاگ‌های کند: رخداد http_request زمان اجرا را دارد
journalctl -u mahdieh -o cat | grep http_request | tail -200 | jq -r '.path' | sort | uniq -c | sort -rn | head

# بارسنجی هدفمند برای اندازه‌گیری دقیق
scripts/load_test.py --base-url https://mahdie-shohada.ir --users 20 --seconds 30 \
                     --report /tmp/now.md
```

سپس در پوستهٔ جنگو، کوئری‌های کند را پیدا کنید:

```bash
# پرس‌وجوهای پرهزینه از دید MariaDB (نیازمند performance_schema؛ روی میزبان اشتراکی
# ممکن است در دسترس نباشد — آنگاه EXPLAIN روی پرس‌وجوی مشکوک)
$DB -e "SELECT LEFT(digest_text, 120) q, count_star n, ROUND(avg_timer_wait/1e9, 1) ms
        FROM performance_schema.events_statements_summary_by_digest
        ORDER BY avg_timer_wait DESC LIMIT 10;"
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
