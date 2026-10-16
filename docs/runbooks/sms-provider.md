# راه‌اندازی پنل پیامک واقعی

این سند گام‌به‌گام می‌گوید چگونه سامانه را از حالت ماک (بی‌شبکه، برای توسعه و
تست) به یک درگاه پیامک واقعی ایرانی سوئیچ کنیم — بدون تغییر یک خط کد.

## وضعیت کنونی

| مورد | وضعیت |
|---|---|
| آداپتورها | `mock`، `console` (محلی، بی‌شبکه) · `iranpayamak`، `kavenegar`، `ghasedak` (واقعی) |
| پنل کارفرما | **ایران‌پیامک** — ارسال الگومحور (Pattern API)، خط مشترک با مهدیهٔ شهدا |
| وضعیت الگوها | ۳ الگو فعال (کد ورود، خوش‌آمد، تأیید رزرو) · ۱ الگو بی‌کد (تغییر تاریخ) · ۷ الگو ناموجود — `manage.py sms_patterns` |
| پیش‌فرض محیط توسعه | `SMS_PROVIDER=mock`، `SMS_ENABLED=false` |
| ارسال واقعی | تنها با `SMS_ENABLED=true` و کلید معتبر |
| آزمون‌ها | هیچ تستی به شبکه وصل نمی‌شود (سد با تست تضمین شده است) |

قرارداد آداپتور در `src/apps/notifications/ports.py` است: هر ارائه‌دهندهٔ تازه
(ملی‌پیامک، فراز اس‌ام‌اس، …) یک کلاس کوچک است، نه تغییری در دامنه.

## گام‌ها

۱) **اطلاعات پنل را آماده کنید** — از پنل پیامکی (کاوه‌نگار یا قاصدک یا هر
درگاه دیگر) این چهار قلم لازم است:

- `SMS_PROVIDER` — نام آداپتور: `kavenegar` یا `ghasedak`
- `SMS_API_KEY` — کلید API پنل
- `SMS_SENDER_NUMBER` — شمارهٔ فرستندهٔ ثبت‌شده (مثلاً `10004346`)
- `SMS_FALLBACK_PROVIDER` — اختیاری: درگاه دوم برای زمان اختلال (مثلاً `console`
  در staging)

۲) **تنظیم متغیرهای محیطی** در `.env` سرور (فایل در مخزن نیست):

```dotenv
SMS_ENABLED=true
SMS_PROVIDER=kavenegar
SMS_API_KEY=<کلید پنل>
SMS_SENDER_NUMBER=<شمارهٔ فرستنده>
SMS_FALLBACK_PROVIDER=
SMS_DAILY_BUDGET=1000
```

۳) **آزمون دود بی‌خطر** (بدون فرستادن پیامک واقعی):

```bash
uv run python manage.py shell -c "
from apps.notifications.providers import provider_catalog, is_local_provider
print(provider_catalog())
print('local?', is_local_provider())
"
```

باید `has_api_key: True` برای آداپتور انتخابی و `local?: False` را ببینید.
اگر `local?` مقدار `True` بدهد، یعنی آداپتور واقعی «محلی» شمرده شده است — این
وضعیت خطرناک است (ارسال در محیط آزمایشی) و باید به‌عنوان اشکال گزارش شود.

۴) **نخستین ارسال واقعی** با یک شمارهٔ آزمون:

```bash
uv run python manage.py shell -c "
from apps.notifications.services import send_sms
from apps.notifications.choices import SmsTemplateKey
d = send_sms(phone='+989120000000', template_key=SmsTemplateKey.DRY_RUN,
             context={'message': 'آزمون درگاه مهدیه'})
print(d.status, d.provider, d.provider_ref, d.error)
"
```

سرنوشت پیام در جدول `notifications_smsdelivery` ثبت می‌شود (متن پوشانده‌شده،
شمارهٔ پوشانده‌شده). `status=sent` یعنی اتصال درست است.

۵) **پایش پس از راه‌اندازی** — سه نشانه که باید دیده شود:

- شاخص `sms_sent_total{result="sent"}` در `/health/metrics` رشد می‌کند.
- هیچ ردیفی با `status=failed` و خطای «کلید API … » نباشد.
- سهمیهٔ روزانه (`SMS_DAILY_BUDGET`) مصرف نشده باشد؛ ردیف‌های `blocked` با دلیل
  «سهمیهٔ روزانهٔ پیامک تمام شده است» نشانهٔ مصرف بالا است.

## پنل ایران‌پیامک (ارسال الگومحور) — مسیر واقعی کارفرما

ایران‌پیامک متن آزاد نمی‌پذیرد؛ هر پیام باید «الگوی تأییدشده» داشته باشد و
سامانه فقط کد الگو و مقدار متغیرها را می‌فرستد. تصمیم‌های معماری این مسیر در
`docs/adr/0013-sms-pattern-mode.md` ثبت شده است.

### قرارداد درخواست (مستند پنل، ۱۴۰۵/۰۷/۲۴)

```http
POST https://api.iranpayamak.com/ws/v1/sms/pattern
Api-Key: <کلید پنل>
Content-Type: application/json

{"code": "IWoflaqY6M", "attributes": {"name": "زائر", "rooz": "۲"},
 "recipient": "09121234567", "line_number": "50002178584000",
 "number_format": "persian"}
```

### تنظیم محیط

```dotenv
SMS_ENABLED=true
SMS_PROVIDER=iranpayamak
SMS_API_KEY=<کلید پنل مشترک>
SMS_SENDER_NUMBER=50002178584000      # خط اختصاصی زائرسرا (line_number)
SMS_DAILY_BUDGET=2000
# کد الگوها — هر قالبی که این‌جا/در patterns.py بی‌کد باشد، ارسالش «مسدود» می‌شود
SMS_PATTERN_CODES={"otp_login": "3tPNyQa38p", "welcome": "8iCW9vmPFl", "booking_confirmed": "IWoflaqY6M"}
SMS_ATTRIBUTE_DIGITS=persian
```

### وضعیت الگوها و درخواست کدهای باقی‌مانده

```bash
cd src && uv run python manage.py sms_patterns            # جدول وضعیت
uv run python manage.py sms_patterns --json               # برای پایش ماشینی
uv run python manage.py sms_patterns --check              # کد ۱ اگر قالب حیاتی بی‌الگو باشد
uv run python manage.py sms_patterns --request            # 👈 متن آمادهٔ ارسال به پشتیبانی پنل
make sms-patterns ARGS=--request                         # همان دستور از ریشهٔ مخزن
```

`--request` متنی می‌سازد که مدیر می‌تواند بدون تغییر، برای مالک پنل بفرستد:
نام هر الگو، متن پیشنهادی و نام متغیرهایش. **این متن، تنها راه دریافت کدهای
باقی‌مانده است؛ برای هر الگوی تازه، اول همین را بفرستید.**

### الگوهای فعال و معنای متغیرها

| قالب مهدیه | کد الگو | متغیرها | متن الگو (خلاصه) |
|---|---|---|---|
| `otp_login` | `3tPNyQa38p` | `code` | کد ورود/بازیابی |
| `welcome` | `8iCW9vmPFl` | `name` | خوش‌آمد پس از تکمیل پروفایل |
| `booking_confirmed` | `IWoflaqY6M` | `name`، `rooz` | تأیید رزرو + نشانی زائرسرا |
| `booking_moved` | *(بی‌کد — افت به قبلی)* | `name`، `date` | تغییر تاریخ اقامت |

`rooz` با ارقام فارسی و ممیز فارسی («۲»، «۰٫۵») پر می‌شود؛ مدت اقامت از
`core.jalali.duration_days_label` می‌آید تا همه‌جا یک شکل باشد. اگر متن الگو
در پنل، واحد «روز» را متفاوت می‌خواهد (مثلاً همیشه عدد صحیح)، یک جای عوض
می‌شود: `apps/notifications/patterns.py`.

### آزمون پیش از تولید (بی‌خطر، بدون پیامک واقعی)

```bash
cd src && uv run python manage.py test tests.test_iranpayamak --settings=config.settings.development
uv run pytest tests/test_iranpayamak.py -q
```

۳۴ آزمون این مسیر را می‌پوشانند: نگاشت کدها، شکست پیکربندی، پاسخ‌های شناخته‌شدهٔ
پنل، مسدودشدن قالب بی‌الگو، و **تضمین اینکه هیچ تستی به شبکه وصل نمی‌شود**.

### پس از راه‌اندازی: چه چیزی را بپایید

| نشانه | معنا | اقدام |
|---|---|---|
| `sms_sent_total{result="pattern_missing"}` > ۰ | الگوی قالبی در پنل نیست | `sms_patterns --request` و دریافت کد |
| ردیف‌های `blocked` با دلیل «الگویی … ثبت نشده» | همان مورد بالا، برای یک قالب | همان |
| `sms_sent_total{result="failed"}` | پنل پاسخ خطا داد یا کلید/خط اشتباه است | `error` ردیف تحویل را بخوانید |
| خطای سیستم‌چک `mahdieh.W010` | قالب حیاتی بی‌الگو با `SMS_ENABLED=true` | پیش از انتشار کد الگو را بگیرید |

## بازگشت به حالت ماک

```dotenv
SMS_ENABLED=false
SMS_PROVIDER=mock
```

با این تنظیم، هیچ تماس شبکه‌ای رخ نمی‌دهد و ردیف‌های تحویل با وضعیت
`ارسال‌نشده (محیط غیرتولیدی)` ثبت می‌شوند — تاریخچهٔ پیام‌ها برای بررسی باقی
می‌ماند.

## نکات عملیاتی

- **سهمیه:** `SMS_DAILY_BUDGET` سقف روزانهٔ کل سامانه است (پیش‌فرض ۱۰۰۰). برای
  ایام پیک (ماه‌های زیارتی) پیش از موعد بالا ببرید.
- **ساعات سکوت:** یادآورها و پیشنهادها به تنظیمات زمان‌بندی احترام می‌گذارند؛
  برای تغییر، کلیدهای سیاست `notifications.*` را در `/board/policies/` ببینید.
- **کلید تکراری:** هر پیام `dedup_key` دارد؛ اجرای دوبارهٔ یک فرمان، پیام تکراری
  به زائر نمی‌فرستد.
- **قالب‌ها:** متن‌ها در `src/apps/notifications/templates.py`، کلیدها در
  `notifications/choices.py` و نگاشت به الگوهای پنل در
  `apps/notifications/patterns.py` است؛ پیام بی‌قالب (متن خام در کد) ممنوع است.
- **افزودن پیام تازه:** ۱) کلید در `choices.py`، ۲) متن در `templates.py`،
  ۳) اگر پنل الگومحور است، ثبت الگو در پنل + یک ورودی در `PATTERNS` و کد در
  `SMS_PATTERN_CODES`. تا آن لحظه، پیام «مسدود» ثبت می‌شود — بی‌صدا نمی‌ماند.
