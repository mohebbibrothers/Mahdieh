# رانبوک — استقرار روی میزبان cPanel

> میزبان: حساب `mahdiesh` · CloudLinux 9 (CageFS) · Apache + Passenger · MariaDB 11.4 ·
> alt-python 3.12 · سقف LVE: ۱ گیگ دیسک، ۱ گیگ حافظه، ۱ هسته، ۵۰ فرآیند، ۲۰ ورودی هم‌زمان.
> دلیل معماری: [ADR-0020](../adr/0020-mariadb-on-the-shared-host.md) و
> [ADR-0021](../adr/0021-database-backed-cache-and-cron.md).

## ۱. جریان انتشار

```text
push به main ─► CI (۹ شغل، MariaDB 11.4، پایتون ۳.۱۲ و ۳.۱۳) ─► سبز
             ─► Release bundle:
                  bundle   چرخ manylinux برای mysqlclient + بستهٔ خودبسنده + sha256
                  rehearse استقرار واقعی روی AlmaLinux 9 + MariaDB 11.4 (بدون کامپایلر، بدون PyPI)
                  publish  پیش‌انتشار «edge» (برچسب v* ⇐ انتشار نسخه‌دار)
میزبان       ─► ~/mahdieh/bin/mahdieh-deploy fetch        (یک فرمان)
```

میزبان به PyPI دسترسی ندارد؛ همه‌چیز از GitHub Release می‌آید و با sha256 راستی‌آزمایی
می‌شود. بستهٔ تمرین‌نشده هرگز منتشر نمی‌شود.

## ۲. چیدمان روی میزبان

```text
~/.mahdieh/mahdieh.env      فایل محیط تولید (۶۰۰) — تنها منبع رازها
~/.mahdieh/my.cnf           اعتبار کلاینت MariaDB برای اپراتور (۶۰۰)
~/mahdieh/
  app/passenger_wsgi.py     ریشهٔ برنامهٔ Passenger (پوستهٔ ثابت)
  app/tmp/restart.txt       لمس ⇒ بازنشانی نرم
  current -> releases/<id>  تنها چیزی که استقرار عوض می‌کند (اتمی)
  releases/<id>/            کد هر انتشار (۳ تای آخر نگه داشته می‌شوند)
    .venv -> ../../venvs/<key>   محیط مجازی مشترک تا وقتی وابستگی‌ها عوض نشده
    src/.env -> ~/.mahdieh/mahdieh.env
    var -> ../../shared/var
  venvs/<key>/              محیط‌های مجازی (هرس خودکار)
  shared/var/{backups,drills,log,locks}   داده‌های پایدار
  shared/media/
  bin/{deploy.sh,cron_run.sh,mahdieh-deploy}
```

## ۳. فرمان‌های روزمره

همهٔ کارها با فرمان یکپارچهٔ **`mahdieh`** (`deploy/cpanel/mahdieh_cli.py`؛ هر استقرار آن را در
`~/mahdieh/bin` نصب و به PATH در `~/.bashrc` می‌افزاید؛ تکمیل خودکار با Tab).

| کار | فرمان |
|---|---|
| **به‌روزرسانی یک‌خطی** (بک‌اند و، از مرحلهٔ ۲، فرانت) | `mahdieh update` |
| فقط یک بخش | `mahdieh update back` · `mahdieh update front` |
| فقط ببین چه چیزی تازه است | `mahdieh update --check` |
| نسخهٔ مشخص | `mahdieh update back --tag v1.0.0` |
| داشبورد | `mahdieh` |
| معاینهٔ کامل (کد خروج ۰/۱) | `mahdieh doctor` |
| بازگشت فوری | `mahdieh rollback` (یا `mahdieh rollback back <RELEASE>`) |
| گزارش‌ها | `mahdieh logs` · `mahdieh logs deploy -f` · `mahdieh logs errors` |
| فرمان مدیریتی جنگو | `mahdieh manage <فرمان>` (مثلاً `createsuperuser`) |

`mahdieh update` اگر آخرین کامیت main هنوز در GitHub در حال آزمون/ساخت/تمرین است صبر می‌کند
(نوار پیشرفت)، اگر CI رد شود منتشر نمی‌کند، فهرست تغییرها را نشان می‌دهد، هرگز به نسخهٔ
قدیمی‌تر برنمی‌گردد و همان استقرار اتمی ده‌مرحله‌ای با بازگشت خودکار را اجرا می‌کند. GitHub
بی‌توکن خوانده می‌شود؛ اگر سقف نرخ API پر باشد فقط داده‌های اختیاری (تغییرها، انتظار) حذف
می‌شوند. ابزار سطح پایین همچنان هست: `~/mahdieh/bin/mahdieh-deploy {fetch,rollback,status}`.

`manage.py` ماژول تنظیمات را از `src/.env` می‌خواند؛ لازم نیست `DJANGO_SETTINGS_MODULE`
را دستی تنظیم کنید.

## ۴. مراحل `deploy.sh activate` (هر مرحله دروازه است)

| # | مرحله | شکست یعنی |
|---|---|---|
| ۱ | محیط مجازی (بازمصرف با کلید؛ نصب آفلاین؛ اگر چرخ mysqlclient بالا نیامد، ساخت از sdist با `mysql_config`) | سایت دست‌نخورده |
| ۲ | پیوندهای `.env`/`var`/`media` (فایل محیط باید ۶۰۰ باشد) | سایت دست‌نخورده |
| ۳ | `production_check` | سایت دست‌نخورده |
| ۴ | پشتیبان تأییدشده، فقط اگر مهاجرت معلق هست | سایت دست‌نخورده |
| ۵ | `migrate` + `createcachetable` | سایت دست‌نخورده (مهاجرت‌ها افزایشی‌اند) |
| ۶ | `collectstatic` | سایت دست‌نخورده |
| ۷ | آزمون درون‌فرآیندی WSGI انتشار تازه (`/health/ready/`، شناسهٔ انتشار، صفحهٔ اصلی) | سایت دست‌نخورده |
| ۸ | جابه‌جایی اتمی `current` + لمس `restart.txt` | — |
| ۹ | راستی‌آزمایی HTTPS: دامنه باید **همین** شناسهٔ انتشار را سالم سرو کند | **بازگشت خودکار** به انتشار قبلی |
| ۱۰ | crontab، ابزارها، هرس | هشدار (سایت سالم است) |

## ۵. کارهای زمان‌بندی‌شده (crontab)

بلوک میان `# >>> mahdieh` و `# <<< mahdieh` را `deploy.sh` می‌نویسد؛ دستی ویرایش نکنید.
بقیهٔ crontab دست‌نخورده می‌ماند. هر کار از `cron_run.sh` می‌گذرد: تک‌نمونه (flock)،
سقف زمان، لاگ چرخشی.

| کار | زمان | سقف |
|---|---|---|
| رلهٔ صندوق خروجی (`--loop --interval 5 --max-seconds 55`) | هر دقیقه | ۷۵ ثانیه |
| آزادسازی نگه‌داشت‌ها | هر ۱۰ دقیقه | ۵ دقیقه |
| یادآوری رزرو | دقیقهٔ ۵ هر ساعت، ۷ تا ۲۲ | ۱۵ دقیقه |
| درخواست بازخورد | دقیقهٔ ۱۵ هر ساعت، ۸ تا ۲۲ | ۱۵ دقیقه |
| پایش سلامت (HTTPS، هشدار ایمیلی فقط هنگام تغییر وضعیت) | هر ۵ دقیقه | ۲ دقیقه |
| پشتیبان شبانه (نگه‌داری ۱۴ روز) | ۰۲:۳۰ | ۳۰ دقیقه |
| جاروب نشست‌ها | ۰۳:۴۵ | ۱۵ دقیقه |
| تمرین بازیابی در `mahdiesh_drill` | پنجشنبه ۰۴:۰۰ | ۳۰ دقیقه |
| پاک‌سازی دادهٔ منقضی (حریم خصوصی) | یکشنبه ۰۴:۳۰ | ۳۰ دقیقه |

## ۶. ظرفیت

- هر فرآیند Passenger دو اتصال پایگاه‌داده دارد (`default` + `cache`)، پایدار با بررسی
  سلامت (`CONN_MAX_AGE=300`).
- بدترین حالت: ۲۰ فرآیند ورودی × ۲ + ۱۰ ذخیره (مهاجرت، پشتیبان، cron، اپراتور) = **۵۰**
  ≤ یک‌سوم `max_connections` سرور (۱۵۱). آزمون `test_capacity_formula_fits_host_connection_budget`
  نگهبان همین فرمول است.
- حافظه: هر فرآیند جنگو حدود ۸۰ تا ۱۰۰ مگابایت؛ Passenger فرآیندها را بر اساس ترافیک
  می‌سازد و بیکارها را می‌بندد. سقف ۱ گیگ برای چند فرآیند هم‌زمان کافی است.
- دیسک: هر انتشار حدود ۱۰ مگابایت، هر محیط مجازی حدود ۸۰ مگابایت (فقط وقتی وابستگی‌ها
  عوض شوند تازه ساخته می‌شود)، پشتیبان‌ها فشرده. `mahdieh-deploy status` مصرف را نشان
  می‌دهد.

## ۷. عیب‌یابی

| نشانه | بررسی |
|---|---|
| استقرار در مرحلهٔ ۳ می‌ایستد | خروجی `production_check` همان‌جا چاپ شده؛ مقدار ناقص را در `~/.mahdieh/mahdieh.env` اصلاح کنید و `mahdieh-deploy activate ~/mahdieh/releases/<id>` را دوباره بزنید |
| ۵۰۰ با «Passenger error #2 … Permission denied» | وب‌سرور به ریشهٔ برنامه راه ندارد: `chmod 711 ~/mahdieh ~/mahdieh/app ~/mahdieh/app/tmp && chmod 644 ~/mahdieh/app/passenger_wsgi.py && touch ~/mahdieh/app/tmp/restart.txt` (`deploy.sh` همین را در هر استقرار اعمال می‌کند) |
| مرحلهٔ ۹ شکست و بازگشت خودکار | `tail ~/mahdieh/shared/var/log/deploy.log`؛ گزارش خطای Passenger: `~/logs/` یا «Errors» در cPanel |
| صفحهٔ «We're sorry, but something went wrong» | `~/mahdieh/current/.venv/bin/python ~/mahdieh/current/deploy/cpanel/wsgi_probe.py ~/mahdieh/current --host mahdei.n-cpanel.xyz` خطای واقعی را چاپ می‌کند |
| پیامک‌ها نمی‌روند | `tail ~/mahdieh/shared/var/log/outbox.log` و `crontab -l` |
| «رد شد: اجرای قبلی هنوز در جریان است» پیاپی | کار گیر کرده؛ `ps -u $USER -o pid,etime,cmd` |
| خطای اتصال پایگاه‌داده | `mariadb --defaults-file=~/.mahdieh/my.cnf -e 'select 1'` و `DATABASE_URL` در فایل محیط |

## ۸. امنیت

- رازها فقط در `~/.mahdieh/mahdieh.env` (۶۰۰)؛ اسکریپت‌ها هرگز آن را `source` نمی‌کنند
  (مقدارهای فارسی بی‌نقل‌قول اجرا می‌شدند) و جنگو خودش آن را می‌خواند.
- `MAHDIEH_TRUSTED_PROXY_COUNT=0`: Apache مستقیم به برنامه وصل است؛ `X-Forwarded-For`
  کاربر نادیده گرفته می‌شود تا سقف نرخ کد ورود با جعل هدر دور زده نشود.
- بسته با sha256 راستی‌آزمایی می‌شود و پیش از بازشدن، مسیر مطلق یا `..` در آن رد می‌شود.
