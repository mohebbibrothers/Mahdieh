# رانبوک جامع «روز انتشار» — از سرور خالی تا سایتِ زندهٔ مردمی

> این سند، **تنها مرجع** ترتیب کارهاست. هر جای دیگری که چیزی دربارهٔ انتشار
> می‌گوید، تابع این ترتیب است. اگر روزی این دو ناسازگار شدند، این سند درست است
> و آن یکی باید اصلاح شود.
>
> مخاطب: مهندس عملیات، در روز انتشار. زمان تقریبی اجرای کامل: ۹۰ دقیقه.
> **هیچ مرحله‌ای را «چون قبلاً کار می‌کرد» جا نیندازید.**

---

## نقشهٔ کلی

```text
T-7   آماده‌سازی: DNS، گواهی، سرور، بازرسی پیش از انتشار
T-1   تمرین خشک: استقرار روی همان سرور + بازگردانی از پشتیبان
T-0   انتشار: توقف پذیرش، استقرار، سنجش، بازکردن
T+0   پس از انتشار: کدهای پیامک، شمارهٔ پذیرش، عدد بار واقعی
T+7   بازبینی: دروس، اصلاح رانبوک، بایگانی برگهٔ آمادهٔ
```

---

## T-7 — آماده‌سازی (یک هفته پیش)

### ۱. سرور و شبکه

```bash
# حداقل مشخصات: ۴ هسته، ۸ گیگ رم، ۴۰ گیگ SSD، اوبونتو ۲۴٫۰۴ LTS
sudo apt update && sudo apt install -y nginx postgresql-18 valkey certbot python3-certbot-nginx
sudo systemctl enable --now nginx postgresql valkey-server
```

| کار | دستور / معیار |
| --- | --- |
| DNS نوع A | `mahdie-shohada.ir` و `www` ⇒ IP سرور (پیش از گواهی) |
| گواهی TLS | `sudo certbot --nginx -d mahdie-shohada.ir -d www.mahdie-shohada.ir` |
| تمدید خودکار | `sudo systemctl enable --now certbot.timer` و `certbot renew --dry-run` |
| پورت ۸۰۰۰ | **فقط** روی `127.0.0.1`؛ از بیرون بستهٔ (ADR-0016) |
| کاربر سرویس | `sudo useradd --system --home /srv/mahdieh --shell /usr/sbin/nologin mahdieh` |

### ۲. کد و محیط

```bash
sudo -u mahdieh git clone <repo> /srv/mahdieh && cd /srv/mahdieh
sudo install -d -o mahdieh -g mahdieh /srv/mahdieh/var /etc/mahdieh
sudo install -m 640 -o root -g mahdieh deploy/env/mahdieh.env.example /etc/mahdieh/mahdieh.env
sudo -u mahdieh python3 -m venv /srv/mahdieh/.venv
sudo -u mahdieh /srv/mahdieh/.venv/bin/pip install -r <(uv export --frozen --no-dev --no-hashes)
```

پرکردن `/etc/mahdieh/mahdieh.env` (فهرست کامل و توضیح هر متغیر در همان فایل):

| متغیر | مقدار |
| --- | --- |
| `MAHDIEH_SECRET_KEY` | `python -c "import secrets;print(secrets.token_urlsafe(64))"` |
| `FIELD_ENCRYPTION_KEY` | ۳۲ بایت base64 (تغییرش دادهٔ قبلی را ناخوانا می‌کند) |
| `FIELD_HASH_KEY` | رشتهٔ تصادفی بلند (HMAC جست‌وجوی کور کد ملی) |
| `DATABASE_URL` / `DATABASE_PASSWORD` | یکی؛ اگر هر دو، **باید یکی باشند** (preflight می‌سنجد) |
| `MAHDIEH_ALLOWED_HOSTS` | `mahdie-shohada.ir,www.mahdie-shohada.ir` |
| `MAHDIEH_SUPPORT_ADDRESS` | `مشهد، نوغان، کوچه ۱۱ شهید قائمی، درب سوم` |
| `MAHDIEH_SUPPORT_PHONE` | **خالی** تا اعلام شمارهٔ پذیرش (پس از انتشار) |
| `SMS_ENABLED` | `false` تا رسیدن کدهای الگو |
| `MAHDIEH_CORS_ORIGINS` | **خالی** تا مشخص‌شدن دامنهٔ فرانت |

### ۳. بازرسی پیش از انتشار

```bash
sudo scripts/preflight_production.sh            # فقط می‌خواند؛ تا «۰ ایراد بلوکه» تکرار کنید
sudo scripts/preflight_production.sh --with-dast # + کاوش امنیتی روی همان سرور
```

---

## T-1 — تمرین خشک (یک روز پیش)

هدف: همهٔ کارهای پرریسک **یک بار** انجام شده باشند، پیش از آنکه زائر منتظر باشد.

```bash
# ۱) استقرار واقعی، ولی بدون بازکردن ترافیک
sudo systemctl start mahdieh
curl -fsS -H 'X-Forwarded-Proto: https' http://127.0.0.1:8000/health/ready/ | python3 -m json.tool

# ۲) تمرین بازگردانی از پشتیبانِ دیشب (در پایگاه‌دادهٔ قرنطینه)
sudo systemctl start mahdieh-drill.service && journalctl -u mahdieh-drill -n 30 -o cat

# ۳) آژیر پایش را یک بار بشنوید (رانبوک monitoring.md بخش ۵)
sudo systemctl stop valkey-server && python manage.py healthcheck --base-url http://127.0.0.1:8000 --json
sudo systemctl start valkey-server
```

**معیار عبور:** آمادگی `healthy` · تمرین بازگردانی سبز · آژیر در دو جهت (خرابی و بهبود) کار کند.

---

## T-0 — روز انتشار

### گام ۱ — «سایت هنوز بسته است»

```bash
sudo systemctl stop mahdieh 2>/dev/null || true
sudo -u mahdieh git -C /srv/mahdieh fetch --all && sudo -u mahdieh git -C /srv/mahdieh reset --hard origin/main
```

### گام ۲ — نصب و استقرار

```bash
cd /srv/mahdieh
sudo -u mahdieh .venv/bin/python src/manage.py migrate --noinput
sudo -u mahdieh .venv/bin/python src/manage.py collectstatic --noinput --clear
sudo -u mahdieh .venv/bin/python src/manage.py production_check --strict   # باید ۰ برگرداند
sudo install -m 644 deploy/systemd/mahdieh*.service deploy/systemd/mahdieh*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mahdieh mahdieh-outbox.service
sudo systemctl enable --now mahdieh-backup.timer mahdieh-drill.timer mahdieh-reminders.timer \
  mahdieh-holds.timer mahdieh-retention.timer mahdieh-healthcheck.timer
sudo cp deploy/nginx/mahdieh.conf /etc/nginx/sites-available/mahdieh
sudo ln -sf /etc/nginx/sites-available/mahdieh /etc/nginx/sites-enabled/mahdieh
sudo nginx -t && sudo systemctl reload nginx
```

> **چرا `--strict` این‌جا؟** در روز انتشار هیچ استثنایی نمی‌پذیریم. در روزهای
> عادی، دو هشدار عمدی (شمارهٔ پذیرش و الگوهای پیامک) بلوکه نمی‌کنند؛ امروز
> باید هر دو هم بسته شوند یا آگاهانه پذیرفته و در برگهٔ انتشار نوشته شوند.

### گام ۳ — سنجش پیش از بازکردن

```bash
curl -fsS https://mahdie-shohada.ir/health/live/
curl -fsS https://mahdie-shohada.ir/health/ready/ | python3 -m json.tool
scripts/dast_probe.py --base-url https://mahdie-shohada.ir      # باید کد ۰ بدهد
scripts/load_test.py --users 20 --duration 60 --server-cpus 4    # دود بار واقعی
```

### گام ۴ — بازکردن و اعلام

| کار | معیار |
| --- | --- |
| صفحهٔ اصلی | ۲۰۰، بدون خطای کنسول، تصاویر و CSS کامل |
| تقویم | ۹۰ روز آینده، نیم‌روزها درست |
| یک رزرو آزمایشی | ثبت ⇒ پیامک می‌آید (اگر الگو فعال است) ⇒ لغو ⇒ سانس آزاد |
| پایش | `systemctl list-timers 'mahdieh*'` همه فعال؛ `mahdieh-healthcheck` سبز |
| پشتیبان | `systemctl start mahdieh-backup` و بررسی `ls -lh /var/backups/mahdieh` |

---

## T+0 — بلافاصله پس از انتشار (به ترتیب)

۱. **درخواست کدهای الگو از پنل** (متن آماده: `docs/sms/pattern-request.md`).
۲. کدها که رسید: `sudo scripts/sms_golive.sh --codes "…" --restart --selftest --to 09XX…`
۳. شمارهٔ پذیرش را در `/etc/mahdieh/mahdieh.env` بگذارید و `--restart`.
۴. `production_check --strict` را دوباره بزنید — این بار باید صفر هشدار بدهد.
۵. عدد بار ۴ هسته را ثبت کنید: `docs/load/production-capacity-plan.md`.

---

## بازگردانی (اگر چیزی بد شد)

| نشانه | کار | زمان تقریبی |
| --- | --- | --- |
| آمادگی ۵۰۳ برای > ۱ دقیقه | `sudo systemctl restart mahdieh` و لاگ | ۳۰ ثانیه |
| خطای کد بعد از انتشار | `git reset --hard <کامیت قبلی>` + migrate + restart | ۵ دقیقه |
| دادهٔ خراب‌شده | `scripts/db_restore.sh <فایل>` (رانبوک incident-response) | ۱۵ دقیقه |
| TLS منقضی | `certbot renew --force-renewal` + reload nginx | ۲ دقیقه |

معیار «بازگشت به حالت سالم»: `healthcheck` کد ۰ + `systemctl --failed` خالی +
یک رزرو آزمایشی کامل (ثبت و لغو).

---

## پس از روز انتشار

```bash
python scripts/readiness_report.py     # برگهٔ آمادگی برای بایگانی
```

این برگه را در `docs/release/` نگه دارید و در گزارش تحویل به کارفرما ذکر کنید.
