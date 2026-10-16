"""شبکهٔ نیم‌روز و توابع رمزنگاری — تست‌های واحد بدون دیتابیس.

این تست‌ها «قانون فیزیکی» سامانه را قفل می‌کنند: نصف روز، شبانه‌روز، هفته.
اگر کسی روزی محاسبهٔ زمان را عوض کند، اینجا شکست می‌خورد — نه در تولید.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
import pytest

from apps.core.crypto import blind_index, decrypt_value, encrypt_value, is_encrypted
from apps.identity.validators import (
    mask_national_id,
    normalize_phone,
    validate_national_id,
    validate_person_name,
)
from apps.inventory.lattice import (
    DEFAULT_LATTICE,
    LatticeConfig,
    cell_at_boundary,
    cells_for_day,
    count_cells,
    index_to_cell,
    is_grid_boundary,
    is_on_grid,
    period_from_index,
    snap_forward,
    stay_label,
    with_cleaning_buffer,
)
from tests.factories import make_valid_national_id

pytestmark = pytest.mark.unit

DAY = date(2026, 10, 20)
TEHRAN_OFFSET_HOURS = 3.5


def test_day_and_night_cells_cover_the_whole_day() -> None:
    """دو سلول هر شبانه‌روز: روزانه ۰۸ تا ۲۰ و شبانه ۲۰ تا ۰۸ فردا."""
    day_cell, night_cell = cells_for_day(DAY)
    assert day_cell.start.hour == 8
    assert day_cell.end.hour == 20
    assert night_cell.start.hour == 20
    assert night_cell.end.date() == DAY + timedelta(days=1)
    assert night_cell.end.hour == 8
    assert day_cell.kind == "day"
    assert night_cell.kind == "night"


def test_cells_are_contiguous_without_gap() -> None:
    """پایان هر سلول دقیقاً آغاز سلول بعدی است — هیچ شکاف پنهانی نیست."""
    day_cell, night_cell = cells_for_day(DAY)
    assert day_cell.end == night_cell.start
    next_day_cell, _ = cells_for_day(DAY + timedelta(days=1))
    assert night_cell.end == next_day_cell.start


def test_cell_index_is_monotonic_and_round_trips() -> None:
    """شمارهٔ سلول یکنواست و تبدیل رفت‌وبرگشت درست کار می‌کند."""
    first = cells_for_day(DAY)[0]
    second = cells_for_day(DAY)[1]
    tomorrow = cells_for_day(DAY + timedelta(days=1))[0]
    assert first.index < second.index < tomorrow.index
    assert index_to_cell(first.index).start == first.start


def test_one_week_equals_fourteen_cells() -> None:
    """«حداکثر یک هفته» = ۱۴ نیم‌روز — تعریف عددی در دامنه."""
    start = cells_for_day(DAY)[0].start
    start_index = cell_at_boundary(start).index
    week_start, week_end = period_from_index(start_index, 14)
    assert count_cells(week_start, week_end) == 14
    assert week_end == cells_for_day(DAY + timedelta(days=7))[0].start


def test_half_day_equals_one_cell() -> None:
    """«حداقل نصف روز» = ۱ نیم‌روز — همان تعریف کارفرما."""
    start, end = period_from_index(cell_at_boundary(cells_for_day(DAY)[0].start).index, 1)
    assert count_cells(start, end) == 1
    assert end - start == timedelta(hours=12)


def test_off_grid_period_is_rejected() -> None:
    """بازهٔ ناهم‌تراز با شبکه، مقدار عددی نمی‌گیرد (قاعدهٔ A1)."""
    start = cells_for_day(DAY)[0].start + timedelta(hours=3)
    end = start + timedelta(hours=12)
    assert not is_on_grid(start, end)
    with pytest.raises(ValueError, match="مرزهای شبکه"):
        count_cells(start, end)


def test_grid_boundaries_detected_across_days() -> None:
    """مرزهای شبکه در همهٔ روزها شناسایی می‌شوند و ۰۸:۳۰ مرز نیست."""
    for moment in (
        datetime(2026, 10, 20, 8, 0, tzinfo=cells_for_day(DAY)[0].start.tzinfo),
        datetime(2026, 10, 20, 20, 0, tzinfo=cells_for_day(DAY)[0].start.tzinfo),
    ):
        assert is_grid_boundary(moment)
    off = datetime(2026, 10, 20, 8, 30, tzinfo=cells_for_day(DAY)[0].start.tzinfo)
    assert not is_grid_boundary(off)


def test_cleaning_buffer_expands_both_sides() -> None:
    """بازهٔ مسدودکننده = اقامت ± بافر نظافت (قاعدهٔ A3)."""
    start, end = period_from_index(cell_at_boundary(cells_for_day(DAY)[0].start).index, 1)
    block_start, block_end = with_cleaning_buffer(start, end, minutes=60)
    assert block_start == start - timedelta(minutes=60)
    assert block_end == end + timedelta(minutes=60)


def test_snap_forward_rounds_to_next_boundary() -> None:
    """گرد کردن به مرز بعدی برای پیشنهاد بازهٔ جایگزین."""
    offset = cells_for_day(DAY)[0].start.tzinfo
    snapped = snap_forward(datetime(2026, 10, 20, 9, 15, tzinfo=offset))
    assert snapped.hour == 20
    exact = snap_forward(datetime(2026, 10, 20, 8, 0, tzinfo=offset))
    assert exact.hour == 8


def test_stay_labels_are_human_readable() -> None:
    """برچسب‌های فارسی مدت اقامت."""
    assert stay_label(1) == "نصف روز"
    assert stay_label(2) == "یک شبانه‌روز کامل"
    assert stay_label(4) == "2 شبانه‌روز کامل"
    assert stay_label(3) == "3 نیم‌روز"


def test_lattice_config_rejects_invalid_cell_hours() -> None:
    """طول سلول باید ۲۴ را بی‌باقی‌مانده تقسیم کند."""
    with pytest.raises(ValueError, match="مقسوم‌علیه"):
        LatticeConfig(cell_hours=5)
    assert DEFAULT_LATTICE.cells_per_day == 2


def test_encryption_round_trip_and_tamper_detection() -> None:
    """رمزنگاری برگشت‌پذیر است، متن روشن ذخیره نمی‌شود، و دستکاری کشف می‌شود."""
    secret = "0079845612"
    token = encrypt_value(secret)
    assert token is not None
    assert secret not in token
    assert is_encrypted(token)
    assert decrypt_value(token) == secret

    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert decrypt_value(tampered, default="مقدار جایگزین") == "مقدار جایگزین"


def test_blind_index_is_deterministic_and_namespaced() -> None:
    """ایندکس کور یکتا و پایدار است، ولی بین زمینه‌ها تفکیک می‌شود."""
    first = blind_index("0079845612", namespace="national_id")
    second = blind_index("0079845612", namespace="national_id")
    other = blind_index("0079845612", namespace="other")
    assert first == second
    assert first != other
    assert len(first) == 64


def test_national_id_checksum_is_enforced() -> None:
    """کد ملی معتبر پذیرفته و کد با رقم کنترلی نادرست رد می‌شود.

    مقدار درست با همان الگوریتم رسمی ساخته می‌شود تا تست، خودش منبع خطا نباشد.
    """
    valid = make_valid_national_id("007984561")
    assert validate_national_id(valid) == valid
    # ارقام فارسی هم پذیرفته می‌شوند (زائر با کیبورد فارسی می‌نویسد)
    persian = str(valid).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    assert validate_national_id(persian) == valid
    with pytest.raises(DjangoValidationError):
        validate_national_id(valid[:9] + str((int(valid[9]) + 1) % 10))
    with pytest.raises(DjangoValidationError):
        validate_national_id("1111111111")


def test_phone_normalization_accepts_persian_and_plus98() -> None:
    """همهٔ قالب‌های رایج شمارهٔ تلفن به یک مقدار نرمال می‌رسند."""
    for raw in ("09123456789", "9123456789", "+989123456789", "00989123456789", "۰۹۱۲۳۴۵۶۷۸۹"):
        assert normalize_phone(raw) == "+989123456789"
    with pytest.raises(DjangoValidationError):
        normalize_phone("12345")


def test_person_name_normalization_and_masking() -> None:
    """نام‌های عربی/فارسی یکسان‌سازی می‌شوند و نمایش‌ها پوشانده‌اند."""
    assert validate_person_name("  زهرا   كاظمي ") == "زهرا کاظمی"
    assert mask_national_id("0079845612") == "***5612"
    with pytest.raises(DjangoValidationError):
        validate_person_name("ز")
