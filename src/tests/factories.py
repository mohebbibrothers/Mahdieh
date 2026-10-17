"""ابزارهای ساخت دادهٔ تست — کوچک، صریح، بدون جادو.

چرا ماژول و نه ``factory_boy``؟ چون دادهٔ این دامنه معنای دقیق دارد: «دو نیم‌روز
از ۰۸:۰۰»، «کد ملی با رقم کنترلی درست». توابع صریح، تست‌ها را خودمستند می‌کنند و
وابستگی اضافه به کتابخانهٔ کارخانه نمی‌آورند.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.utils import timezone

from apps.identity.choices import Province, UserRole
from apps.identity.models import User
from apps.identity.validators import validate_national_id
from apps.inventory.choices import UnitType
from apps.inventory.lattice import cell_at_boundary, cells_for_day, period_from_index
from apps.inventory.models import Floor, Unit, UnitFeature

DEFAULT_PHONE = "09123456789"


def make_valid_national_id(seed: str) -> str:
    """ساخت کد ملی معتبر با رقم کنترلی صحیح از ۹ رقم دلخواه."""
    digits = (seed + "000000000")[:9]
    total = sum(int(digit) * (10 - index) for index, digit in enumerate(digits))
    remainder = total % 11
    check = remainder if remainder < 2 else 11 - remainder
    candidate = f"{digits}{check}"
    validate_national_id(candidate)  # تضمین: دادهٔ تست واقعاً معتبر است
    return candidate


def make_floor(number: int = 1, *, title: str = "") -> Floor:
    """ساخت طبقهٔ زائرسرا."""
    return Floor.objects.create(number=number, title=title or f"طبقهٔ {number}", sort_order=number)


def make_unit(
    code: str = "A1",
    *,
    floor: Floor | None = None,
    unit_type: str = UnitType.SUITE,
    title: str = "",
    capacity_adults: int = 4,
    capacity_children: int = 2,
    cleaning_buffer_minutes: int | None = None,
) -> Unit:
    """ساخت واحد اقامتی؛ بافر پیش‌فرض «تابع سیاست» است (همانند تولید)."""
    return Unit.objects.create(
        code=code,
        floor=floor or make_floor(1),
        unit_type=unit_type,
        title=title or f"واحد {code}",
        capacity_adults=capacity_adults,
        capacity_children=capacity_children,
        cleaning_buffer_minutes=cleaning_buffer_minutes,
    )


def make_feature(code: str, *, title: str = "", icon: str = "✓") -> UnitFeature:
    """ساخت امکان واحد."""
    return UnitFeature.objects.create(code=code, title=title or code, icon=icon)


def national_id_for(phone: str, seed: str | None = None) -> str:
    """کد ملی معتبر و **یکتا به‌ازای هر شمارهٔ تلفن**.

    اگر بذر صریح داده شود (تست‌های یکتایی)، همان استفاده می‌شود؛ در غیر این
    صورت ۹ رقم آخر شماره مبنای محاسبه است تا کاربران تست با هم برخورد نکنند.
    """
    if seed:
        return make_valid_national_id(seed)
    digits = "".join(character for character in phone if character.isdigit())[-9:]
    return make_valid_national_id(digits)


def make_user(
    phone: str = DEFAULT_PHONE,
    *,
    role: str = UserRole.GUEST,
    complete_profile: bool = True,
    national_id_seed: str | None = None,
    first_name: str = "زهرا",
    last_name: str = "محمدی",
    province: str = Province.KHORASAN_RAZAVI,
    city: str = "مشهد",
) -> User:
    """ساخت کاربر؛ به‌طور پیش‌فرض پروفایل کامل (دسترسی رزرو باز)."""
    user = User.objects.create_user(phone=phone, role=role)
    if complete_profile:
        national_id = national_id_for(phone, national_id_seed)
        user.complete_profile(
            first_name=first_name,
            last_name=last_name,
            province=province,
            city=city,
            national_id=national_id,
        )
    return user


def make_staff(phone: str = "09991234567", *, role: str = UserRole.OPERATOR) -> User:
    """ساخت کاربر کارکنان با پروفایل کامل."""
    return make_user(phone=phone, role=role, complete_profile=True)


def days_ahead(days: int) -> date:
    """تاریخ محلی به‌اندازهٔ چند روز آینده."""
    return timezone.localdate() + timedelta(days=days)


def lattice_period(*, start_day: date, index: int = 0, cells: int = 2) -> tuple[datetime, datetime]:
    """ساخت بازهٔ روی‌شبکه از یک تاریخ محلی (سلول روزانه/شبانه)."""
    cell = cells_for_day(start_day)[index]
    first_index = cell_at_boundary(cell.start).index
    return period_from_index(first_index, cells)


def future_period(*, days: int = 5, index: int = 0, cells: int = 2) -> tuple[datetime, datetime]:
    """بازهٔ امن در آینده (پیش‌فرض: ۵ روز بعد، دو نیم‌روز)."""
    return lattice_period(start_day=days_ahead(days), index=index, cells=cells)
