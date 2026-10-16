"""محتوا و جست‌وجوی «میز راهنما» — پاسخ آمادهٔ پرسش‌های واقعی زائر.

چرا محتوا در کد و نه در پایگاه‌داده؟ چون این متن‌ها **قرارداد محصول‌اند**: هر
جمله یا از یک قاعدهٔ موتور می‌آید (کوتاه‌ترین/بلندترین اقامت، افق رزرو، مهلت
لغو) یا از پیکربندی استقرار (تلفن پذیرش). پس کنار کد می‌مانند تا بازبینی شوند،
ولی متن‌شان از **سیاست زنده** پر می‌شود تا «راهنما از موتور نیفتد».

سه قاعده، هر سه آزموده‌شده در ``tests/test_help_center.py``:
    ۱. هر متغیر جای‌نگهدار متن، در ``policy_values`` وجود دارد (هیچ «{…}»
       ناپریده به چشم زائر نمی‌رود).
    ۲. هیچ عددی در متن سخت‌کد نیست؛ عوض‌کردن سیاست، متن راهنما را هم عوض می‌کند.
    ۳. جست‌وجو با شکل‌های غلط رایج («ك»، «ي»، نیم‌فاصله، ارقام فارسی) هم کار
       می‌کند — چون از ``apps.core.text.normalize_fa`` می‌گذرد.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.core.jalali import format_jalali
from apps.core.text import normalize_fa, search_tokens, to_persian_digits
from apps.inventory.lattice import stay_label
from apps.reservation import policy


@dataclass(frozen=True, slots=True)
class HelpAction:
    """دکمهٔ پایان یک پاسخ — کاربر باید از «دانستن» به «انجام دادن» برسد."""

    label: str
    url_name: str


@dataclass(frozen=True, slots=True)
class HelpItem:
    """یک پرسش راهنما با پاسخ، گام‌ها، برچسب جست‌وجو و دکمهٔ اقدام."""

    slug: str
    question: str
    answer: str
    steps: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    action: HelpAction | None = None
    related: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class HelpCategory:
    """یک دستهٔ راهنما — با نماد، خلاصه و پرسش‌هایش."""

    slug: str
    title: str
    icon: str
    summary: str
    items: tuple[HelpItem, ...]


@dataclass(frozen=True, slots=True)
class HelpMatch:
    """یک نتیجهٔ جست‌وجو: پرسش، دسته‌اش، امتیاز و برش متن پاسخ."""

    item: HelpItem
    category: HelpCategory
    score: int
    snippet: str


# ─── گام‌های «رزرو در چهار حرکت» (نوار بالای راهنما) ────────────────────────
QUICK_STEPS: Final[tuple[dict[str, str], ...]] = (
    {
        "icon": "📱",
        "title": "۱. شمارهٔ تلفن",
        "text": "با شمارهٔ همراه خود وارد شوید؛ کد یک‌بارمصرف پیامک می‌شود. ایمیل لازم نیست.",
        "url_name": "web:login",
    },
    {
        "icon": "🧭",
        "title": "۲. سانس را انتخاب کنید",
        "text": "روی تقویم، واحد و نیم‌روزهای آزاد را ببینید و بازهٔ دلخواه را برگزینید.",
        "url_name": "web:calendar",
    },
    {
        "icon": "✅",
        "title": "۳. رزرو را ثبت کنید",
        "text": "تعداد نفرات را بگویید و ثبت کنید؛ کد پیگیری همان لحظه ساخته می‌شود.",
        "url_name": "web:units",
    },
    {
        "icon": "🗝️",
        "title": "۴. در پذیرش",
        "text": "کد پیگیری و کارت رزرو را نشان دهید؛ کلید در همان لحظه تحویل می‌شود.",
        "url_name": "web:my_reservations",
    },
)

# اندازهٔ برش متن در نتایج جست‌وجو و وزن زمینه‌ها (پرسش مهم‌تر از پاسخ است).
SNIPPET_LENGTH: Final[int] = 160
SNIPPET_LEAD: Final[int] = 40
SNIPPET_WINDOW: Final[int] = 150
SEARCH_WEIGHTS: Final[dict[str, int]] = {
    "question": 5,
    "tags": 3,
    "category": 2,
    "answer": 2,
    "steps": 1,
}


def contact() -> dict[str, str]:
    """اطلاعات تماس پذیرش — هرچه در استقرار تنظیم شده باشد، بدون حدس.

    اگر شماره یا نشانی تنظیم نشده باشد، رشتهٔ خالی برمی‌گردد و قالب گزینهٔ
    جانشین نشان می‌دهد؛ هرگز شمارهٔ ساختگی چاپ نمی‌شود.
    """
    phone = str(getattr(settings, "SUPPORT_PHONE", "") or "").strip()
    address = str(getattr(settings, "SUPPORT_ADDRESS", "") or "").strip()
    return {
        "phone": phone,
        "phone_display": to_persian_digits(phone) if phone else "",
        "address": address,
    }


def _hour_label(hour: int) -> str:
    """برچسب ساعت به شکل فارسی «۱۴:۰۰» (ساعت‌های سیاست، نه زمان اکنون)."""
    return to_persian_digits(f"{hour:02d}:00")


def policy_values(read: Callable[[str], Any] | None = None) -> dict[str, str]:
    """مقادیر زندهٔ سیاست و استقرار، برای پُرشدن متن راهنما.

    این تابع تنها پلِ راهنما به موتور است: هیچ عددی در متن فارسی سخت‌کد
    نشده و همهٔ جای‌نگهدارها از همین‌جا پر می‌شوند.

    ``read`` برای حالت اضطراری است: وقتی پایگاه‌داده در دسترس نیست، قالب خطا
    هم باید رندر شود؛ آن‌وقت همان مقادیر **پیش‌فرض موتور** خوانده می‌شوند تا
    «خطای اصلی» با «خطای دوم در رندر صفحهٔ خطا» بدل به ۵۰۰ نشود.
    """
    read = read or policy.policy_int
    min_cells = read("reservation.min_cells")
    max_cells = read("reservation.max_cells")
    cell_hours = read("lattice.cell_hours")
    cells_per_day = max(1, 24 // max(1, cell_hours))
    quota_cells = read("reservation.max_cells_per_window")
    quota_days = read("reservation.cells_window_days")
    annual = read("reservation.max_cells_per_year")
    active = read("reservation.max_active_per_user")
    cancel_hours = read("reservation.cancel_free_before_hours")
    cooldown = read("reservation.late_cancel_cooldown_hours")
    gap = read("reservation.min_gap_between_bookings_hours")
    lead = read("reservation.min_lead_time_hours")
    horizon = read("reservation.booking_horizon_days")
    checkin = read("lattice.checkin_cutoff")
    checkout = read("lattice.checkout_cutoff")
    grid_start = read("lattice.grid_start_local")
    buffer_minutes = read("lattice.default_cleaning_buffer_minutes")
    quiet_end = read("notification.quiet_hours_end")
    offer_minutes = read("waitlist.offer_window_minutes")
    hold_minutes = read("reservation.hold_ttl_minutes")
    return {
        "min_stay": stay_label(min_cells),
        "max_stay": stay_label(max_cells),
        "cells_per_day": to_persian_digits(cells_per_day),
        "cell_hours": to_persian_digits(cell_hours),
        "day_start": _hour_label(grid_start),
        "night_start": _hour_label(grid_start + cell_hours),
        "checkin_hour": _hour_label(checkin),
        "checkout_hour": _hour_label(checkout),
        "cancel_hours": to_persian_digits(cancel_hours),
        "cooldown_hours": to_persian_digits(cooldown),
        "gap_hours": to_persian_digits(gap),
        "lead_hours": to_persian_digits(lead),
        "horizon_days": to_persian_digits(horizon),
        "horizon_months": to_persian_digits(round(horizon / 30)),
        "quota_cells": to_persian_digits(quota_cells),
        "quota_days": to_persian_digits(quota_days),
        "quota_nights": to_persian_digits(quota_cells // cells_per_day),
        "annual_cells": to_persian_digits(annual),
        "max_active": to_persian_digits(active),
        "buffer_minutes": to_persian_digits(buffer_minutes),
        "quiet_end": _hour_label(quiet_end),
        "offer_minutes": to_persian_digits(offer_minutes),
        "hold_minutes": to_persian_digits(hold_minutes),
    }


def default_values() -> dict[str, str]:
    """مقادیر **پیش‌فرض موتور** (بدون دیتابیس) — تور نجات قالب در قطعی گذرا."""
    return policy_values(read=lambda key: policy.DEFAULTS[key])


def render(text: str, values: dict[str, str]) -> str:
    """پُرشدن جای‌نگهدارهای یک متن با مقادیر سیاست.

    نبودِ یک کلید، خطا است و نه سکوت: متن راهنما نباید با «{max_stay}» به چشم
    زائر برود (همین را تست ضمانت می‌کند).
    """
    return text.format(**values)


# ─── دسته‌ها و پرسش‌ها ─────────────────────────────────────────────────────
CATEGORIES: Final[tuple[HelpCategory, ...]] = (
    HelpCategory(
        slug="before",
        title="پیش از سفر",
        icon="🧳",
        summary="ثبت‌نام، پروفایل و چیزهایی که باید آماده باشد.",
        items=(
            HelpItem(
                slug="signup",
                question="چطور ثبت‌نام کنم؟ ایمیل ندارم.",
                answer=(
                    "ورود مهدیه فقط با شمارهٔ تلفن همراه است. شماره را وارد می‌کنید، "
                    "کد یک‌بارمصرف پیامک می‌شود و وارد می‌شوید. ایمیل و گذرواژه در "
                    "سامانه وجود ندارد؛ همین، امنیت را ساده‌تر و برای زائر سال‌خورده "
                    "راحت‌تر می‌کند."
                ),
                steps=(
                    "شمارهٔ همراه را با کد شهر (مثل ۰۹۱۲…) وارد کنید.",
                    "کد پیامک‌شده را در همان صفحه بگذارید.",
                    "نام، نام خانوادگی، کد ملی، استان و شهر را کامل کنید — رزرو بدون پروفایل کامل ثبت نمی‌شود.",
                ),
                tags=("ورود", "ثبت نام", "کد پیامک", "ایمیل", "شماره تلفن"),
                action=HelpAction(label="ورود / ثبت‌نام", url_name="web:login"),
                related=("profile", "otp-problem"),
            ),
            HelpItem(
                slug="profile",
                question="چرا کد ملی و شهر را می‌پرسید؟",
                answer=(
                    "پذیرش باید بداند مهمان کیست و از کجا آمده؛ هم برای تحویل کلید، هم "
                    "برای گزارش مدیریتی زائرسرا. کد ملی فقط برای شناسایی در پذیرش "
                    "به‌کار می‌رود و در پیامک‌ها هرگز کامل نمایش داده نمی‌شود."
                ),
                tags=("پروفایل", "کد ملی", "استان", "شهر"),
                action=HelpAction(label="تکمیل پروفایل", url_name="web:profile"),
            ),
            HelpItem(
                slug="otp-problem",
                question="کد ورود پیامک نشد، چه کنم؟",
                answer=(
                    "نخست مطمئن شوید شماره درست است و پیامک‌های تبلیغاتی مسدود نشده‌اند. "
                    "سامانه برای هر درخواست سهمیهٔ زمانی دارد و پس از چند تلاش پشت‌سرهم، "
                    "کوتاه‌مدتی صبر می‌خواهد. اگر باز هم نیامد، از پذیرش بخواهید ورود شما "
                    "را دستی انجام دهد."
                ),
                steps=(
                    "یک دقیقه صبر کنید و دوباره «ارسال کد» را بزنید.",
                    "پوشهٔ پیامک‌های ناشناس و پیامک‌های مسدودشده را ببینید.",
                    "اگر شماره را عوض کرده‌اید، با شمارهٔ تازه وارد شوید.",
                ),
                tags=("کد نیامد", "پیامک", "ورود", "مشکل"),
                action=HelpAction(label="تلاش دوباره", url_name="web:login"),
            ),
            HelpItem(
                slug="what-to-bring",
                question="چه چیزی همراهم باشد؟",
                answer=(
                    "کارت ملی همهٔ همراهان، کد پیگیری رزرو و وسایل شخصی. کلید و "
                    "پتو و لوازم اصلی واحد در زائرسرا هست؛ وسایل خوردنی و بهداشتی "
                    "شخصی را همراه بیاورید. هیچ پرداختی — نه ودیعه و نه اجاره — "
                    "نمی‌گیریم."
                ),
                tags=("همراه", "مدارک", "کارت ملی", "رزرو", "پرداخت"),
                action=HelpAction(label="رزروهای من", url_name="web:my_reservations"),
            ),
        ),
    ),
    HelpCategory(
        slug="booking",
        title="رزرو سانس",
        icon="🗓️",
        summary="نیم‌روز چیست، چند شب می‌توانم بمانم و چطور رزرو کنم.",
        items=(
            HelpItem(
                slug="how-to-book",
                question="چطور سانس رزرو کنم؟",
                answer=(
                    "زائرسرا به «نیم‌روز» تقسیم شده است: سانس روز از {day_start} و "
                    "سانس شب از {night_start} آغاز می‌شود. واحد، تاریخ ورود و تعداد "
                    "نیم‌روز را انتخاب می‌کنید و سامانه همان لحظه سانس را برای شما "
                    "نگه می‌دارد تا رزرو نهایی شود."
                ),
                steps=(
                    "در صفحهٔ واحدها، واحد دلخواه را باز کنید.",
                    "روی تقویم، روز ورود و سانس (روز یا شب) را بزنید.",
                    "تعداد نیم‌روز و تعداد نفرات را مشخص کنید و «ثبت رزرو» را بزنید.",
                    "کد پیگیری را بردارید؛ در پذیرش همان را نشان می‌دهید.",
                ),
                tags=("رزرو", "چطور", "نیم روز", "سانس", "تقویم"),
                action=HelpAction(label="شروع رزرو", url_name="web:units"),
                related=("halfday", "limits"),
            ),
            HelpItem(
                slug="halfday",
                question="«نیم‌روز» یعنی چه؟",
                answer=(
                    "هر شبانه‌روز به دو نیم‌روز {cell_hours} ساعته تقسیم می‌شود: سانس "
                    "روز {day_start} تا {night_start} و سانس شب {night_start} تا "
                    "صبح. کمترین رزرو یک نیم‌روز است، پس می‌توانید فقط برای یک "
                    "شبانه‌روز کامل یا نیمهٔ آن بیایید."
                ),
                tags=("نیم روز", "سانس روز", "سانس شب", "ساعت"),
                action=HelpAction(label="دیدن تقویم", url_name="web:calendar"),
                related=("how-to-book",),
            ),
            HelpItem(
                slug="limits",
                question="چند شب می‌توانم بمانم؟ چند نوبت در سال؟",
                answer=(
                    "کوتاه‌ترین اقامت {min_stay} و بلندترین {max_stay} است. سهمیهٔ "
                    "عادلانه هم داریم: در هر {quota_days} روز دست‌کم {quota_cells} "
                    "نیم‌روز (یعنی حدود {quota_nights} شبانه‌روز) و در سال "
                    "{annual_cells} نیم‌روز. هر زائر {max_active} رزرو فعال هم‌زمان "
                    "می‌تواند داشته باشد."
                ),
                tags=("سهمیه", "چند شب", "محدودیت", "سقف", "سالانه"),
                related=("how-to-book", "gap"),
            ),
            HelpItem(
                slug="horizon",
                question="تا چه تاریخی می‌توانم رزرو کنم؟",
                answer=(
                    "افق رزرو {horizon_days} روز است (حدود {horizon_months} ماه)، "
                    "ولی سانس‌ها باید دست‌کم {lead_hours} ساعت پس از همین لحظه آغاز "
                    "شوند تا پذیرش برای آماده‌سازی فرصت داشته باشد."
                ),
                tags=("افق", "چند روز آینده", "زودترین", "چقدر جلوتر"),
                action=HelpAction(label="تقویم موجودی", url_name="web:calendar"),
                related=("gap",),
            ),
            HelpItem(
                slug="gap",
                question="چرا بعد از رزرو قبلی‌ام اجازهٔ رزرو نمی‌دهد؟",
                answer=(
                    "دو قاعده اینجا کار می‌کند: میان پایان یک اقامت و آغاز اقامت "
                    "بعدی دست‌کم {gap_hours} ساعت فاصله لازم است، و پس از لغو "
                    "دیرهنگام یا عدم‌حضور، {cooldown_hours} ساعت خنک‌شدن لازم است. "
                    "این قواعد برای عادلانه‌ماندن نوبت است، نه برای سخت‌کردن رزرو."
                ),
                tags=("خطا", "اجازه نمی دهد", "فاصله", "خنک شدن"),
                related=("limits",),
            ),
            HelpItem(
                slug="guests",
                question="چند نفر می‌توانم بیاورم؟",
                answer=(
                    "هر واحد ظرفیت اعلام‌شدهٔ خودش را دارد و در صفحهٔ همان واحد "
                    "دیده می‌شود. تعداد نفرات را در فرم رزرو بنویسید؛ پذیرش بر همان "
                    "اساس تخت و لوازم می‌آماده می‌کند."
                ),
                tags=("ظرفیت", "چند نفر", "تخت"),
                action=HelpAction(label="واحدها و ظرفیت‌ها", url_name="web:units"),
            ),
        ),
    ),
    HelpCategory(
        slug="change",
        title="تغییر و لغو",
        icon="🔁",
        summary="لغو، جابه‌جایی و آنچه پس از آن رخ می‌دهد.",
        items=(
            HelpItem(
                slug="cancel",
                question="چطور رزرو را لغو کنم؟",
                answer=(
                    "در «رزروهای من» رزرو را باز کنید و «لغو» را بزنید. تا "
                    "{cancel_hours} ساعت پیش از ورود، لغو رایگان و بی‌اثر است. پس از "
                    "آن، سانس آزاد می‌شود ولی سهمیهٔ شما مدتی خنک می‌شود."
                ),
                steps=(
                    "به «رزروهای من» بروید.",
                    "رزرو فعال را باز کنید و دلیل لغو را کوتاه بنویسید.",
                    "پس از لغو، همان سانس بلافاصله به نوبت‌انتظار پیشنهاد می‌شود.",
                ),
                tags=("لغو", "کنسل", "انصراف"),
                action=HelpAction(label="رزروهای من", url_name="web:my_reservations"),
                related=("late-cancel", "no-show"),
            ),
            HelpItem(
                slug="late-cancel",
                question="دیر لغو کردم، چه اتفاقی می‌افتد؟",
                answer=(
                    "لغو دیرهنگام، تنبیه مالی ندارد؛ فقط {cooldown_hours} ساعت "
                    "خنک‌شدن می‌آورد تا نوبت زائر دیگری که در صف بود بیشتر از دست "
                    "نرود. اگر دلیلتان اضطراری است، در پذیرش توضیح دهید تا وضعیت "
                    "شما بازبینی شود."
                ),
                tags=("لغو دیرهنگام", "خنک شدن", "جریمه"),
                related=("cancel",),
            ),
            HelpItem(
                slug="move",
                question="می‌خواهم تاریخ اقامتم را عوض کنم.",
                answer=(
                    "جابه‌جایی با کارکنان پذیرش انجام می‌شود: سانس تازه انتخاب می‌شود "
                    "و سانس قبلی همان لحظه آزاد می‌گردد. اگر خودتان سانس قبلی را "
                    "لغو کنید و سانس تازه را دستی بگیرید، ممکن است در فاصلهٔ میان این "
                    "دو، سانس تازه را زائر دیگری بردارد."
                ),
                tags=("تغییر", "جابجایی", "عوض کردن تاریخ"),
                related=("cancel",),
            ),
            HelpItem(
                slug="no-show",
                question="اگر نرسم چه می‌شود؟",
                answer=(
                    "اگر بدون اطلاع نرسید، پس از پایان فرصت اقامت، عدم‌حضور ثبت "
                    "می‌شود و سانس آزاد می‌گردد تا زائر دیگری استفاده کند. اگر در راه "
                    "هستید، به پذیرش خبر دهید تا رزرو شما بماند."
                ),
                tags=("نرسیدم", "عدم حضور", "دیر می رسم"),
                related=("cancel",),
            ),
        ),
    ),
    HelpCategory(
        slug="waitlist",
        title="نوبت انتظار",
        icon="⏳",
        summary="وقتی سانس پر است: چطور باخبر می‌شوید و چطور بپذیرید.",
        items=(
            HelpItem(
                slug="join",
                question="سانس‌ها پر بودند؛ چطور باخبر شوم؟",
                answer=(
                    "درخواست انتظار ثبت کنید. سانس که آزاد شود — با لغو یا جابه‌جایی — "
                    "اولین نفر صف پیامک پیشنهاد می‌گیرد و می‌تواند با یک ضربه قبولش "
                    "کند. برای هر بازهٔ زمانی یک درخواست فعال می‌پذیریم."
                ),
                steps=(
                    "در تقویم، بازهٔ دلخواه را انتخاب کنید.",
                    "«ثبت در نوبت انتظار» را بزنید.",
                    "اگر پیامک پیشنهاد آمد، تا {offer_minutes} دقیقه پاسخ دهید.",
                ),
                tags=("نوبت", "انتظار", "صف", "پر است", "پیامک پیشنهاد"),
                action=HelpAction(label="تقویم موجودی", url_name="web:calendar"),
                related=("offer",),
            ),
            HelpItem(
                slug="offer",
                question="پیامک پیشنهاد آمد؛ مهلت چقدر است؟",
                answer=(
                    "پیشنهاد {offer_minutes} دقیقه اعتبار دارد. با زدن لینک پیامک، "
                    "سانس به نام شما ثبت می‌شود. اگر پاسخ ندهید، پیشنهاد به نفر بعدی "
                    "صف می‌رود و شما در همان نوبت باقی می‌مانید."
                ),
                tags=("پیشنهاد", "مهلت", "لینک پیامک", "قبول"),
                action=HelpAction(label="نوبت‌های من", url_name="web:my_waitlist"),
                related=("join",),
            ),
            HelpItem(
                slug="fairness",
                question="صف چطور منصفانه می‌ماند؟",
                answer=(
                    "ترتیب صف بر پایهٔ زمان ثبت درخواست است و «برگشت به صف» تنها پس "
                    "از چند پیشنهاد بی‌پاسخ رخ می‌دهد، نه با هر پیشنهاد. درخواست "
                    "باطل‌شده یا واگذارشده جای کسی را اشغال نمی‌کند."
                ),
                tags=("انصاف", "ترتیب", "نوبت من"),
                related=("join",),
            ),
        ),
    ),
    HelpCategory(
        slug="stay",
        title="در زائرسرا",
        icon="🏠",
        summary="پذیرش، تحویل کلید، ساعت‌ها و گواهی اقامت.",
        items=(
            HelpItem(
                slug="arrival",
                question="وقتی رسیدم چه کار کنم؟",
                answer=(
                    "کد پیگیری رزرو (یا کارت رزرو) را به پذیرش نشان دهید. کارمند "
                    "اقامت را «پذیرش» ثبت می‌کند و کلید تحویل می‌شود. ساعت معمول "
                    "پذیرش از {checkin_hour} و ساعت تخلیه تا {checkout_hour} است؛ "
                    "برای خارج از این ساعت‌ها با پذیرش هماهنگ کنید."
                ),
                steps=(
                    "کد پیگیری را همراه داشته باشید (پیامک یا صفحهٔ رزرو).",
                    "کارت ملی همراهان را برای پذیرش بیاورید.",
                    "پس از تحویل کلید، در صورت خرابی چیزی فوراً به پذیرش بگویید.",
                ),
                tags=("پذیرش", "رسیدم", "کلید", "ورود", "ساعت"),
                action=HelpAction(label="کارت رزرو من", url_name="web:my_reservations"),
                related=("certificate", "units"),
            ),
            HelpItem(
                slug="certificate",
                question="گواهی رزرو برای پذیرش را از کجا بگیرم؟",
                answer=(
                    "در «رزروهای من» هر رزرو یک کارت با کد یکتای QR دارد. همان کارت "
                    "را نشان دهید یا چاپ کنید؛ پذیرش با اسکن آن، رزرو را در همان لحظه "
                    "می‌بیند و خطای دستی از میان می‌رود."
                ),
                tags=("گواهی", "کارت رزرو", "QR", "چاپ"),
                action=HelpAction(label="رزروهای من", url_name="web:my_reservations"),
                related=("arrival",),
            ),
            HelpItem(
                slug="units",
                question="واحدها چه تفاوتی دارند؟",
                answer=(
                    "زائرسرا سه طبقه و پنج واحد دارد: سوئیت و اتاق بزرگ در طبقهٔ اول "
                    "و دوم، و یک اتاق بزرگ در طبقهٔ سوم. ظرفیت، امکانات و عکس هر واحد "
                    "در صفحهٔ واحدها آمده است."
                ),
                tags=("واحد", "اتاق", "سوئیت", "طبقه", "امکانات"),
                action=HelpAction(label="معرفی واحدها", url_name="web:units"),
                related=("guests",),
            ),
            HelpItem(
                slug="cleaning",
                question="فاصلهٔ میان دو زائر چرا لازم است؟",
                answer=(
                    "پس از تخلیه، واحد دست‌کم {buffer_minutes} دقیقه بافر نظافت دارد. "
                    "این بازه در تقویم به‌صورت وضعیت «بافر نظافت» دیده می‌شود، پس شما "
                    "هرگز واحد آماده‌نشده تحویل نمی‌گیرید."
                ),
                tags=("نظافت", "بافر", "آماده سازی"),
                related=("arrival",),
            ),
        ),
    ),
    HelpCategory(
        slug="trouble",
        title="مشکل و پشتیبانی",
        icon="🛠️",
        summary="اگر چیزی درست پیش نرفت: چه کار کنیم و با چه کسی تماس بگیریم.",
        items=(
            HelpItem(
                slug="sms-missing",
                question="پیامک تأیید رزرو برایم نیامد.",
                answer=(
                    "رزرو شما در «رزروهای من» دیده می‌شود؛ پیامک فقط تسهیل‌کننده است و "
                    "نبودش رزرو را باطل نمی‌کند. اگر لازم است پذیرش هم باخبر شود، کد "
                    "پیگیری خود را در پذیرش بگویید."
                ),
                tags=("پیامک نیامد", "تأیید", "کد پیگیری"),
                action=HelpAction(label="رزروهای من", url_name="web:my_reservations"),
                related=("otp-problem", "arrival"),
            ),
            HelpItem(
                slug="wrong-booking",
                question="در رزرو اشتباه کردم (تاریخ یا واحد).",
                answer=(
                    "تا پیش از آغاز اقامت، لغو کنید و سانس درست را بگیرید؛ اگر سانس "
                    "درست پر است، هم‌زمان درخواست انتظار بگذارید. اگر اشتباه را "
                    "کارکنان باید اصلاح کنند، با پذیرش تماس بگیرید."
                ),
                tags=("اشتباه", "تاریخ غلط", "واحد غلط", "اصلاح"),
                action=HelpAction(label="رزروهای من", url_name="web:my_reservations"),
                related=("move", "cancel"),
            ),
            HelpItem(
                slug="contact",
                question="چطور با پذیرش تماس بگیرم؟",
                answer=(
                    "شمارهٔ پذیرش در پایین همین صفحه و در پای صفحهٔ سایت آمده است. اگر "
                    "شماره ثبت نشده باشد، از کارکنان حاضر در زائرسرا بپرسید یا در "
                    "فرم نظرسنجی پس از اقامت، پیام‌تان را بنویسید — همان‌جا خوانده می‌شود."
                ),
                tags=("تماس", "پشتیبانی", "تلفن", "پذیرش"),
                related=("sms-missing",),
            ),
            HelpItem(
                slug="feedback",
                question="چطور نظر یا انتقادم را بگویم؟",
                answer=(
                    "پس از پایان اقامت، پیامک نظرسنجی می‌آید و پاسخ چند ثانیه‌ای است. "
                    "انتقاد دربارهٔ نظافت، امکانات یا پذیرش را همان‌جا بنویسید؛ "
                    "کارکنان هر هفته نظرسنجی‌ها را می‌بینند و برای بهترشدن اقامت "
                    "زائران بعدی تصمیم می‌گیرند."
                ),
                tags=("نظرسنجی", "انتقاد", "پیشنهاد", "شکایت"),
                related=("contact",),
            ),
        ),
    ),
)

# سند «گام‌های نخست» برای میز پذیرش — روی دیوار پذیرش چاپ می‌شود.
STAFF_CHECKLIST: Final[tuple[dict[str, str], ...]] = (
    {
        "title": "گام ۱ — آغاز روز",
        "text": "تابلوی پذیرش را باز کنید: ورودی‌های امروز، اقامت‌های جاری و سانس‌های آزاد را ببینید.",
    },
    {
        "title": "گام ۲ — پذیرش مهمان",
        "text": "کد پیگیری یا کارت QR را بگیرید، «پذیرش» را ثبت کنید و کلید را تحویل دهید.",
    },
    {
        "title": "گام ۳ — تغییر و لغو",
        "text": "جابه‌جایی یا لغو را از همان صفحهٔ رزرو انجام دهید؛ سانس آزادشده خودکار به نوبت‌انتظار پیشنهاد می‌شود.",
    },
    {
        "title": "گام ۴ — پایان روز",
        "text": "اقامت‌های پایان‌یافته را «تخلیه» کنید و گزارش اکسل روز را برای مدیریت بفرستید.",
    },
    {
        "title": "گام ۵ — اگر سامانه خطا داد",
        "text": "کلید درخواست (پای صفحه) را یادداشت کنید و به پشتیبانی فنی بگویید؛ با همان کلید، خطا در چند ثانیه پیدا می‌شود.",
    },
)

SITE_HELP_NOTE: Final[str] = (
    "این راهنما با قواعد زندهٔ سامانه ساخته می‌شود؛ اگر سهمیه یا ساعت‌ها تغییر کند، "
    "اینجا هم به‌روز می‌شود."
)


def _pairs() -> list[tuple[HelpCategory, HelpItem]]:
    """همهٔ (دسته، پرسش) به‌ترتیب — پایهٔ جست‌وجو و شمارش."""
    return [(category, item) for category in CATEGORIES for item in category.items]


def all_items() -> tuple[HelpItem, ...]:
    """همهٔ پرسش‌ها، به‌ترتیب دسته‌ها."""
    return tuple(item for _, item in _pairs())


def category_by_slug(slug: str) -> HelpCategory | None:
    """یافتن یک دسته با نشانهٔ آدرس؛ ``None`` اگر نبود."""
    return next((category for category in CATEGORIES if category.slug == slug), None)


def item_by_slug(slug: str) -> HelpItem | None:
    """یافتن یک پرسش با نشانه (برای پیوندهای «بیشتر بخوانید»)."""
    return next((item for item in all_items() if item.slug == slug), None)


def item_url(item: HelpItem) -> str:
    """نشانی پایدار یک پرسش — برای لینک‌دادن و اشتراک‌گذاری پاسخ."""
    return f"{reverse('web:help')}#item-{item.slug}"


def related_items(item: HelpItem) -> tuple[HelpItem, ...]:
    """پرسش‌های مرتبط یک پاسخ (بدون تکرار و بدون خودارجاعی)."""
    found: list[HelpItem] = []
    for slug in item.related:
        candidate = item_by_slug(slug)
        if candidate is not None and candidate.slug != item.slug and candidate not in found:
            found.append(candidate)
    return tuple(found)


def action_url(action: HelpAction | None) -> str:
    """نشانی یک دکمهٔ اقدام؛ دکمهٔ بی‌نشانی، هیچ‌وقت خطا نمی‌دهد."""
    return reverse(action.url_name) if action else ""


def _haystack(category: HelpCategory, item: HelpItem, values: dict[str, str]) -> dict[str, str]:
    """زمینه‌های جست‌وجو با وزن‌های متفاوت (پرسش مهم‌تر از پاسخ است)."""
    return {
        "question": normalize_fa(item.question),
        "tags": normalize_fa(" ".join(item.tags)),
        "answer": normalize_fa(render(item.answer, values)),
        "steps": normalize_fa(" ".join(item.steps)),
        "category": normalize_fa(category.title),
    }


def _snippet(item: HelpItem, values: dict[str, str], tokens: Sequence[str]) -> str:
    """برش کوتاه از پاسخ، نزدیک نخستین واژهٔ جست‌وجوشده — برای فهرست نتایج."""
    text = render(item.answer, values)
    normalized = normalize_fa(text)
    position = min((normalized.find(token) for token in tokens if token in normalized), default=-1)
    if position <= 0:
        if len(text) <= SNIPPET_LENGTH:
            return text
        return text[: SNIPPET_LENGTH - 3].rstrip() + "…"
    start = max(0, position - SNIPPET_LEAD)
    piece = text[start : start + SNIPPET_WINDOW].strip()
    prefix = "…" if start else ""
    suffix = "…" if start + SNIPPET_WINDOW < len(text) else ""
    return f"{prefix}{piece}{suffix}"


def search(query: str, *, values: dict[str, str] | None = None, limit: int = 6) -> list[HelpMatch]:
    """جست‌وجوی متنی در پرسش‌ها؛ همهٔ واژه‌های کاربر باید پیدا شوند (منطق «و»).

    وزن‌دهی: پرسش ۵، برچسب ۳، دسته ۲، پاسخ ۲، گام‌ها ۱. ترتیب نهایی با امتیاز
    و سپس ترتیب اصلی دسته‌ها تعیین می‌شود، پس نتیجه‌ها پایدارند.
    """
    tokens = search_tokens(query)
    if not tokens:
        return []
    resolved = values or policy_values()
    matches: list[HelpMatch] = []
    for index, (category, item) in enumerate(_pairs()):
        haystack = _haystack(category, item, resolved)
        if not all(any(token in haystack[field] for field in haystack) for token in tokens):
            continue
        score = sum(
            weight * sum(haystack[field].count(token) for token in tokens)
            for field, weight in SEARCH_WEIGHTS.items()
        )
        matches.append(
            HelpMatch(
                item=item,
                category=category,
                score=score * 100 - index,  # ترتیب اصلی، داورِ امتیاز مساوی
                snippet=_snippet(item, resolved, tokens),
            )
        )
    matches.sort(key=lambda match: match.score, reverse=True)
    return matches[:limit]


def categories_with(items: Iterable[HelpItem]) -> tuple[HelpCategory, ...]:
    """دسته‌هایی که دست‌کم یکی از پرسش‌هایشان در مجموعهٔ ورودی است."""
    slugs = {item.slug for item in items}
    return tuple(
        HelpCategory(
            slug=category.slug,
            title=category.title,
            icon=category.icon,
            summary=category.summary,
            items=tuple(item for item in category.items if item.slug in slugs),
        )
        for category in CATEGORIES
        if any(item.slug in slugs for item in category.items)
    )


def faq_payload(*, values: dict[str, str] | None = None, limit: int = 12) -> list[dict[str, str]]:
    """پرسش‌های پرتکرار برای داده‌ی ساخت‌یافتهٔ ``FAQPage`` (گوگل و صفحه‌خوان).

    محدود و ثابت است تا نشانی صفحه با تغییر محتوا نلرزد؛ فقط پرسش‌هایی که
    پاسخشان «پرسش واقعی زائر» است انتخاب می‌شوند.
    """
    resolved = values or policy_values()
    chosen = (
        "signup",
        "how-to-book",
        "halfday",
        "limits",
        "horizon",
        "cancel",
        "join",
        "offer",
        "arrival",
        "certificate",
        "sms-missing",
        "contact",
    )
    payload: list[dict[str, str]] = []
    for slug in chosen[:limit]:
        item = item_by_slug(slug)
        if item is None:
            continue
        payload.append(
            {
                "question": item.question,
                "answer": render(item.answer, resolved),
            }
        )
    return payload


def today_label() -> str:
    """تاریخ امروز برای زیرنویس کارت چاپی راهنما."""
    return format_jalali(timezone.localdate(), with_weekday=True)


def stats(values: dict[str, str] | None = None) -> dict[str, Any]:
    """شمار خلاصهٔ راهنما — برای نشان‌دادن «چقدر پوشش داریم»."""
    resolved = values or policy_values()
    items = all_items()
    return {
        "categories": len(CATEGORIES),
        "questions": len(items),
        "steps": len(QUICK_STEPS),
        "contact_ready": bool(contact()["phone"]),
        "values": resolved,
    }
