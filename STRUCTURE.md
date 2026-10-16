# ساختار پروژه

> این فایل **تولیدشده** است؛ دستی ویرایش نکنید.
> بازتولید: `uv run python scripts/generate_structure.py`
> بررسی drift در CI: `uv run python scripts/generate_structure.py --check`

## اپلیکیشن‌ها

| اپ | مسئولیت | مدل | مهاجرت | فایل‌های تست |
|---|---|---:|---:|---:|
| `analytics` | تحلیل اشغال، پیش‌بینی، رضایت زائر و داشبورد پذیرش | 0 | 0 | 0 |
| `audit` | حسابرسی زنجیره‌ای و عدم‌انکار | 2 | 7 | 1 |
| `core` | هستهٔ مشترک: BaseModel، RFC 9457، Outbox، هم‌توانی، تقویم جلالی، سلامت | 4 | 3 | 0 |
| `identity` | هویت تلفن‌محور، پروفایل، نقش‌ها | 3 | 3 | 1 |
| `inventory` | واحدها، طبقات، امکانات، بلوک‌ها و شبکهٔ ساعتی نیم‌روز | 7 | 1 | 1 |
| `notifications` | پیامک و قالب‌ها با ارائه‌دهندهٔ ماک (تا اتصال پنل واقعی) | 1 | 1 | 0 |
| `reporting` | گزارش اکسل چندبرگی، راست‌به‌چپ و ممیزی‌شده | 0 | 0 | 0 |
| `reservation` | چرخهٔ عمر رزرو، سیاست‌ها، تخصیص، گواهی و تقویم موجودی | 7 | 4 | 3 |
| `waitlist` | صف انتظار، پیشنهاد آبشاری و پذیرش یک‌کلیکی | 2 | 1 | 0 |
| `web` | رابط SSR + HTMX: صفحات زائر، تابلوی پذیرش و صفحهٔ تأیید گواهی | 0 | 0 | 1 |

## قرارداد لایه‌ها

| فایل | مسئولیت |
|---|---|
| `models.py` | مدل‌های داده |
| `choices.py` | شمارشی‌های دامنه |
| `services.py` | منطق کسب‌وکار و نوشتن |
| `selectors.py` | خواندن داده (بدون side-effect) |
| `serializers.py` | اعتبارسنجی ورودی/خروجی |
| `views.py` | لایهٔ HTTP |
| `urls.py` | مسیرها |
| `permissions.py` | کنترل دسترسی |
| `throttles.py` | محدودسازی نرخ |
| `tasks.py` | کار پس‌زمینه |
| `events.py` | رخدادهای دامنه |
| `admin.py` | پنل مدیریت |
| `apps.py` | پیکربندی اپ |

قواعدی که با تست (`src/tests/test_architecture_discipline.py`) اجرا می‌شوند:

- هر ماژول تولیدی باید docstring داشته باشد.
- هر کلاس/تابع عمومی باید docstring داشته باشد.
- `views.py` نباید مستقیماً روی مدل/کوئری‌ست بنویسد (متناقض با لایهٔ سرویس).
- `models.py`/`services.py` نباید به لایهٔ HTTP (DRF) وابسته باشند.
- `TODO`/`FIXME` باید شمارهٔ issue داشته باشد.

## درخت اپلیکیشن‌ها

```text
analytics/
├── __init__.py
├── api_urls.py
├── apps.py  # پیکربندی اپ
├── charts.py
├── insights.py
├── serializers.py  # اعتبارسنجی ورودی/خروجی
├── views.py  # لایهٔ HTTP
│
audit/
├── __init__.py
├── admin.py  # پنل مدیریت
├── apps.py  # پیکربندی اپ
├── choices.py  # شمارشی‌های دامنه
├── management/commands/audit_verify.py
├── models.py  # مدل‌های داده
├── services.py  # منطق کسب‌وکار و نوشتن
│
core/
├── __init__.py
├── admin.py  # پنل مدیریت
├── api_urls.py
├── apps.py  # پیکربندی اپ
├── cache.py
├── checks.py
├── choices.py  # شمارشی‌های دامنه
├── context_processors.py
├── crypto.py
├── errors.py
├── events.py  # رخدادهای دامنه
├── fields.py
├── health.py
├── health_urls.py
├── idempotency.py
├── infrastructure.py
├── jalali.py
├── logging.py
├── management/__init__.py
├── management/commands/__init__.py
├── management/commands/outbox_relay.py
├── management/commands/retention_sweep.py
├── metrics.py
├── middleware.py
├── models.py  # مدل‌های داده
├── outbox.py
├── pagination.py
├── problems.py
├── schema.py
├── serializers.py  # اعتبارسنجی ورودی/خروجی
├── shedding.py
├── site.py
├── text.py
├── urls.py  # مسیرها
├── uuid7.py
├── views.py  # لایهٔ HTTP
├── xorigin.py
│
identity/
├── __init__.py
├── admin.py  # پنل مدیریت
├── api_urls.py
├── apps.py  # پیکربندی اپ
├── choices.py  # شمارشی‌های دامنه
├── models.py  # مدل‌های داده
├── permissions.py  # کنترل دسترسی
├── retention.py
├── serializers.py  # اعتبارسنجی ورودی/خروجی
├── services.py  # منطق کسب‌وکار و نوشتن
├── throttles.py  # محدودسازی نرخ
├── validators.py
├── views.py  # لایهٔ HTTP
│
inventory/
├── __init__.py
├── admin.py  # پنل مدیریت
├── api_urls.py
├── apps.py  # پیکربندی اپ
├── choices.py  # شمارشی‌های دامنه
├── lattice.py
├── management/__init__.py
├── management/commands/__init__.py
├── management/commands/seed_inventory.py
├── models.py  # مدل‌های داده
├── selectors.py  # خواندن داده (بدون side-effect)
├── serializers.py  # اعتبارسنجی ورودی/خروجی
├── services.py  # منطق کسب‌وکار و نوشتن
├── views.py  # لایهٔ HTTP
│
notifications/
├── __init__.py
├── admin.py  # پنل مدیریت
├── apps.py  # پیکربندی اپ
├── checks.py
├── choices.py  # شمارشی‌های دامنه
├── management/__init__.py
├── management/commands/__init__.py
├── management/commands/sms_patterns.py
├── models.py  # مدل‌های داده
├── pattern_texts.py
├── patterns.py
├── ports.py
├── providers.py
├── retention.py
├── services.py  # منطق کسب‌وکار و نوشتن
├── templates.py
│
reporting/
├── __init__.py
├── api_urls.py
├── apps.py  # پیکربندی اپ
├── archive.py
├── dataset.py
├── excel.py
├── management/__init__.py
├── management/commands/__init__.py
├── management/commands/export_report.py
├── ranges.py
├── services.py  # منطق کسب‌وکار و نوشتن
├── theme.py
├── views.py  # لایهٔ HTTP
│
reservation/
├── __init__.py
├── admin.py  # پنل مدیریت
├── allocation.py
├── api_urls.py
├── apps.py  # پیکربندی اپ
├── availability.py
├── certificate.py
├── choices.py  # شمارشی‌های دامنه
├── codes.py
├── feedback.py
├── ics.py
├── management/__init__.py
├── management/commands/__init__.py
├── management/commands/expire_holds.py
├── management/commands/seed_demo.py
├── management/commands/send_booking_reminders.py
├── management/commands/send_feedback_requests.py
├── models.py  # مدل‌های داده
├── policy.py
├── policy_api_urls.py
├── reminders.py
├── selectors.py  # خواندن داده (بدون side-effect)
├── serializers.py  # اعتبارسنجی ورودی/خروجی
├── services.py  # منطق کسب‌وکار و نوشتن
├── views.py  # لایهٔ HTTP
│
waitlist/
├── __init__.py
├── api_urls.py
├── apps.py  # پیکربندی اپ
├── choices.py  # شمارشی‌های دامنه
├── handlers.py
├── management/__init__.py
├── management/commands/__init__.py
├── models.py  # مدل‌های داده
├── selectors.py  # خواندن داده (بدون side-effect)
├── services.py  # منطق کسب‌وکار و نوشتن
├── tokens.py
├── views.py  # لایهٔ HTTP
│
web/
├── __init__.py
├── apps.py  # پیکربندی اپ
├── context_processors.py
├── forms.py
├── help_content.py
├── mixins.py
├── presenters.py
├── services.py  # منطق کسب‌وکار و نوشتن
├── templatetags/__init__.py
├── templatetags/mahdieh.py
├── urls.py  # مسیرها
├── views.py  # لایهٔ HTTP
├── views_calendar.py
├── views_dev.py
├── views_feedback.py
├── views_help.py
├── views_insights.py
├── views_staff.py
├── views_waitlist.py
```
