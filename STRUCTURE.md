# ساختار پروژه

> این فایل **تولیدشده** است؛ دستی ویرایش نکنید.
> بازتولید: `uv run python scripts/generate_structure.py`
> بررسی drift در CI: `uv run python scripts/generate_structure.py --check`

## اپلیکیشن‌ها

| اپ | مسئولیت | مدل | مهاجرت | فایل‌های تست |
|---|---|---:|---:|---:|
| `audit` | حسابرسی زنجیره‌ای و عدم‌انکار | 2 | 2 | 1 |
| `core` | هستهٔ مشترک: BaseModel، RFC 9457، Outbox، هم‌توانی، تقویم جلالی، سلامت | 4 | 3 | 0 |
| `identity` | هویت تلفن‌محور، پروفایل، نقش‌ها | 2 | 1 | 0 |

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
├── errors.py
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
├── urls.py  # مسیرها
├── uuid7.py
├── views.py  # لایهٔ HTTP
│
identity/
├── __init__.py
├── admin.py  # پنل مدیریت
├── apps.py  # پیکربندی اپ
├── choices.py  # شمارشی‌های دامنه
├── models.py  # مدل‌های داده
```
