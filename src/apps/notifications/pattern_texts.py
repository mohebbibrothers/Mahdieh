"""متن رسمی الگوهای پیامک — همان چیزی که برای پنل ایران‌پیامک می‌فرستیم.

چرا این ماژول جداست؟
    ``patterns.py`` می‌گوید «کدام پیام به کدام کد الگو می‌رود» (اتصال فنی).
    این ماژول می‌گوید «متن رسمی هر الگو چه است» (قرارداد محتوایی). جدا نگه
    داشتنشان یک دلیل عملی دارد: متن الگو را **مالک پنل** تأیید می‌کند و ممکن
    است بارها ویرایش شود، ولی نگاشت کد/متغیر از کد ما نمی‌جنبد؛ اگر هر دو در
    یک فایل باشند، هر ویرایش متن، یک تغییر کد به نظر می‌رسد.

قواعدی که خودکار آزموده می‌شوند (``tests/test_sms_pattern_readiness.py``):
    ۱. هر متن با «تموم زندگیم مال حسینه...» آغاز می‌شود (پیش‌شمارهٔ رسمی).
    ۲. هر متغیر داخل ``{}`` حروف لاتین کوچک است و **دقیقاً** با فهرست
       ``variables`` همان الگو می‌خواند — نه کم، نه زیاد.
    ۳. هیچ متن آزادی از کاربر در پیام نمی‌رود؛ فقط مقدار متغیرهای اعلام‌شده.
    ۴. ارقام متن‌های ثابت فارسی‌اند (ADR-0014)؛ مقدار متغیرها را پنل با
       ``number_format=persian`` فارسی می‌کند.
    ۵. طول متن از سقف پنل کمتر است.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Final

from apps.notifications.choices import SmsTemplateKey

#: پیش‌شمارهٔ رسمی همهٔ پیام‌های زائرسرا (روی همهٔ الگوهای تأییدشده هست).
SLOGAN: Final[str] = "تموم زندگیم مال حسینه..."

#: نشانی و هماهنگی — داخل **متن الگو** می‌نشیند، نه در پایگاه‌داده؛ چون متن
#: رسمی را پنل نگه می‌دارد و ما فقط متغیر می‌فرستیم.
ADDRESS_LINE: Final[str] = "نشانی: مشهد، نوغان، کوچه ۱۱ شهید قائمی، درب سوم"
CONTACT_LINE: Final[str] = "جهت هماهنگی و دریافت کلید، با مهندس خائف تماس بگیرید."
SITE_LINE: Final[str] = "سامانه: mahdie-shohada.ir"

#: سقف محافظه‌کارانهٔ طول متن الگو در پنل‌های ایرانی.
MAX_BODY_LENGTH: Final[int] = 400

#: لحن مرجع: متن **رسمی و تأییدشدهٔ** الگوی «تأیید رزرو» در پنل. در نامهٔ
#: درخواست می‌آید تا نویسندهٔ پنل، لحن و ساختار را از همین بگیرد؛ «هم‌زبان‌بودن»
#: همهٔ پیام‌ها با یک الگو، چیزی است که زائر حس می‌کند.
REFERENCE_BODY: Final[str] = (
    f"{SLOGAN}\n"
    "زائر عزیز name\n"
    "rooz روز اقامت رایگان شما در زائرسرای امام زمان عج ثبت شد.\n"
    f"{ADDRESS_LINE}\n"
    f"{CONTACT_LINE}"
)

REQUEST: Final[str] = "request"
UPGRADE: Final[str] = "upgrade"


@dataclass(frozen=True, slots=True)
class PatternDraft:
    """پیش‌نویس یک الگوی پیامک: متن رسمی + متغیرها + دلیل نیاز."""

    key: str
    title: str
    audience: str  # «زائر» یا «کارکنان»
    importance: str
    why: str
    variables: tuple[tuple[str, str], ...]
    body: str
    sample_values: Mapping[str, str]
    status: str = REQUEST

    @property
    def placeholders(self) -> tuple[str, ...]:
        """نام متغیرهای داخل متن، به ترتیب ظهور."""
        found: list[str] = []
        index = 0
        while True:
            start = self.body.find("{", index)
            if start < 0:
                return tuple(found)
            end = self.body.find("}", start)
            if end < 0:
                return tuple(found)
            found.append(self.body[start + 1 : end])
            index = end + 1

    def rendered_sample(self) -> str:
        """همان متن با مقدار نمونه — چیزی که زائر واقعاً می‌خواند."""
        text = self.body
        for name, value in self.sample_values.items():
            text = text.replace("{" + name + "}", value)
        return text


DRAFTS: Final[tuple[PatternDraft, ...]] = (
    PatternDraft(
        key=SmsTemplateKey.OFFER_CREATED,
        title="پیشنهاد سانس آزادشده",
        audience="زائر",
        importance="بحرانی — ستون فقرات صف انتظار",
        why="بدون این الگو، صف انتظار فقط یک فهرست بی‌صدا است: جا آزاد می‌شود و هیچ‌کس خبردار نمی‌شود.",
        variables=(
            ("name", "نام و نام خانوادگی زائر"),
            ("unit", "نام واحد اقامتی"),
            ("window", "بازهٔ تاریخ نیم‌روزها (متن خوانا)"),
            ("link", "نشانی پذیرش با یک ضربه (اعتبار ۳۰ دقیقه)"),
        ),
        body=(
            f"{SLOGAN}\n"
            "زائر عزیز {name}\n"
            "سانسی که منتظرش بودید آزاد شد: {unit}\n"
            "بازه: {window}\n"
            "تا ۳۰ دقیقه فرصت دارید؛ پذیرش با یک ضربه:\n"
            "{link}\n"
            "اگر نمی‌خواهید، همین پیام را نادیده بگیرید."
        ),
        sample_values={
            "name": "زائر گرامی",
            "unit": "سوئیت طبقهٔ اول",
            "window": "۵ تا ۷ آبان",
            "link": "mahdie-shohada.ir/o/abc123",
        },
    ),
    PatternDraft(
        key=SmsTemplateKey.BOOKING_REMINDER,
        title="یادآور پیش از پذیرش",
        audience="زائر",
        importance="بالا",
        why="«عدم مراجعه» گران‌ترین رخداد یک زائرسرای رایگان است: تخت رزروشده و خالی می‌ماند و سانس به کسی که تشنهٔ آن بود نمی‌رسد.",
        variables=(
            ("name", "نام و نام خانوادگی زائر"),
            ("unit", "نام واحد اقامتی"),
            ("date", "روز پذیرش (تاریخ شمسی)"),
        ),
        body=(
            f"{SLOGAN}\n"
            "زائر عزیز {name}\n"
            "یادآوری: اقامت رایگان شما در {unit}، {date} آغاز می‌شود.\n"
            f"{ADDRESS_LINE}\n"
            f"{CONTACT_LINE}"
        ),
        sample_values={
            "name": "زائر گرامی",
            "unit": "اتاق بزرگ طبقهٔ دوم",
            "date": "پنجشنبه ۷ آبان",
        },
    ),
    PatternDraft(
        key=SmsTemplateKey.BOOKING_CANCELLED,
        title="لغو رزرو",
        audience="زائر",
        importance="بالا",
        why="زائر باید مطمئن شود سانسش آزاد شده؛ وگرنه روز پذیرش با چمدان می‌رسد و تلفن پذیرش اشغال می‌شود.",
        variables=(
            ("name", "نام و نام خانوادگی زائر"),
            ("code", "کد رزرو"),
        ),
        body=(
            f"{SLOGAN}\n"
            "زائر عزیز {name}\n"
            "رزرو شما با کد {code} لغو شد و سانس آزاد گردید.\n"
            "اگر خودتان این لغو را نخواسته بودید، با مهندس خائف تماس بگیرید.\n"
            f"{SITE_LINE}"
        ),
        sample_values={"name": "زائر گرامی", "code": "MH-4K2P9"},
    ),
    PatternDraft(
        key=SmsTemplateKey.OFFER_ACCEPTED,
        title="تأیید پذیرش پیشنهاد",
        audience="زائر",
        importance="متوسط",
        why="بستن حلقهٔ صف انتظار: زائر باید بداند پیشنهادِ زمان‌دار را برده و اقامتش ثبت شده است.",
        variables=(
            ("name", "نام و نام خانوادگی زائر"),
            ("unit", "نام واحد اقامتی"),
            ("code", "کد رزرو"),
        ),
        body=(
            f"{SLOGAN}\n"
            "زائر عزیز {name}\n"
            "پیشنهاد را پذیرفتید و اقامت رایگان شما در {unit} ثبت شد.\n"
            "کد رزرو: {code}\n"
            f"{ADDRESS_LINE}\n"
            f"{CONTACT_LINE}"
        ),
        sample_values={
            "name": "زائر گرامی",
            "unit": "سوئیت طبقهٔ دوم",
            "code": "MH-7Q1LZ",
        },
    ),
    PatternDraft(
        key=SmsTemplateKey.WAITLIST_JOINED,
        title="ثبت درخواست انتظار",
        audience="زائر",
        importance="متوسط",
        why="زائر باید نوبت خود را بداند؛ وگرنه چند بار ثبت می‌کند و صف را شلوغ می‌کند.",
        variables=(
            ("name", "نام و نام خانوادگی زائر"),
            ("unit", "نام واحد اقامتی یا «هر واحد آزاد»"),
            ("window", "بازهٔ تاریخ نیم‌روزها (متن خوانا)"),
            ("position", "نوبت زائر در صف (عدد فارسی)"),
        ),
        body=(
            f"{SLOGAN}\n"
            "زائر عزیز {name}\n"
            "درخواست انتظار شما برای {unit} در بازهٔ {window} ثبت شد.\n"
            "نوبت شما: {position}\n"
            "به‌محض آزادشدن سانس، اولین نفر باخبر می‌شوید.\n"
            f"{SITE_LINE}"
        ),
        sample_values={
            "name": "زائر گرامی",
            "unit": "هر واحد آزاد",
            "window": "۵ تا ۷ آبان",
            "position": "۲",
        },
    ),
    PatternDraft(
        key=SmsTemplateKey.WAITLIST_REQUEUED,
        title="پایان فرصت پیشنهادها",
        audience="زائر",
        importance="متوسط",
        why="حلقهٔ انتظار باید بسته شود: زائر باید بداند چرا دیگر پیامی نمی‌آید تا دوباره تلاش کند یا منتظر بماند.",
        variables=(
            ("name", "نام و نام خانوادگی زائر"),
            ("window", "بازهٔ تاریخ نیم‌روزها (متن خوانا)"),
            ("offers", "شمار پیشنهادهایی که بی‌پاسخ ماند (عدد فارسی)"),
        ),
        body=(
            f"{SLOGAN}\n"
            "زائر عزیز {name}\n"
            "برای بازهٔ {window} پس از {offers} پیشنهاد، سانس مناسبی آزاد نشد و درخواست انتظار شما بسته شد.\n"
            "هر وقت خواستید می‌توانید دوباره ثبت کنید.\n"
            f"{SITE_LINE}"
        ),
        sample_values={"name": "زائر گرامی", "window": "۵ تا ۷ آبان", "offers": "۳"},
    ),
    PatternDraft(
        key=SmsTemplateKey.FEEDBACK_REQUEST,
        title="نظرسنجی پس از اقامت",
        audience="زائر",
        importance="متوسط",
        why="بدون پیامک، نظرسنجی نه دیده می‌شود و نه پر؛ و بدون نظر زائر، بهبود سلیقه‌ای می‌شود.",
        variables=(
            ("name", "نام و نام خانوادگی زائر"),
            ("unit", "نام واحد اقامتی"),
            ("link", "نشانی کوتاه فرم نظرسنجی"),
        ),
        body=(
            f"{SLOGAN}\n"
            "زائر عزیز {name}\n"
            "امیدواریم اقامت شما در {unit} خوش گذشته باشد.\n"
            "نظر شما مسیر بهترشدن زائرسرای امام زمان عج را می‌سازد؛ پاسخ چند ثانیه‌ای:\n"
            "{link}"
        ),
        sample_values={
            "name": "زائر گرامی",
            "unit": "اتاق بزرگ طبقهٔ سوم",
            "link": "mahdie-shohada.ir/f/b921",
        },
    ),
    PatternDraft(
        key=SmsTemplateKey.STAFF_ALERT,
        title="هشدار عملیاتی کارکنان",
        audience="کارکنان",
        importance="اختیاری (پایش)",
        why="اگر سامانه نیمه‌شب به مشکل بخورد، مدیر باید پیامک بگیرد — نه ایمیلی که کسی نمی‌بیند.",
        variables=(("message", "متن هشدار (کوتاه و بی‌دادهٔ حساس)"),),
        body=(f"{SLOGAN}\nهشدار سامانهٔ زائرسرای امام زمان عج:\n{{message}}"),
        sample_values={"message": "پایگاه‌داده ۳ بار پیاپی پاسخ نداد."},
    ),
    PatternDraft(
        key=SmsTemplateKey.DRY_RUN,
        title="پیام آزمایشی",
        audience="کارکنان",
        importance="اختیاری (آزمون)",
        why="سالم‌بودن پنل و مسیر ارسال را بی‌آنکه کد یک‌بارمصرف واقعی سوزانده شود، اثبات می‌کند.",
        variables=(("message", "متن آزمایشی"),),
        body=(f"{SLOGAN}\nپیام آزمایشی سامانهٔ زائرسرای امام زمان عج:\n{{message}}"),
        sample_values={"message": "آزمون دوره‌ای پنل پیامک."},
    ),
    PatternDraft(
        key=SmsTemplateKey.OTP_LOGIN,
        title="کد ورود به سامانه",
        audience="زائر",
        importance="اختیاری (به‌جای الگوی فعلی)",
        why="الگوی فعلی متنش «کد بازیابی رمز عبور» است؛ کار می‌کند، ولی با کاربرد «ورود» هم‌زبان نیست.",
        variables=(("code", "کد یک‌بارمصرف ورود"),),
        body=(
            f"{SLOGAN}\n"
            "کد ورود شما به زائرسرای امام زمان عج:\n"
            "{code}\n"
            "این کد کوتاه‌عمر است؛ آن را در اختیار دیگران نگذارید.\n"
            f"{SITE_LINE}"
        ),
        sample_values={"code": "۴۸۱۹۰۲"},
        status=UPGRADE,
    ),
    PatternDraft(
        key=SmsTemplateKey.WELCOME,
        title="خوش‌آمد (تکمیل پروفایل)",
        audience="زائر",
        importance="اختیاری (به‌جای الگوی فعلی)",
        why="الگوی فعلی فقط نام را می‌گیرد؛ این نسخه همان پیام را با لحن و راهنمای گام بعد می‌دهد.",
        variables=(("name", "نام و نام خانوادگی زائر"),),
        body=(
            f"{SLOGAN}\n"
            "زائر عزیز {name}\n"
            "ثبت‌نام شما در زائرسرای امام زمان عج کامل شد.\n"
            "از این پس می‌توانید نیم‌روزهای اقامت رایگان را از تقویم سامانه رزرو کنید.\n"
            f"{SITE_LINE}"
        ),
        sample_values={"name": "زائر گرامی"},
        status=UPGRADE,
    ),
)


def drafts(*, status: str | None = None) -> tuple[PatternDraft, ...]:
    """پیش‌نویس‌ها؛ می‌توان بر پایهٔ وضعیت (درخواست/اختیاری) صافی کرد."""
    if status is None:
        return DRAFTS
    return tuple(draft for draft in DRAFTS if draft.status == status)


def draft_for(key: str) -> PatternDraft | None:
    """پیش‌نویس یک قالب مشخص."""
    for draft in DRAFTS:
        if draft.key == key:
            return draft
    return None


def validate() -> list[str]:
    """بازرسی سازگاری متن‌ها — فهرست ایرادها (تهی یعنی سالم).

    این تابع، قلب «قرارداد محتوایی» است: اگر متن الگو متغیری بخواهد که کد ما
    نمی‌فرستد (یا برعکس)، همین‌جا لو می‌رود — پیش از آنکه پیام ناقص به موبایل
    زائر برسد.
    """
    problems: list[str] = []
    from apps.notifications.patterns import INTERNAL_TEMPLATES, MISSING_TEMPLATES, PATTERNS

    # ``PATTERNS`` مقدارش ``PatternBinding`` است (نه Mapping)؛ فقط همین کلیدها
    # را نگه می‌داریم و بقیهٔ دو کاتالوگ که dict اند، سرجای خود می‌مانند.
    catalog: dict[str, Mapping[str, Any]] = {
        **MISSING_TEMPLATES,
        **INTERNAL_TEMPLATES,
    }
    bound_keys = set(PATTERNS)
    for draft in DRAFTS:
        if not draft.body.startswith(SLOGAN):
            problems.append(f"{draft.key}: متن با پیش‌شمارهٔ «{SLOGAN}» آغاز نمی‌شود.")
        declared = [name for name, _ in draft.variables]
        found = list(draft.placeholders)
        if found != declared:
            problems.append(
                f"{draft.key}: متغیرهای متن {found} با فهرست اعلام‌شده {declared} نمی‌خواند."
            )
        for name in found:
            if not name.isascii() or not name.islower() or not name.isidentifier():
                problems.append(f"{draft.key}: نام متغیر «{name}» باید لاتین کوچک باشد.")
        if len(draft.body) > MAX_BODY_LENGTH:
            problems.append(
                f"{draft.key}: طول متن {len(draft.body)} از سقف {MAX_BODY_LENGTH} گذشته است."
            )
        if draft.key not in catalog and draft.key not in bound_keys:
            problems.append(f"{draft.key}: در کاتالوگ الگوها (patterns.py) نیست.")
        if draft.key in catalog:
            spec = catalog.get(draft.key)
            attributes = spec["attributes"] if spec is not None else None
            if isinstance(attributes, Mapping):
                mapped = list(attributes.values())
                if mapped != declared:
                    problems.append(
                        f"{draft.key}: نگاشت پنل {mapped} با متغیرهای متن {declared} نمی‌خواند."
                    )
        missing_samples = [name for name in found if name not in draft.sample_values]
        if missing_samples:
            problems.append(f"{draft.key}: مقدار نمونه برای {missing_samples} نیست.")
    return problems


def preview_text() -> str:
    """پیش‌نمایش «پیامی که زائر می‌گیرد» برای هر الگو — سند انسانی.

    این متن، رندرِ واقعی با مقدار نمونه است؛ برای بازبینی محتوایی پیش از
    ساخت الگو در پنل و برای این‌که کارفرما/کارکنان یک‌جا ببینند زائر چه
    می‌خواند. با ``make sms-patterns ARGS=\"--preview\"`` بازتولید می‌شود.
    """
    lines: list[str] = []
    for index, draft in enumerate(DRAFTS, start=1):
        bar = "─" * 34
        lines += [
            f"{bar}",
            f"{index}) {draft.title} — {draft.key}",
            f"مخاطب: {draft.audience} · اهمیت: {draft.importance}",
            bar,
            "",
            draft.rendered_sample(),
            "",
            f"متغیرها: {' · '.join(f'{{{name}}}' for name, _ in draft.variables)}",
            "",
        ]
    return "\n".join(lines).rstrip()


def all_placeholders() -> Iterator[tuple[str, str]]:
    """جفت‌های (قالب، متغیر) برای بازرسی‌های بیرونی."""
    for draft in DRAFTS:
        for name in draft.placeholders:
            yield draft.key, name
