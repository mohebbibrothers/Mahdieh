# راهنمای چرخش رازها — مهدیه

> اصل: رازها **تاریخ انقضا** دارند. چرخش برنامه‌ریزی‌شده، بی‌درد است؛ چرخش
> اجباری در روز حادثه، دردناک. برای هر راز «مالک» و «دورهٔ چرخش» تعیین کنید.

---

## ۱) فهرست رازها و دوره‌ها

| راز | متغیر محیط | دورهٔ چرخش | اثر چرخش |
| --- | --- | --- | --- |
| کلید امضای نشست/CSRF | `MAHDIEH_SECRET_KEY` | ۶ ماه یا پس از هر افشا | همهٔ کاربران باید دوباره وارد شوند (بی‌خطر) |
| کلید رمزنگاری کد ملی | `FIELD_ENCRYPTION_KEY` | **فقط با برنامهٔ مهاجرت داده** | تغییر بی‌برنامه ⇒ کدهای ملی قبلی ناخوانا می‌شوند |
| کلید جست‌وجوی کور کد ملی | `FIELD_HASH_KEY` | هم‌زمان با کلید رمزنگاری | یکتایی/جست‌وجو به کلید قدیم گره می‌خورد |
| رمز پایگاه‌داده | داخل `DATABASE_URL` (یک نسخه) | ۹۰ روز | راه‌اندازی مجدد سرویس |
| اعتبارنامهٔ پیامک | `SMS_API_KEY` | بر پایهٔ سیاست کارفرما | بدون وقفه (درخواست بعدی از کلید نو استفاده می‌کند) |
| کلید لنگر حسابرسی (HMAC) | در همان `SECRET_KEY` مشتق شده | در صورت افشا | لنگرهای قبلی باید همچنان قابل‌بررسی بمانند ⇒ کلید قدیم را نگه دارید |

---

## ۲) چرخش بی‌درد: `MAHDIEH_SECRET_KEY`

```bash
# ۱) کلید تازه
NEW=$(python -c "import secrets;print(secrets.token_urlsafe(64))")

# ۲) نوشتن در محیط با پشتیبان‌گیری از نسخهٔ قبلی
sudo cp /etc/mahdieh/mahdieh.env /etc/mahdieh/mahdieh.env.bak-$(date +%F)
sudo sed -i "s|^MAHDIEH_SECRET_KEY=.*|MAHDIEH_SECRET_KEY=${NEW}|" /etc/mahdieh/mahdieh.env

# ۳) راه‌اندازی مجدد (کاربران دوباره وارد می‌شوند؛ رزروها دست‌نخورده‌اند)
sudo systemctl restart mahdieh mahdieh-outbox
sudo -u mahdieh bash /srv/mahdieh/scripts/deploy_smoke.sh
```

**نکتهٔ مهم:** با تغییر `SECRET_KEY`، توکن‌های امضاشده (لینک‌های نظرسنجی،
کدهای آفلاین گواهی، لینک‌های امضاشده) باطل می‌شوند. اگر می‌خواهید بی‌وقفه
باشد، کلید را در مسیر «کلیدهای فعال + کلید قبلی برای بررسی» ببرید (پارامتر
`SECRET_KEY_FALLBACKS` جنگو) و پس از یک دورهٔ گذار، کلید قدیم را حذف کنید.

---

## ۳) چرخش پرخطر: کلیدهای رمزنگاری کد ملی

`FIELD_ENCRYPTION_KEY` کلید AES-GCM برای کد ملی و `FIELD_HASH_KEY` کلید HMAC
برای `national_id_blind` است. **دادهٔ موجود با کلید قدیم رمز شده است**؛ پس
ترتیب مهاجرت اجباری است:

```bash
# ۱) توقف نوشتن
sudo systemctl stop mahdieh mahdieh-outbox

# ۲) پشتیبان کامل (نقطهٔ بازگشت قطعی)
cd /srv/mahdieh && sudo -u mahdieh scripts/db_backup.sh --label pre-key-rotation

# ۳) هر دو کلید نو را در محیط بگذارید و کلیدهای قدیم را نگه دارید
#    FIELD_ENCRYPTION_KEY_PREVIOUS=…  FIELD_HASH_KEY_PREVIOUS=…

# ۴) اجرای مهاجرت داده (اسکریپت پروژه؛ آزمون‌شده و بازگشت‌پذیر)
sudo -u mahdieh env $(grep -v '^#' /etc/mahdieh/mahdieh.env | xargs) \
  /srv/mahdieh/.venv/bin/python src/manage.py rotate_field_keys --dry-run
sudo -u mahdieh env $(grep -v '^#' /etc/mahdieh/mahdieh.env | xargs) \
  /srv/mahdieh/.venv/bin/python src/manage.py rotate_field_keys --apply

# ۵) راستی‌آزمایی نمونه‌ای و راه‌اندازی
sudo -u mahdieh env … python src/manage.py shell -c \
  "from apps.identity.models import User;u=User.objects.exclude(national_id_encrypted='').first();print(u.national_id_hash[:12] if u else 'no data')"
sudo systemctl start mahdieh mahdieh-outbox
```

> اگر فرمان `rotate_field_keys` آماده نیست، چرخش کلید را **انجام ندهید**؛
> منتظر ابزار بمانید. این تنها رازی است که چرخش دستی‌اش داده را نابود می‌کند.

---

## ۴) رمز پایگاه‌داده

راز **یک جا** زندگی می‌کند: داخل `DATABASE_URL`. همان یک خط را عوض کنید؛
اپ و اسکریپت‌های پشتیبان/بازگردانی هر دو از همان می‌خوانند.

```bash
sudo -u postgres psql -c "ALTER USER mahdieh WITH PASSWORD 'نیو-رمز-قوی';"
# ← جای قانونیِ راز: همان URL (اگر DATABASE_PASSWORD هم تنظیم است، آن را هم
#   همان‌جا به همان مقدار برسانید؛ preflight واگرایی را قرمز می‌کند)
sudo sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql://mahdieh:نیو-رمز-قوی@127.0.0.1:5432/mahdieh|" /etc/mahdieh/mahdieh.env
sudo systemctl restart mahdieh mahdieh-outbox
bash /srv/mahdieh/scripts/deploy_smoke.sh
```

> چرا `DATABASE_PASSWORD` حذف شد؟ چون دو محل برای یک راز یعنی روزی دو مقدار
> متفاوت؛ و آن روز دقیقاً روز حادثه است («پشتیبان گرفته شد، بازگردانی نشد»).
> حالا پیشرفتِ چرخش را با `sudo bash scripts/preflight_production.sh` بسنجید.

---

## ۵) اعتبارنامهٔ پیامک (وقتی کارفرما آن را می‌دهد)

```bash
sudo sed -i \
  -e "s|^SMS_PROVIDER=.*|SMS_PROVIDER=kavenegar|" \
  -e "s|^SMS_API_KEY=.*|SMS_API_KEY=<کلید>|" \
  -e "s|^SMS_SENDER_NUMBER=.*|SMS_SENDER_NUMBER=<شماره>|" \
  /etc/mahdieh/mahdieh.env

# ۱) نخست در حالت خشک: بدون ارسال واقعی، فقط تولید و ثبت تحویل
sudo systemctl restart mahdieh
sudo -u mahdieh env $(grep -v '^#' /etc/mahdieh/mahdieh.env | xargs | sed 's/SMS_ENABLED=.*/SMS_ENABLED=false/') \
  /srv/mahdieh/.venv/bin/python src/manage.py send_feedback_requests --dry-run --limit 3

# ۲) ارسال آزمایشی واقعی به شمارهٔ خودتان (یک پیام)
# ۳) روشن‌کردن ارسال واقعی
sudo sed -i "s|^SMS_ENABLED=.*|SMS_ENABLED=true|" /etc/mahdieh/mahdieh.env
sudo systemctl restart mahdieh
```

جزئیات کامل: `docs/runbooks/sms-provider.md`.

---

## ۶) قاعده‌های همیشگی

1. هیچ رازی در مخزن گیت نوشته نمی‌شود (`.gitignore` همهٔ `.env*` را می‌گیرد).
2. `chmod 640` و مالک `root:mahdieh` برای `/etc/mahdieh/mahdieh.env`.
3. پس از هر چرخش: `scripts/deploy_smoke.sh` + یک بار ورود آزمایشی با پیامک.
4. هر چرخش در `docs/incidents/` یا `CHANGELOG.md` ثبت شود (چه، چه‌وقت، توسط که).
5. آخرین نسخهٔ رازها در گاوصندوق کارفرما (خارج از سرور) نگه‌داری شود؛ بدون
   آن، بازسازی سامانه پس از فاجعه ناقص می‌ماند.
