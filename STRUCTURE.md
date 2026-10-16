# ساختار پروژه

> این فایل **تولیدشده** است؛ دستی ویرایش نکنید.
> بازتولید: `uv run python scripts/generate_structure.py`
> بررسی drift در CI: `uv run python scripts/generate_structure.py --check`

## اپلیکیشن‌ها

| اپ | مسئولیت | مدل | مهاجرت | فایل‌های تست |
|---|---|---:|---:|---:|
| `audit` | حسابرسی زنجیره‌ای و عدم‌انکار | 2 | 4 | 1 |
| `core` | هستهٔ مشترک: BaseModel، RFC 9457، Outbox، هم‌توانی، تقویم جلالی، سلامت | 4 | 3 | 0 |
| `identity` | هویت تلفن‌محور، پروفایل، نقش‌ها | 3 | 2 | 1 |
| `inventory` | — | 7 | 1 | 1 |
| `notifications` | — | 1 | 1 | 0 |
| `reporting` | — | 0 | 0 | 0 |
| `reservation` | — | 5 | 1 | 3 |

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
audit/
├── __init__.py
├── admin.py  # پنل مدیریت
├── apps.py  # پیکربندی اپ
├── choices.py  # شمارشی‌های دامنه
├── models.py  # مدل‌های داده
├── services.py  # منطق کسب‌وکار و نوشتن
│
core/
├── __init__.py
├── admin.py  # پنل مدیریت
├── api_urls.py
├── apps.py  # پیکربندی اپ
├── cache.py
├── choices.py  # شمارشی‌های دامنه
├── context_processors.py
├── crypto.py
├── errors.py
├── fields.py
├── health.py
├── health_urls.py
├── idempotency.py
├── jalali.py
├── logging.py
├── management/__init__.py
├── management/commands/__init__.py
├── management/commands/outbox_relay.py
├── metrics.py
├── middleware.py
├── models.py  # مدل‌های داده
├── outbox.py
├── pagination.py
├── problems.py
├── schema.py
├── urls.py  # مسیرها
├── uuid7.py
├── views.py  # لایهٔ HTTP
│
identity/
├── __init__.py
├── admin.py  # پنل مدیریت
├── api_urls.py
├── apps.py  # پیکربندی اپ
├── choices.py  # شمارشی‌های دامنه
├── models.py  # مدل‌های داده
├── permissions.py  # کنترل دسترسی
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
├── choices.py  # شمارشی‌های دامنه
├── models.py  # مدل‌های داده
├── ports.py
├── providers.py
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
├── api_urls.py
├── apps.py  # پیکربندی اپ
├── availability.py
├── choices.py  # شمارشی‌های دامنه
├── codes.py
├── management/__init__.py
├── management/commands/__init__.py
├── management/commands/expire_holds.py
├── management/commands/seed_demo.py
├── models.py  # مدل‌های داده
├── policy.py
├── selectors.py  # خواندن داده (بدون side-effect)
├── serializers.py  # اعتبارسنجی ورودی/خروجی
├── services.py  # منطق کسب‌وکار و نوشتن
├── views.py  # لایهٔ HTTP
```
