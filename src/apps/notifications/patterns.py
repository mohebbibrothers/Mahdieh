"""نگاشت قالب‌های مهدیه به «الگوهای» ثبت‌شدهٔ پنل ایران‌پیامک.

چرا این ماژول وجود دارد؟
    ایران‌پیامک (و همهٔ پنل‌های رسمی ایرانی) «ارسال الگومحور» را اجبار می‌کنند:
    متن پیام از پیش در پنل ثبت و تأیید شده است و سامانه فقط **کد الگو** و
    **مقدار متغیرها** را می‌فرستد. پس متن قالب‌های مهدیه
    (``apps/notifications/templates.py``) در محیط واقعی کنار گذاشته می‌شود و
    آنچه به زائر می‌رسد، متن رسمی پنل است.
    این ماژول همان «مرز» است: تنها جایی که می‌داند کدام قالب مهدیه به کدام کد
    الگو می‌رود و کدام متغیر داخلی، کدام ``attribute`` پنل را پر می‌کند.
    اگر روزی پنل عوض شود، فقط همین فایل عوض می‌شود.

دو قاعدهٔ ایمنی (هر دو آزموده‌شده):
    ۱. **هرگز پیام درهم نمی‌فرستیم.** هر نگاشت، «متغیرهای الزامی» را اعلام
       می‌کند؛ اگر یکی در context نباشد، ارسال **مسدود** می‌شود (نه ارسال با
       جای‌خالی پرنشده). «{name} عزیز» بدون نام، بدتر از نرسیدن پیامک است.
    ۲. **کد الگو دادهٔ عملیاتی است، نه ثابت کد.** کدها با متغیر محیطی
       ``SMS_PATTERN_CODES`` (JSON) بازنویسی می‌شوند؛ چون مالک پنل هر زمان
       می‌تواند الگو را ویرایش کند و کد تازه بگیرد — بدون انتشار نسخهٔ جدید.

وضعیت الگوها: در ``MISSING_TEMPLATES`` هر قالبی که الگویش هنوز از پنل گرفته
نشده، با متن پیشنهادی آمده است؛ ``python manage.py sms_patterns --request``
همان متن را برای ارسال به پشتیبانی پنل چاپ می‌کند.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import logging
import re
from typing import Any, Final

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.core.text import PERSIAN_DECIMAL_SEPARATOR, to_persian_digits
from apps.notifications.choices import SmsTemplateKey

logger = logging.getLogger(__name__)

NUMERIC_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d+(?:\.\d+)?$")
PERSIAN_DIGIT_MODE: Final[str] = "persian"
LATIN_DIGIT_MODE: Final[str] = "latin"
DIGIT_MODES: Final[frozenset[str]] = frozenset({PERSIAN_DIGIT_MODE, LATIN_DIGIT_MODE})


@dataclass(frozen=True, slots=True)
class PatternBinding:
    """پیوند یک قالب مهدیه به یک الگوی ثبت‌شدهٔ پنل.

    ``attributes`` ترتیب‌مند است: کلید = نام متغیر داخلی، مقدار = نام
    ``attribute`` در پنل. ``required`` متغیرهایی است که نبودشان یعنی پیام
    ناقص؛ در آن حالت ارسال مسدود می‌شود.
    """

    key: str
    code: str
    title: str
    attributes: Mapping[str, str]
    required: frozenset[str] = field(default_factory=frozenset)
    fallback_key: str | None = None
    note: str = ""


# ── کدهای الگو: امروز همه **تهی** هستند (به‌روزرسانی ۱۴۰۵/۰۷/۲۵) ─────────────
# چرا تهی؟ کارفرما از پنل ایران‌پیامک پیگیری کرد و پاسخ گرفت: پنل، الگوها را
# تنها پس از **بالا آمدن دامنهٔ سایت** تأیید می‌کند؛ بنابراین سه کدی که
# پیش‌تر گرفته بودیم (کد ورود، خوش‌آمد، تأیید رزرو) هم در بازبینی رد شده‌اند.
#
# تصمیم مهندسی (نه تعارف): پیش‌فرض‌ها را **خالی** می‌گذاریم تا سامانه دروغ
# نگوید. با پیکربندی خالی:
#   • ``sms_patterns --check`` با کد خروج ۱ قرمز می‌ماند ⇒ هیچ‌کس گمان نمی‌کند
#     «پیامک کار می‌کند». قرمزِ صادق از سبزِ نمایشی بهتر است.
#   • هر ارسال واقعی، «مسدود» ثبت می‌شود (نه ارسال با کد منقضی که پنل رد کند و
#     کسی نفهمد چرا).
#   • روزی که دامنه بالا آمد، مسیر فعال‌سازی همان است که از روز اول طراحی شد:
#     کدها **داده‌اند**، از ``SMS_PATTERN_CODES``؛ صفر خط تغییر کد.
#     متن کامل همهٔ الگوها آمادهٔ ارسال است (``pattern_texts.py`` و
#     ``docs/sms/pattern-request.md``) و ``scripts/sms_golive.sh`` کل مسیر
#     «کد رسید ⇒ فعال شد ⇒ آزمون واقعی» را یک‌جا انجام می‌دهد.
DEFAULT_PATTERN_CODES: Final[dict[str, str]] = {
    SmsTemplateKey.OTP_LOGIN: "",
    SmsTemplateKey.WELCOME: "",
    SmsTemplateKey.BOOKING_CONFIRMED: "",
    SmsTemplateKey.BOOKING_MOVED: "",
}

PATTERNS: Final[dict[str, PatternBinding]] = {
    SmsTemplateKey.OTP_LOGIN: PatternBinding(
        key=SmsTemplateKey.OTP_LOGIN,
        code=DEFAULT_PATTERN_CODES[SmsTemplateKey.OTP_LOGIN],
        title="کد یک‌بارمصرف",
        attributes={"code": "code"},
        required=frozenset({"code"}),
        note=(
            "متن کامل و آمادهٔ ثبت در ``pattern_texts`` است؛ فعال‌سازی صرفاً با کد. "
            "این قالب **حیاتی** است: بی‌الگو، ورود به سامانه ناممکن است."
        ),
    ),
    SmsTemplateKey.WELCOME: PatternBinding(
        key=SmsTemplateKey.WELCOME,
        code=DEFAULT_PATTERN_CODES[SmsTemplateKey.WELCOME],
        title="خوش‌آمد (تکمیل پروفایل)",
        attributes={"full_name": "name"},
        required=frozenset({"full_name"}),
    ),
    SmsTemplateKey.BOOKING_CONFIRMED: PatternBinding(
        key=SmsTemplateKey.BOOKING_CONFIRMED,
        code=DEFAULT_PATTERN_CODES[SmsTemplateKey.BOOKING_CONFIRMED],
        title="تأیید رزرو",
        attributes={"full_name": "name", "rooz": "rooz"},
        required=frozenset({"full_name", "rooz"}),
        note=(
            "متن مرجعِ لحن (همان چیزی که کارفرما تأیید کرده و در نامهٔ درخواست هم "
            "نقل شده): «{name} عزیز، رزرو شما … {rooz} روز …» + نشانی زائرسرا."
        ),
    ),
    SmsTemplateKey.BOOKING_MOVED: PatternBinding(
        key=SmsTemplateKey.BOOKING_MOVED,
        code=DEFAULT_PATTERN_CODES[SmsTemplateKey.BOOKING_MOVED],
        title="تغییر تاریخ اقامت",
        attributes={"full_name": "name", "date": "date"},
        required=frozenset({"full_name", "date"}),
        # تا رسیدن کد الگوی «تغییر تاریخ»، پیام تأیید رزرو با بازهٔ تازه فرستاده
        # می‌شود: متنش درست است و زائر بی‌خبر نمی‌ماند.
        fallback_key=SmsTemplateKey.BOOKING_CONFIRMED,
        note="کد الگو از پنل درخواست شده است (نگاه کنید به MISSING_TEMPLATES).",
    ),
}

# ── قالب‌هایی که الگویشان هنوز از پنل گرفته نشده ────────────────────────────
MISSING_TEMPLATES: Final[dict[str, dict[str, Any]]] = {
    SmsTemplateKey.BOOKING_CANCELLED: {
        "title": "لغو رزرو",
        "why": "زائر پس از لغو باید بداند سانس آزاد شد؛ وگرنه تلفن پشتیبانی پر می‌شود.",
        "attributes": {"full_name": "name", "code": "code"},
        "sample": "{name} عزیز، رزرو {code} لغو شد و سانس آزاد گردید.",
        "interim": "پیام لغو ارسال نمی‌شود؛ سایت خودِ وضعیت را نشان می‌دهد.",
    },
    SmsTemplateKey.BOOKING_REMINDER: {
        "title": "یادآور پیش از پذیرش",
        "why": "کاهش «عدم مراجعه» (no-show) و آزادکردن سانس‌های بی‌استفاده.",
        "attributes": {"full_name": "name", "unit": "unit", "date": "date"},
        "sample": "{name} عزیز، یادآوری پذیرش {unit} در {date} — زائرسرای امام زمان (عج).",
        "interim": "یادآور ارسال نمی‌شود.",
    },
    SmsTemplateKey.OFFER_CREATED: {
        "title": "پیشنهاد سانس آزادشده (نوبت انتظار)",
        "why": "قلب لیست انتظار: زائر فقط با یک پیامک و یک ضربه سانس را می‌گیرد.",
        "attributes": {
            "full_name": "name",
            "unit": "unit",
            "window": "window",
            "url": "link",
        },
        "sample": (
            "{name} عزیز، سانس مورد نظر شما آزاد شد: {unit}، {window}. پذیرش با یک ضربه: {link}"
        ),
        "interim": "زائر با ایمیل/سایت مطلع نمی‌شود؛ پیشنهاد پس از مهلت بسته می‌شود.",
    },
    SmsTemplateKey.WAITLIST_REQUEUED: {
        "title": "پایان فرصت پیشنهادها",
        "why": "بستن حلقهٔ انتظار؛ زائر باید بداند نوبتش تمام شد تا دوباره تلاش کند.",
        "attributes": {"full_name": "name", "window": "window", "offers": "offers"},
        "sample": "{name} عزیز، در بازهٔ {window} سانس مناسبی آزاد نشد؛ درخواست شما بسته شد.",
        "interim": "پیام ارسال نمی‌شود.",
    },
    SmsTemplateKey.OFFER_ACCEPTED: {
        "title": "تأیید پذیرش پیشنهاد",
        "why": "زائر باید مطمئن شود سانسش ثبت شد؛ بی‌پیامک، احتمال رزرو تکراری هست.",
        "attributes": {"full_name": "name", "unit": "unit", "code": "code"},
        "sample": "{name} عزیز، پیشنهاد را پذیرفتید و {unit} به نام شما ثبت شد.",
        "interim": "پیام ارسال نمی‌شود؛ رزرو در «رزروهای من» دیده می‌شود.",
    },
    SmsTemplateKey.WAITLIST_JOINED: {
        "title": "ثبت درخواست انتظار",
        "why": "زائر باید جای خود را در نوبت بداند، وگرنه چند بار ثبت می‌کند.",
        "attributes": {
            "full_name": "name",
            "unit": "unit",
            "window": "window",
            "position": "position",
        },
        "sample": "{name} عزیز، درخواست انتظار شما برای {unit} در {window} ثبت شد.",
        "interim": "پیام ارسال نمی‌شود؛ نوبت در «انتظارهای من» دیده می‌شود.",
    },
    SmsTemplateKey.FEEDBACK_REQUEST: {
        "title": "نظرسنجی پس از اقامت",
        "why": "بدون پیامک، نظرسنجی نه دیده می‌شود و نه پر می‌شود.",
        "attributes": {"full_name": "name", "unit": "unit", "url": "link"},
        "sample": "{name} عزیز، اقامت شما در {unit} چطور بود؟ پاسخ چند ثانیه‌ای: {link}",
        "interim": "پیام نظرسنجی ارسال نمی‌شود؛ کارکنان حضوری یادآور می‌شوند.",
    },
}

# قالب‌های درونی (کارکنان/آزمون): بدون آن‌ها سایت سالم است، اما داشتنشان
# پایش عملیاتی را کامل می‌کند. عمداً از MISSING_TEMPLATES جدا نگه داشته شده‌اند
# تا «کارهای زائر» و «کارهای ستاد» در گزارش قاطی نشوند.
INTERNAL_TEMPLATES: Final[dict[str, dict[str, Any]]] = {
    SmsTemplateKey.STAFF_ALERT: {
        "title": "هشدار عملیاتی کارکنان",
        "why": "اگر سامانه شبانه به مشکل بخورد، مدیر باید پیامک بگیرد نه ایمیل.",
        "attributes": {"message": "message"},
        "sample": "هشدار زائرسرای مهدیه: {message}",
    },
    SmsTemplateKey.DRY_RUN: {
        "title": "پیام آزمایشی",
        "why": "آزمون سالم بودن پنل، بدون سوزاندن کد یک‌بارمصرف واقعی.",
        "attributes": {"message": "message"},
        "sample": "آزمون پنل زائرسرای مهدیه: {message}",
    },
}

# قالب‌هایی که نبودشان برای زائر «اختلال» است، نه «کاهش کیفیت»؛ سیستم‌چک و
# پایش، این فهرست را جدی می‌گیرند.
CRITICAL_TEMPLATES: Final[frozenset[str]] = frozenset(
    {
        SmsTemplateKey.OTP_LOGIN,
        SmsTemplateKey.BOOKING_CONFIRMED,
        SmsTemplateKey.OFFER_CREATED,
    }
)


def parse_overrides(raw: Any) -> dict[str, str]:
    """تفسیر بازنویسی کد الگوها از متغیر محیطی (JSON) — خرابی، خطای روشن.

    چرا سخت‌گیر؟ چون پیکربندی شکستهٔ پیامک، خودش را در قالب «ارسال نشد» نشان
    می‌دهد و پیدا کردنش ساعت‌ها وقت می‌برد. در زمان راه‌اندازی، پیام صریح بهتر
    از سکوت است: «SMS_PATTERN_CODES باید JSON باشد».
    """
    if raw in (None, "", b""):
        return {}
    if isinstance(raw, Mapping):
        return {str(key): str(value or "") for key, value in raw.items()}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        msg = (
            'SMS_PATTERN_CODES باید JSON باشد؛ نمونه: {"otp_login": "AbC123", '
            '"booking_confirmed": "XyZ789"}'
        )
        raise ImproperlyConfigured(msg) from exc
    if not isinstance(parsed, dict):
        msg = "SMS_PATTERN_CODES باید یک شیء JSON باشد (کلید: کلید قالب، مقدار: کد الگو)."
        raise ImproperlyConfigured(msg)
    return {str(key): str(value or "") for key, value in parsed.items()}


def pattern_codes() -> dict[str, str]:
    """کدهای الگو پس از اعمال بازنویسی محیطی (``SMS_PATTERN_CODES``)."""
    merged = dict(DEFAULT_PATTERN_CODES)
    merged.update(parse_overrides(getattr(settings, "SMS_PATTERN_CODES", None)))
    return merged


def catalog_bindings() -> dict[str, PatternBinding]:
    """نگاشت‌های **مشتق‌شده از کاتالوگ** برای الگوهایی که کدشان نرسیده است.

    چرا؟ تا «فعال‌سازی» یک کار داده‌ای باشد نه برنامه‌نویسی: کاتالوگ
    (``MISSING_TEMPLATES`` و ``INTERNAL_TEMPLATES``) از پیش می‌داند هر الگو
    چه متغیرهایی می‌خواهد؛ پس به‌محض رسیدن کد از پنل، همان کد را در
    ``SMS_PATTERN_CODES`` می‌گذاریم و قالب **بی‌هیچ تغییر کدی** فعال می‌شود.
    تست ``tests/test_sms_patterns.py`` همین را اثبات می‌کند.

    الگوهای بدون کد، ``code=""`` می‌گیرند و ``is_bound`` آن‌ها را نامعتبر
    می‌داند؛ یعنی پیش از رسیدن کد هم چیزی به شبکه نمی‌رود.
    """
    codes = pattern_codes()
    bindings: dict[str, PatternBinding] = {}
    for key, spec in {**MISSING_TEMPLATES, **INTERNAL_TEMPLATES}.items():
        attributes = dict(spec["attributes"])
        bindings[key] = PatternBinding(
            key=key,
            code=codes.get(key, ""),
            title=str(spec["title"]),
            attributes=attributes,
            required=frozenset(attributes),
            note="از کاتالوگ الگوهای درخواستی پنل؛ فعال‌سازی تنها با کد الگو.",
        )
    return bindings


def _bindings() -> dict[str, PatternBinding]:
    """نگاشت‌های کارآمد با کدهای جاری (پس از بازنویسی محیطی).

    ترتیب اهمیت: نگاشت‌های **صریح** ``PATTERNS`` (با یادداشت و پلهٔ افت) بر
    نگاشت‌های مشتق‌شده از کاتالوگ اولویت دارند.
    """
    codes = pattern_codes()
    merged = catalog_bindings()
    merged.update(
        {
            key: PatternBinding(
                key=binding.key,
                code=codes.get(key, binding.code),
                title=binding.title,
                attributes=binding.attributes,
                required=binding.required,
                fallback_key=binding.fallback_key,
                note=binding.note,
            )
            for key, binding in PATTERNS.items()
        }
    )
    return merged


def all_bindings() -> dict[str, PatternBinding]:
    """همهٔ نگاشت‌ها با کدهای جاری — کاتالوگ + الگوهای ثبت‌شده.

    برای گزارش‌گیری، پایش و آزمون «آمادگی فراخوان‌ها» به‌کار می‌رود: هر کس
    بخواهد بداند یک قالب چه متغیرهایی لازم دارد، تنها همین تابع را می‌پرسد.
    """
    return _bindings()


def is_bound(binding: PatternBinding) -> bool:
    """آیا این نگاشت، کد الگویی دارد که واقعاً در پنل ثبت شده است؟"""
    return bool(binding.code.strip())


def resolve_binding(template_key: str) -> tuple[str, PatternBinding] | None:
    """نگاشت کارآمد برای یک قالب — با یک پله افت به قالب جانشین.

    خروجی ``(کلید قالبی که واقعاً ارسال می‌شود, نگاشت)`` است؛ ``None`` یعنی
    «این قالب راهی به پنل ندارد» و فراخوان باید ارسال را مسدود کند.
    """
    bindings = _bindings()
    binding = bindings.get(template_key)
    if binding is not None and is_bound(binding):
        return template_key, binding
    # پلهٔ افت: پیام جانشینِ معنادار (مثلاً «تأیید رزرو» به‌جای «تغییر تاریخ»).
    if binding is not None and binding.fallback_key:
        fallback = bindings.get(binding.fallback_key)
        if fallback is not None and is_bound(fallback):
            logger.info(
                "sms_pattern_fallback",
                extra={"template_key": template_key, "fallback": binding.fallback_key},
            )
            return binding.fallback_key, fallback
    return None


def digit_mode() -> str:
    """قالب ارقام در ``attributes``: ``persian`` (پیش‌فرض) یا ``latin``."""
    mode = str(getattr(settings, "SMS_ATTRIBUTE_DIGITS", PERSIAN_DIGIT_MODE) or "").strip().lower()
    if mode not in DIGIT_MODES:
        logger.warning("sms_attribute_digits_unknown", extra={"mode": mode})
        return PERSIAN_DIGIT_MODE
    return mode


def _render_value(value: Any) -> str:
    """تبدیل یک مقدار به رشتهٔ امن برای ``attribute`` پنل.

    ارقام بر پایهٔ تنظیمات به فارسی یا لاتین درمی‌آیند؛ سطر تازه و پرانتزهای
    کنترلی حذف می‌شوند تا متن پیام در پنل به‌هم نریزد.
    """
    text = " ".join(str(value).split())
    if digit_mode() != PERSIAN_DIGIT_MODE:
        return text
    rendered = to_persian_digits(text)
    # ممیز فقط در مقادیر عددی («۲٫۵ روز») فارسی می‌شود؛ نقطه در نشانی و متن
    # باید دست‌نخورده بماند، وگرنه لینک امضاشدهٔ پیشنهاد سانس خراب می‌شود.
    if NUMERIC_PATTERN.match(text):
        rendered = rendered.replace(".", PERSIAN_DECIMAL_SEPARATOR)
    return rendered


def build_attributes(
    binding: PatternBinding, context: Mapping[str, Any]
) -> tuple[dict[str, str], tuple[str, ...]]:
    """ساخت ``attributes`` پنل از context مهدیه.

    خروجی دوم، فهرست متغیرهای الزامیِ غایب است؛ اگر خالی نبود، فراخوان باید
    ارسال را مسدود کند (قاعدهٔ ۱ در ابتدای فایل).
    """
    attributes: dict[str, str] = {}
    missing: list[str] = []
    for name, provider_name in binding.attributes.items():
        raw = context.get(name)
        if raw is None or str(raw).strip() == "":
            if name in binding.required:
                missing.append(name)
            continue
        attributes[provider_name] = _render_value(raw)
    return attributes, tuple(missing)


def pattern_readiness() -> dict[str, Any]:
    """گزارش آمادگی الگوها — برای مدیریت، پایش و گزارش عملیاتی.

    خروجی: شمار قالب‌های آماده/غایب، فهرست کلیدهای آماده و فهرست قالب‌های
    بی‌الگو (همان چیزی که باید از پنل درخواست شود).
    """
    bound: list[str] = []
    unbound: list[str] = []
    for key in SmsTemplateKey.values:
        if resolve_binding(key) is None:
            unbound.append(key)
        else:
            bound.append(key)
    return {
        "bound": sorted(bound),
        "unbound": sorted(unbound),
        "guest_unbound": sorted(key for key in unbound if key in MISSING_TEMPLATES),
        "internal_unbound": sorted(key for key in unbound if key in INTERNAL_TEMPLATES),
        "critical_unbound": sorted(key for key in unbound if key in CRITICAL_TEMPLATES),
        "missing_catalog": sorted(MISSING_TEMPLATES),
        "internal_catalog": sorted(INTERNAL_TEMPLATES),
        "codes": pattern_codes(),
        "digit_mode": digit_mode(),
    }


def site_url() -> str:
    """دامنهٔ سایت برای نامهٔ درخواست — از تنظیمات، بدون حدس.

    پنل ایران‌پیامک الگو را تنها پس از بالا آمدن دامنه تأیید می‌کند؛ پس نامه
    باید **همان دامنهٔ واقعی** را نشان بدهد. اگر تنظیم نشده باشد، جای‌نگهدار
    می‌آید تا کسی نامهٔ ناقص نفستد.
    """
    raw = str(getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    return raw or "⟨دامنهٔ سایت — MAHDIEH_SITE_URL⟩"


def request_text() -> str:
    """نامهٔ آمادهٔ ارسال به پشتیبانی پنل: **متن کامل هر الگو**.

    سه چیز در آن است: لحن مرجع (متنی که کارفرما تأیید کرد، تا نویسندهٔ پنل لحن
    را از آن بگیرد)، متن خط‌به‌خط همهٔ الگوهای درخواستی با متغیرهایشان، و نمونهٔ
    پیامی که زائر می‌گیرد. آمادهٔ کپی‌کردن در پیام‌رسان است.
    """
    from apps.notifications import pattern_texts

    lines = [
        "سلام و وقت بخیر،",
        "برای سامانهٔ زائرسرای امام زمان (عج) به ثبت الگوهای پیامک نیاز داریم.",
        "پیش‌تر چند الگو ثبت شد و بازگشت داده شد؛ پاسخ این بود که الگوها تنها",
        f"پس از بالا آمدن دامنهٔ سایت قابل تأییدند. سایت روی دامنهٔ {site_url()}",
        "بالا آمده است و مدارک لازم تقدیم می‌شود.",
        "لطفاً برای هر مورد یک الگو بسازید و «کد الگو» را برای ما بفرستید.",
        "",
        "قواعد مشترک همهٔ متن‌ها:",
        f"• هر متن با پیش‌شمارهٔ «{pattern_texts.SLOGAN}» آغاز می‌شود.",
        "• متغیرها با حروف لاتین کوچک داخل {} نوشته می‌شوند (مثل name و rooz).",
        "• نشانی و راه تماس، متنِ ثابت الگو است؛ ما آن‌ها را متغیر نمی‌فرستیم.",
        "• هر پیام چندخطی و خوانا باشد؛ زائر آن را روی موبایل می‌خواند.",
        "",
        "── لحن مرجع (متنی که تأیید کرده‌اید) ──",
        "",
        pattern_texts.REFERENCE_BODY,
        "",
    ]
    for index, draft in enumerate(pattern_texts.drafts(status=pattern_texts.REQUEST), start=1):
        attrs = " · ".join(f"{{{name}}} = {meaning}" for name, meaning in draft.variables)
        lines += [
            f"── {to_persian_digits(index)}) «{draft.title}» — قالب مهدیه: {draft.key} ──",
            "",
            "متن الگو:",
            "",
            draft.body,
            "",
            f"متغیرها: {attrs}",
            "",
            "نمونهٔ پیامی که زائر می‌گیرد:",
            "",
            draft.rendered_sample(),
            "",
            f"چرا لازم است: {draft.why}",
            "",
        ]
    internals = pattern_texts.drafts(status=pattern_texts.INTERNAL)
    if internals:
        lines += [
            "── اختیاری ولی توصیه‌شده: پیام‌های درونی (کارکنان/آزمون) ──",
            "   (اگر ساختشان آسان است، این دو، پایش و آزمون ما را کامل می‌کند.)",
            "",
        ]
        for draft in internals:
            lines += [f"• «{draft.title}» — قالب مهدیه: {draft.key}", "", draft.body, ""]
    bindings = _bindings()
    covered = {draft.key for draft in pattern_texts.DRAFTS}
    pending = [
        key for key, binding in bindings.items() if not is_bound(binding) and key not in covered
    ]
    if pending:
        lines.append("همچنین کد الگویی که ساخته‌اید ولی کدش را نگرفته‌ایم:")
        for key in pending:
            sketch = pattern_texts.draft_for(key)
            title = sketch.title if sketch is not None else bindings[key].title
            lines.append(f"  • {title} — قالب مهدیه: {key}")
    return "\n".join(lines).rstrip()
