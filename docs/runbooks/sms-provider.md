# راه‌اندازی پنل پیامک واقعی

این سند گام‌به‌گام می‌گوید چگونه سامانه را از حالت ماک (بی‌شبکه، برای توسعه و
تست) به یک درگاه پیامک واقعی ایرانی سوئیچ کنیم — بدون تغییر یک خط کد.

## وضعیت کنونی

| مورد | وضعیت |
|---|---|
| آداپتورها | `mock`، `console` (محلی، بی‌شبکه) · `kavenegar`، `ghasedak` (واقعی) |
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
- **قالب‌ها:** متن‌ها در `src/apps/notifications/templates.py` و کلیدها در
  `notifications/choices.py` هستند؛ پیام بی‌قالب (متن خام در کد) ممنوع است.
