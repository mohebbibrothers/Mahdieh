"""سازندهٔ کارپوشهٔ اکسل — هفت برگه، راست‌به‌چپ، آمادهٔ چاپ و بازتولیدپذیر.

اصول:
    • **داشبورد پیش از جدول:** برگهٔ نخست «خلاصهٔ مدیریتی» است — نوار هویت، کارت‌های
      اطلاعات سند، چهار شاخص درشت، تفکیک وضعیت و منبع با نوار داده، فهرست پیونددار
      برگه‌ها و مُهر اصالت (SHA-256).
    • **راست‌به‌چپ واقعی:** افزون بر جهت برگه، هر خانه ``readingOrder=2`` دارد تا
      گیومه، تاریخ و متن دوزبانه حتی در نمایشگرهای ساده هم وارونه نشوند.
    • **رنگ فقط برای معنا:** «قرص» رنگی وضعیت‌ها، نوار دادهٔ نرخ اشغال، ردیف جمع کل.
    • **بازتولیدپذیری:** هیچ تاریخی جز ``generated_at`` در فایل نیست و متادادهٔ
      سند ثابت است؛ پس دو اجرا با دادهٔ یکسان، فایل یکسان می‌دهد.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink
from openpyxl.worksheet.worksheet import Worksheet

from apps.reporting import theme
from apps.reporting.dataset import ReportData
from apps.reporting.ranges import ReportRange
from apps.reporting.theme import Column
from apps.reservation.choices import ReservationStatus

TITLE_ROW = 1
SUBTITLE_ROW = 2
META_ROW = 3
BAND_ROW = 4
HEADER_ROW = 5
FIRST_DATA_ROW = HEADER_ROW + 1

RTL = 2  # readingOrder: راست‌به‌چپ
LTR = 1
RLM = (
    "\u200f"  # نشانهٔ راست‌به‌چپ — جهت پایهٔ متن را در نمایشگرهای بی‌اعتنا به readingOrder هم حفظ می‌کند
)

_THIN = Side(style="thin", color=theme.BORDER)
_HAIR = Side(style="hair", color=theme.BORDER)

# ستون‌هایی که «قرص» رنگی وضعیت می‌گیرند (رنگ از کد وضعیت یا خود برچسب).
_PILL_KEYS = frozenset({"status_label", "status", "demand"})


# ── سبک ──────────────────────────────────────────────────────────────────────


def _font(
    size: float = 10,
    *,
    bold: bool = False,
    color: str = theme.TEXT,
    italic: bool = False,
    family: str = theme.FONT_FAMILY,
) -> Font:
    """قلم سند — Vazirmatn با رنگ و وزن نقش."""
    return Font(name=family, size=size, bold=bold, italic=italic, color=color)


def _align(
    horizontal: str = "right", *, wrap: bool = False, order: int = RTL, vertical: str = "center"
) -> Alignment:
    """هم‌ترازی با جهت خواندن صریح (پیش‌فرض راست‌به‌چپ)."""
    return Alignment(horizontal=horizontal, vertical=vertical, wrap_text=wrap, readingOrder=order)


def _fill(color: str) -> PatternFill:
    """زمینهٔ یکدست."""
    return PatternFill("solid", start_color=color, end_color=color)


@dataclass(frozen=True, slots=True)
class Style:
    """یک «نقش» بصری خانه — قلم، زمینه، هم‌ترازی، کادر و قالب عدد."""

    font: Font = field(default_factory=_font)
    fill: PatternFill | None = None
    alignment: Alignment = field(default_factory=_align)
    border: Border | None = None
    number_format: str | None = None


def _paint(cell: Any, style: Style) -> None:
    """اعمال نقش روی یک خانه (خانه‌های ادغامی را هم می‌پذیرد)."""
    cell.font = style.font
    cell.alignment = style.alignment
    if style.fill is not None:
        cell.fill = style.fill
    if style.border is not None:
        cell.border = style.border
    if style.number_format:
        cell.number_format = style.number_format


def _block(sheet: Worksheet, area: tuple[int, int, int, int], value: Any, style: Style) -> Any:
    """ادغام بازهٔ ‎(بالا، راست، پایین، چپ)‎، رنگ‌آمیزی همهٔ خانه‌هایش و نوشتن مقدار در خانهٔ نخست."""
    top, left, bottom, right = area
    if (top, left) != (bottom, right):
        sheet.merge_cells(start_row=top, start_column=left, end_row=bottom, end_column=right)
    for row in sheet.iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right):
        for cell in row:
            _paint(cell, style)
    anchor = sheet.cell(row=top, column=left)
    anchor.value = value
    return anchor


def _box(sheet: Worksheet, top: int, left: int, bottom: int, right: int, *, accent: str) -> None:
    """قاب کارت: خط رنگی ضخیم بالا، کادر نازک دیگر لبه‌ها."""
    top_side = Side(style="thick", color=accent)
    for row in range(top, bottom + 1):
        for column in range(left, right + 1):
            sheet.cell(row=row, column=column).border = Border(
                top=top_side if row == top else None,
                bottom=_THIN if row == bottom else None,
                right=_THIN if column == left else None,
                left=_THIN if column == right else None,
            )


def workflow_sheet(workbook: Workbook, name: str, *, tab: str = theme.BRAND) -> Worksheet:
    """برگهٔ راست‌به‌چپ با تنظیمات چاپ حرفه‌ای (A4 افقی، حاشیهٔ متعادل، شمارهٔ صفحه)."""
    sheet = workbook.create_sheet(title=name)
    sheet.sheet_view.rightToLeft = True
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 100
    sheet.sheet_properties.tabColor = tab
    setup = sheet.page_setup
    setup.orientation = "landscape"
    setup.paperSize = sheet.PAPERSIZE_A4
    setup.fitToWidth = 1
    setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_options.horizontalCentered = True
    margins = sheet.page_margins
    margins.left = margins.right = 0.4
    margins.top = margins.bottom = 0.55
    margins.header = margins.footer = 0.25
    sheet.oddFooter.center.text = "صفحهٔ &P از &N"
    sheet.oddFooter.center.size = 8
    sheet.oddFooter.right.text = theme.DOCUMENT_TITLE
    sheet.oddFooter.right.size = 8
    sheet.oddFooter.left.text = "&A"
    sheet.oddFooter.left.size = 8
    return sheet


# ── کارپوشه ──────────────────────────────────────────────────────────────────

_SHEET_INFO: tuple[tuple[str, str, str], ...] = (
    ("رزروها", "reservations", "هر ردیف یک رزرو: واحد، زمان ورود و خروج، نفرات، زائر و یادداشت‌ها"),
    ("تقویم سانس‌ها", "slots", "وضعیت تک‌تک نیم‌روزهای هر واحد: آزاد، اشغال، بافر نظافت، مسدود"),
    ("اشغال واحدها", "units", "کارنامهٔ هر واحد با نرخ اشغال، جمع کل و نمودار ستونی"),
    ("آمار روزانه", "daily", "نبض روزبه‌روز: اشغال، ورودی، خروجی و سطح تقاضا با نمودار روند"),
    ("کنش‌های کارکنان", "staff_actions", "پذیرش، تخلیه، لغو و جابه‌جایی با نام خادم و دلیل ثبت‌شده"),
    ("پیامک‌ها", "sms", "پیامک‌های ارسالی با شمارهٔ پوشیده برای ممیزی و پیگیری تحویل"),
)


def build_workbook(data: ReportData) -> Workbook:
    """ساخت کارپوشهٔ کامل گزارش از دادهٔ آماده."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    _stamp(workbook, data.generated_at)
    _guide_sheet(workbook, data)

    phones = (
        "⚠ شمارهٔ تلفن کامل است — سند محرمانه؛ فقط در سامانهٔ امن نگه دارید."
        if data.phones_revealed
        else "تاریخ‌ها شمسی و شمارهٔ تلفن پوشیده است."
    )
    reservations_sheet, _ = _table_sheet(
        workbook,
        data,
        name="رزروها",
        title="فهرست رزروها",
        subtitle=f"هر ردیف یک رزرو · {phones}",
        columns=_RESERVATION_COLUMNS,
        rows=data.reservations,
    )
    # ۲۳ ستون در A4 ریز می‌شود؛ A3 افقی خوانا می‌ماند.
    reservations_sheet.page_setup.paperSize = reservations_sheet.PAPERSIZE_A3
    _table_sheet(
        workbook,
        data,
        name="تقویم سانس‌ها",
        title="تقویم سانس‌ها",
        subtitle="وضعیت هر نیم‌روز هر واحد: آزاد، اشغال، بافر نظافت یا غیرقابل‌رزرو.",
        columns=_SLOT_COLUMNS,
        rows=data.slots,
    )
    units_sheet, units_last = _table_sheet(
        workbook,
        data,
        name="اشغال واحدها",
        title="کارنامهٔ اشغال واحدها",
        subtitle="شمار نیم‌روزها و نرخ اشغال به‌ازای هر واحد در بازهٔ گزارش.",
        columns=_UNIT_COLUMNS,
        rows=data.units,
        totals=_sum_row(data.units, label_key="unit", label="جمع"),
        tab=theme.ACCENT,
    )
    _chart(units_sheet, _UNIT_COLUMNS, "unit", units_last, "نرخ اشغال به‌تفکیک واحد", kind="bar")
    daily_sheet, daily_last = _table_sheet(
        workbook,
        data,
        name="آمار روزانه",
        title="آمار روزانه",
        subtitle="نبض روزبه‌روز: اشغال، ورودی، خروجی و سطح تقاضا.",
        columns=_DAILY_COLUMNS,
        rows=data.daily,
        totals=_sum_row(data.daily, label_key="day", label="جمع بازه"),
        tab=theme.ACCENT,
    )
    _chart(
        daily_sheet, _DAILY_COLUMNS, "date_jalali", daily_last, "روند نرخ اشغال روزانه", kind="line"
    )
    _table_sheet(
        workbook,
        data,
        name="کنش‌های کارکنان",
        title="کنش‌های کارکنان",
        subtitle="پذیرش، تخلیه، لغو و جابه‌جایی — با نام کارمند و دلیل ثبت‌شده.",
        columns=_ACTION_COLUMNS,
        rows=data.staff_actions,
        tab=theme.SLATE,
    )
    _table_sheet(
        workbook,
        data,
        name="پیامک‌ها",
        title="گزارش پیامک‌ها",
        subtitle="ارسال‌ها با شمارهٔ پوشیده؛ برای ممیزی و پیگیری تحویل.",
        columns=_SMS_COLUMNS,
        rows=data.sms,
        tab=theme.SLATE,
    )
    return workbook


def _stamp(workbook: Workbook, generated_at: datetime) -> None:
    """ثبت متادادهٔ ثابت سند — شرط لازم بازتولیدپذیری بایت‌به‌بایت."""
    properties = workbook.properties
    properties.title = theme.DOCUMENT_TITLE
    properties.subject = theme.DOCUMENT_SUBTITLE
    properties.creator = theme.DOCUMENT_FOOTER
    properties.lastModifiedBy = theme.DOCUMENT_FOOTER
    properties.description = "خروجی رسمی سامانهٔ رزرو زائرسرای مهدیه"
    properties.category = "گزارش عملیاتی"
    properties.keywords = "مهدیه, رزرو, زائرسرا, گزارش"
    properties.created = generated_at
    properties.modified = generated_at


# ── برگهٔ نخست: خلاصهٔ مدیریتی ───────────────────────────────────────────────

COVER_FIRST = 2  # ستون B
COVER_LAST = 12  # ستون L
COVER_COLUMN_WIDTH = 15
COVER_GAP_WIDTH = 1.6
# چهار کارت دوستونه با ستون‌های باریک فاصله میانشان: B:C · D · E:F · G · H:I · J · K:L
TILES: tuple[tuple[int, int], ...] = ((2, 3), (5, 6), (8, 9), (11, 12))
GAPS = (4, 7, 10)
# دو نیمهٔ هم‌عرض برای جدول‌های کنار هم: B..F و H..L (ستون G فاصله است)
HALVES: tuple[tuple[int, int], ...] = ((2, 6), (8, 12))


def _tiles() -> tuple[tuple[int, int], ...]:
    """بازهٔ ستون‌های چهار کارت هم‌عرض ردیف."""
    return TILES


def _guide_sheet(workbook: Workbook, data: ReportData) -> None:
    """برگهٔ نخست — داشبورد یک‌صفحه‌ای قابل‌چاپ."""
    sheet = workflow_sheet(workbook, "راهنما", tab=theme.ACCENT)
    sheet.column_dimensions["A"].width = 2.5
    sheet.column_dimensions[get_column_letter(COVER_LAST + 1)].width = 2.5
    for column in range(COVER_FIRST, COVER_LAST + 1):
        width = COVER_GAP_WIDTH if column in GAPS else COVER_COLUMN_WIDTH
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.page_setup.orientation = "portrait"

    row = _cover_banner(sheet)
    row = _cover_meta(sheet, data, row + 1)
    row = _cover_kpis(sheet, data, row + 1)
    row = _cover_breakdowns(sheet, data, row + 1)
    row = _cover_index(sheet, data, row + 1)
    row = _cover_seal(sheet, data, row + 1)
    sheet.print_area = f"A1:{get_column_letter(COVER_LAST + 1)}{row}"


def _cover_banner(sheet: Worksheet) -> int:
    """نوار هویت: باریکهٔ طلایی، عنوان درشت روی سبز تیره و زیرعنوان طلایی."""
    _block(sheet, (1, COVER_FIRST, 1, COVER_LAST), None, Style(fill=_fill(theme.ACCENT)))
    sheet.row_dimensions[1].height = 5
    _block(
        sheet,
        (2, COVER_FIRST, 2, COVER_LAST),
        theme.DOCUMENT_TITLE,
        Style(
            font=_font(20, bold=True, color=theme.WHITE),
            fill=_fill(theme.BRAND_DARK),
            alignment=_align("center"),
        ),
    )
    sheet.row_dimensions[2].height = 44
    _block(
        sheet,
        (3, COVER_FIRST, 3, COVER_LAST),
        f"{RLM}{theme.DOCUMENT_SUBTITLE} · خلاصهٔ مدیریتی",
        Style(
            font=_font(10.5, color=theme.GOLD_LIGHT),
            fill=_fill(theme.BRAND_DARK),
            alignment=_align("center", vertical="top"),
        ),
    )
    sheet.row_dimensions[3].height = 24
    return 3


def _section(sheet: Worksheet, row: int, left: int, right: int, title: str) -> int:
    """سرفصل بخش: متن سبز درشت با خط طلایی زیرین."""
    sheet.row_dimensions[row].height = 26
    _block(
        sheet,
        (row, left, row, right),
        title,
        Style(
            font=_font(12.5, bold=True, color=theme.BRAND_DARK),
            alignment=_align("right", vertical="bottom"),
            border=Border(bottom=Side(style="medium", color=theme.ACCENT)),
        ),
    )
    return row + 1


def _cover_meta(sheet: Worksheet, data: ReportData, row: int) -> int:
    """چهار کارت اطلاعات سند: بازه، طول بازه، زمان تولید و حالت شماره‌ها."""
    report_range = data.report_range
    phone_value, phone_color = (
        ("کامل · محرمانه", theme.DANGER)
        if data.phones_revealed
        else ("پوشیده (امن)", theme.SUCCESS)
    )
    cards = (
        (
            "بازهٔ گزارش",
            f"{RLM}{report_range.start_jalali} تا {report_range.end_jalali}",
            theme.TEXT,
        ),
        ("طول بازه", f"{RLM}{report_range.days} روز", theme.TEXT),
        ("زمان تولید", f"{RLM}{_jalali_stamp(data.generated_at)}", theme.TEXT),
        ("شمارهٔ تلفن زائران", phone_value, phone_color),
    )
    sheet.row_dimensions[row].height = 20
    sheet.row_dimensions[row + 1].height = 26
    for (left, right), (label, value, color) in zip(_tiles(), cards, strict=True):
        _block(
            sheet,
            (row, left, row, right),
            label,
            Style(
                font=_font(9, color=theme.MUTED),
                fill=_fill(theme.SURFACE),
                alignment=_align("center"),
            ),
        )
        _block(
            sheet,
            (row + 1, left, row + 1, right),
            value,
            Style(
                font=_font(11, bold=True, color=color),
                fill=_fill(theme.SURFACE),
                alignment=_align("center"),
            ),
        )
        _box(sheet, row, left, row + 1, right, accent=theme.BORDER)
    return row + 2


def _cover_kpis(sheet: Worksheet, data: ReportData, row: int) -> int:
    """چهار شاخص درشت: رزروها، زائران، نرخ اشغال و کنش‌های کارکنان."""
    row = _section(sheet, row + 1, COVER_FIRST, COVER_LAST, "نمای کلی بازه")
    totals = data.totals
    reservations = int(totals.get("reservations", 0))
    guests = int(totals.get("guests", 0))
    average = (
        f"میانگین {guests / reservations:.1f} نفر در هر رزرو" if reservations else "رزروی ثبت نشده"
    )
    tiles: tuple[tuple[str, Any, str, str, str], ...] = (
        (
            "رزروهای بازه",
            reservations,
            "#,##0",
            f"{RLM}فعال {totals.get('active', 0)} · پایان‌یافته یا لغو {totals.get('cancelled', 0)}",
            theme.BRAND,
        ),
        ("مجموع زائران", guests, "#,##0", f"{RLM}{average}", theme.INFO),
        (
            "نرخ اشغال",
            float(totals.get("occupancy") or 0),
            "0.0%",
            f"{RLM}{totals.get('occupied_cells', 0)} از {totals.get('total_cells', 0)} نیم‌روز",
            theme.occupancy_color(float(totals.get("occupancy") or 0)),
        ),
        (
            "کنش‌های کارکنان",
            data.row_counts["staff_actions"],
            "#,##0",
            f"{RLM}پیامک‌های بازه: {data.row_counts['sms']}",
            theme.SLATE,
        ),
    )
    heights = (20, 40, 22)
    for offset, height in enumerate(heights):
        sheet.row_dimensions[row + offset].height = height
    for (left, right), (label, value, number_format, caption, accent) in zip(
        _tiles(), tiles, strict=True
    ):
        white = _fill(theme.WHITE)
        _block(
            sheet,
            (row, left, row, right),
            label,
            Style(
                font=_font(9.5, bold=True, color=theme.MUTED),
                fill=white,
                alignment=_align("center"),
            ),
        )
        _block(
            sheet,
            (row + 1, left, row + 1, right),
            value,
            Style(
                font=_font(24, bold=True, color=accent),
                fill=white,
                alignment=_align("center"),
                number_format=number_format,
            ),
        )
        _block(
            sheet,
            (row + 2, left, row + 2, right),
            caption,
            Style(font=_font(8.5, color=theme.MUTED), fill=white, alignment=_align("center")),
        )
        _box(sheet, row, left, row + 2, right, accent=accent)
    return row + len(heights)


def _cover_breakdowns(sheet: Worksheet, data: ReportData, row: int) -> int:
    """تفکیک وضعیت (راست) و منبع ثبت (چپ) کنار هم — با سهم درصدی و نوار داده."""
    (status_left, status_right), (source_left, source_right) = HALVES
    _section(sheet, row + 1, status_left, status_right, "تفکیک وضعیت رزروها")
    start = _section(sheet, row + 1, source_left, source_right, "تفکیک منبع ثبت")
    totals = data.totals
    statuses = [
        (str(ReservationStatus(code).label), count, theme.status_color(code))
        for code, count in totals.get("by_status_code", {}).items()
    ]
    sources = [
        (str(label), count, theme.BRAND) for label, count in totals.get("by_source", {}).items()
    ]
    end_left = _mini_table(sheet, start, status_left, ("وضعیت", "شمار", "سهم"), statuses)
    end_right = _mini_table(sheet, start, source_left, ("منبع", "شمار", "سهم"), sources)
    return max(end_left, end_right)


def _mini_table(
    sheet: Worksheet,
    row: int,
    left: int,
    headers: tuple[str, str, str],
    items: Sequence[tuple[str, int, str]],
) -> int:
    """جدول کوچک یک نیمه (برچسب سه‌ستونه، شمار، سهم) با نوار دادهٔ سهم."""
    head = Style(
        font=_font(9.5, bold=True, color=theme.WHITE),
        fill=_fill(theme.BRAND),
        alignment=_align("center"),
    )
    _block(sheet, (row, left, row, left + 2), headers[0], head)
    _block(sheet, (row, left + 3, row, left + 3), headers[1], head)
    _block(sheet, (row, left + 4, row, left + 4), headers[2], head)
    sheet.row_dimensions[row].height = 22
    if not items:
        _block(
            sheet,
            (row + 1, left, row + 1, left + 4),
            "در این بازه ردیفی نیست",
            Style(
                font=_font(9.5, italic=True, color=theme.MUTED),
                alignment=_align("center"),
                border=Border(bottom=_THIN),
            ),
        )
        return row + 2
    whole = sum(count for _, count, _ in items) or 1
    for offset, (label, count, color) in enumerate(
        sorted(items, key=lambda item: -item[1]), start=1
    ):
        line = Border(bottom=_HAIR)
        zebra = _fill(theme.ZEBRA) if offset % 2 == 0 else None
        _block(
            sheet,
            (row + offset, left, row + offset, left + 2),
            label,
            Style(font=_font(10, bold=True, color=color), fill=zebra, border=line),
        )
        _block(
            sheet,
            (row + offset, left + 3, row + offset, left + 3),
            count,
            Style(
                font=_font(10),
                fill=zebra,
                alignment=_align("center"),
                border=line,
                number_format="#,##0",
            ),
        )
        _block(
            sheet,
            (row + offset, left + 4, row + offset, left + 4),
            round(count / whole, 4),
            Style(
                font=_font(9.5, color=theme.MUTED),
                fill=zebra,
                alignment=_align("center"),
                border=line,
                number_format="0%",
            ),
        )
        sheet.row_dimensions[row + offset].height = 20
    last = row + len(items)
    share = get_column_letter(left + 4)
    sheet.conditional_formatting.add(
        f"{share}{row + 1}:{share}{last}",
        DataBarRule(
            start_type="num", start_value=0, end_type="num", end_value=1, color=theme.ACCENT
        ),
    )
    return last + 1


def _cover_index(sheet: Worksheet, data: ReportData, row: int) -> int:
    """فهرست برگه‌ها با پیوند درون‌سندی، شمار ردیف و شرح محتوا."""
    row = _section(sheet, row + 1, COVER_FIRST, COVER_LAST, "فهرست برگه‌های کارپوشه")
    head = Style(
        font=_font(9.5, bold=True, color=theme.WHITE),
        fill=_fill(theme.BRAND),
        alignment=_align("center"),
    )
    _block(sheet, (row, COVER_FIRST, row, COVER_FIRST + 1), "برگه", head)
    _block(sheet, (row, COVER_FIRST + 2, row, COVER_FIRST + 3), "ردیف‌ها", head)
    _block(sheet, (row, COVER_FIRST + 4, row, COVER_LAST), "محتوا", head)
    sheet.row_dimensions[row].height = 22
    for offset, (name, key, description) in enumerate(_SHEET_INFO, start=1):
        line = row + offset
        zebra = _fill(theme.ZEBRA) if offset % 2 == 0 else None
        border = Border(bottom=_HAIR)
        link = _block(
            sheet,
            (line, COVER_FIRST, line, COVER_FIRST + 1),
            f"{name} ←",
            Style(
                font=Font(
                    name=theme.FONT_FAMILY,
                    size=10,
                    bold=True,
                    color=theme.BRAND,
                    underline="single",
                ),
                fill=zebra,
                border=border,
            ),
        )
        link.hyperlink = Hyperlink(ref=link.coordinate, location=f"'{name}'!A1", display=name)
        _block(
            sheet,
            (line, COVER_FIRST + 2, line, COVER_FIRST + 3),
            data.row_counts[key],
            Style(
                font=_font(10, bold=True),
                fill=zebra,
                alignment=_align("center"),
                border=border,
                number_format="#,##0",
            ),
        )
        _block(
            sheet,
            (line, COVER_FIRST + 4, line, COVER_LAST),
            description,
            Style(font=_font(9.5, color=theme.MUTED), fill=zebra, border=border),
        )
        sheet.row_dimensions[line].height = 21
    return row + len(_SHEET_INFO) + 1


def _cover_seal(sheet: Worksheet, data: ReportData, row: int) -> int:
    """مُهر اصالت: اثر انگشت SHA-256 داده، نسخهٔ قالب و پانویس رسمی."""
    row = _section(sheet, row + 1, COVER_FIRST, COVER_LAST, "اصالت و ممیزی")
    label = Style(
        font=_font(9.5, bold=True, color=theme.TEXT),
        fill=_fill(theme.SURFACE),
        border=Border(bottom=_HAIR),
    )
    _block(sheet, (row, COVER_FIRST, row, COVER_FIRST + 1), "اثر انگشت محتوا (SHA-256)", label)
    _block(
        sheet,
        (row, COVER_FIRST + 2, row, COVER_LAST),
        data.payload_digest(),
        Style(
            font=_font(9, color=theme.BRAND_DARK, family=theme.MONO_FAMILY),
            alignment=_align("left", order=LTR),
            border=Border(bottom=_HAIR),
        ),
    )
    _block(sheet, (row + 1, COVER_FIRST, row + 1, COVER_FIRST + 1), "نسخهٔ قالب سند", label)
    _block(
        sheet,
        (row + 1, COVER_FIRST + 2, row + 1, COVER_LAST),
        f"{RLM}{theme.DOCUMENT_VERSION} · هر تغییر در داده، اثر انگشت بالا را عوض می‌کند.",
        Style(font=_font(9.5, color=theme.MUTED), border=Border(bottom=_HAIR)),
    )
    for offset in (0, 1):
        sheet.row_dimensions[row + offset].height = 21
    footer = row + 3
    _block(
        sheet,
        (footer, COVER_FIRST, footer, COVER_LAST),
        theme.DOCUMENT_FOOTER,
        Style(font=_font(9, italic=True, color=theme.MUTED), alignment=_align("center")),
    )
    return footer


# ── برگه‌های جدولی ───────────────────────────────────────────────────────────


def _table_sheet(
    workbook: Workbook,
    data: ReportData,
    *,
    name: str,
    title: str,
    subtitle: str,
    columns: Sequence[Column],
    rows: Iterable[dict[str, Any]],
    totals: dict[str, Any] | None = None,
    tab: str = theme.BRAND,
) -> tuple[Worksheet, int]:
    """یک برگهٔ جدولی کامل: نوار عنوان، سرستون یخ‌زده، داده، جمع کل، پالایه و چاپ."""
    sheet = workflow_sheet(workbook, name, tab=tab)
    materialized = list(rows)
    span = max(len(columns), 1)
    _table_banner(sheet, span, title, subtitle, _meta_line(data, len(materialized)))
    _write_header(sheet, columns)
    last = _write_rows(sheet, columns, materialized)
    for index, column in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = column.width
    if last < FIRST_DATA_ROW:
        _empty_row(sheet, span)
    else:
        sheet.auto_filter.ref = f"A{HEADER_ROW}:{get_column_letter(span)}{last}"
        _occupancy_bars(sheet, columns, last)
        if totals is not None:
            _write_totals(sheet, columns, totals, last + 1)
    sheet.freeze_panes = f"A{FIRST_DATA_ROW}"
    sheet.print_title_rows = f"{HEADER_ROW}:{HEADER_ROW}"
    return sheet, last


def _meta_line(data: ReportData, count: int) -> str:
    """سطر اطلاعات زیر عنوان هر برگه."""
    report_range = data.report_range
    return (
        f"{RLM}بازهٔ {report_range.start_jalali} تا {report_range.end_jalali} "
        f"({report_range.days} روز) · {count} ردیف · تولید {_jalali_stamp(data.generated_at)}"
    )


def _table_banner(sheet: Worksheet, span: int, title: str, subtitle: str, meta: str) -> None:
    """نوار سه‌سطری بالای جدول + باریکهٔ طلایی جداکننده."""
    dark = _fill(theme.BRAND_DARK)
    _block(
        sheet,
        (TITLE_ROW, 1, TITLE_ROW, span),
        title,
        Style(font=_font(16, bold=True, color=theme.WHITE), fill=dark),
    )
    _block(
        sheet,
        (SUBTITLE_ROW, 1, SUBTITLE_ROW, span),
        f"{RLM}{subtitle}",
        Style(font=_font(10, color=theme.GOLD_LIGHT), fill=dark),
    )
    _block(
        sheet,
        (META_ROW, 1, META_ROW, span),
        meta,
        Style(font=_font(9, color=theme.MUTED), fill=_fill(theme.SURFACE)),
    )
    _block(sheet, (BAND_ROW, 1, BAND_ROW, span), None, Style(fill=_fill(theme.ACCENT)))
    sheet.row_dimensions[TITLE_ROW].height = 34
    sheet.row_dimensions[SUBTITLE_ROW].height = 20
    sheet.row_dimensions[META_ROW].height = 20
    sheet.row_dimensions[BAND_ROW].height = 3


def _write_header(sheet: Worksheet, columns: Sequence[Column]) -> None:
    """سرستون‌های سبز با خط طلایی زیرین."""
    style = Style(
        font=_font(10.5, bold=True, color=theme.WHITE),
        fill=_fill(theme.BRAND),
        alignment=_align("center", wrap=True),
        border=Border(
            bottom=Side(style="medium", color=theme.ACCENT),
            left=Side(style="thin", color=theme.BRAND_DARK),
        ),
    )
    for index, column in enumerate(columns, start=1):
        cell = sheet.cell(row=HEADER_ROW, column=index, value=column.header)
        _paint(cell, style)
    sheet.row_dimensions[HEADER_ROW].height = 30


def _pill_color(key: str, row: dict[str, Any], value: Any) -> str | None:
    """رنگ معنایی ستون وضعیت — از کد وضعیت رزرو یا خود برچسب."""
    if key not in _PILL_KEYS or not isinstance(value, str):
        return None
    if key == "status_label":
        return theme.status_color(str(row.get("status", "")))
    return theme.LABEL_TONES.get(value)


def _write_rows(sheet: Worksheet, columns: Sequence[Column], rows: Sequence[dict[str, Any]]) -> int:
    """ردیف‌های داده: راه‌راه ملایم، «قرص» وضعیت، رنگ نرخ اشغال — بازگشت آخرین ردیف."""
    zebra = _fill(theme.ZEBRA)
    line = Border(bottom=_HAIR)
    for offset, row in enumerate(rows):
        row_index = FIRST_DATA_ROW + offset
        sheet.row_dimensions[row_index].height = 21
        for column_index, column in enumerate(columns, start=1):
            value = row.get(column.key, "")
            cell = sheet.cell(row=row_index, column=column_index, value=value)
            cell.font = _font(10)
            cell.alignment = _align("center" if column.align == "center" else "right")
            cell.border = line
            if offset % 2 == 1:
                cell.fill = zebra
            if column.number_format:
                cell.number_format = column.number_format
            tone = _pill_color(column.key, row, value)
            if tone and value:
                cell.font = _font(9.5, bold=True, color=tone)
                cell.fill = _fill(theme.tint(tone, 0.13))
            if column.key == "occupancy" and isinstance(value, int | float):
                cell.font = _font(10, bold=True, color=theme.occupancy_color(float(value)))
    return FIRST_DATA_ROW + len(rows) - 1


def _empty_row(sheet: Worksheet, span: int) -> None:
    """حالت خالی: پیام آرام به‌جای جدول بی‌ردیف."""
    _block(
        sheet,
        (FIRST_DATA_ROW, 1, FIRST_DATA_ROW, span),
        "در این بازه ردیفی برای این برگه ثبت نشده است.",
        Style(
            font=_font(10.5, italic=True, color=theme.MUTED),
            fill=_fill(theme.ZEBRA),
            alignment=_align("center"),
            border=Border(bottom=_THIN),
        ),
    )
    sheet.row_dimensions[FIRST_DATA_ROW].height = 40


_SUM_KEYS = ("total", "occupied", "free", "buffer", "blocked", "nights", "arrivals", "departures")


def _sum_row(rows: Sequence[dict[str, Any]], *, label_key: str, label: str) -> dict[str, Any]:
    """ردیف جمع کل (مقدار محاسبه‌شده، نه فرمول — در هر نمایشگری درست دیده شود)."""
    out: dict[str, Any] = {label_key: label}
    for key in _SUM_KEYS:
        values = [row[key] for row in rows if isinstance(row.get(key), int | float)]
        if values:
            out[key] = sum(values)
    total = out.get("total") or 0
    if total:
        out["occupancy"] = round((out.get("occupied") or 0) / total, 4)
    return out


def _write_totals(
    sheet: Worksheet, columns: Sequence[Column], totals: dict[str, Any], row: int
) -> None:
    """نوشتن ردیف جمع کل با زمینهٔ سبز کم‌رنگ و خط بالایی پررنگ."""
    style = Style(
        font=_font(10.5, bold=True, color=theme.BRAND_DARK),
        fill=_fill(theme.BRAND_TINT),
        alignment=_align("center"),
        border=Border(top=Side(style="medium", color=theme.BRAND), bottom=_THIN),
    )
    for index, column in enumerate(columns, start=1):
        cell = sheet.cell(row=row, column=index, value=totals.get(column.key))
        _paint(cell, style)
        if column.number_format:
            cell.number_format = column.number_format
    sheet.row_dimensions[row].height = 24


def _occupancy_bars(sheet: Worksheet, columns: Sequence[Column], last_row: int) -> None:
    """نوار دادهٔ نرخ اشغال — نگاه یک‌نگاهی به فشار ظرفیت هر ردیف."""
    for index, column in enumerate(columns, start=1):
        if column.key != "occupancy":
            continue
        letter = get_column_letter(index)
        sheet.conditional_formatting.add(
            f"{letter}{FIRST_DATA_ROW}:{letter}{last_row}",
            DataBarRule(
                start_type="num", start_value=0, end_type="num", end_value=1, color="5FB39C"
            ),
        )


CHART_WIDTH_CM = 18
CHART_HEIGHT_CM = 8.5


def _chart(
    sheet: Worksheet,
    columns: Sequence[Column],
    label_key: str,
    last_row: int,
    title: str,
    *,
    kind: str,
    value_key: str = "occupancy",
) -> None:
    """نمودار بومی اکسل کنار جدول (دادهٔ زنده و قابل‌ویرایش) با رنگ برند."""
    if last_row < FIRST_DATA_ROW:
        return
    keys = [column.key for column in columns]
    if label_key not in keys or value_key not in keys:
        return
    label_col = keys.index(label_key) + 1
    value_col = keys.index(value_key) + 1
    chart: BarChart | LineChart = BarChart() if kind == "bar" else LineChart()
    chart.title = title
    chart.style = 2
    chart.legend = None
    chart.width = CHART_WIDTH_CM
    chart.height = CHART_HEIGHT_CM
    chart.y_axis.number_format = "0%"
    chart.y_axis.scaling.min = 0
    chart.y_axis.scaling.max = 1
    chart.y_axis.majorGridlines = None
    chart.x_axis.delete = False
    chart.y_axis.delete = False
    values = Reference(
        sheet, min_col=value_col, max_col=value_col, min_row=HEADER_ROW, max_row=last_row
    )
    labels = Reference(sheet, min_col=label_col, min_row=FIRST_DATA_ROW, max_row=last_row)
    chart.add_data(values, titles_from_data=True)
    chart.set_categories(labels)
    series = chart.series[0]
    if isinstance(chart, BarChart):
        chart.gapWidth = 60
        series.graphicalProperties.solidFill = theme.BRAND
        series.graphicalProperties.line.solidFill = theme.BRAND_DARK
    else:
        series.graphicalProperties.line.solidFill = theme.BRAND
        series.graphicalProperties.line.width = 28575  # ۲٫۲۵ پوینت
        series.smooth = False
    # زیر جدول (پس از ردیف جمع) ⇒ صفحهٔ چاپی هم‌عرض جدول می‌ماند.
    sheet.add_chart(chart, f"B{last_row + 4}")


# ── تعریف ستون‌ها ────────────────────────────────────────────────────────────

_RESERVATION_COLUMNS: tuple[Column, ...] = (
    Column("کد رزرو", "code", 13, align="center"),
    Column("وضعیت", "status_label", 14, align="center"),
    Column("منبع", "source", 10, align="center"),
    Column("طبقه", "floor", 7, align="center"),
    Column("واحد", "unit", 8, align="center"),
    Column("عنوان واحد", "unit_title", 20),
    Column("نوع واحد", "unit_type", 12, align="center"),
    Column("ورود (شمسی)", "start_jalali", 13, align="center"),
    Column("ساعت ورود", "start_time", 10, align="center"),
    Column("خروج (شمسی)", "end_jalali", 13, align="center"),
    Column("ساعت خروج", "end_time", 10, align="center"),
    Column("نیم‌روز", "cells", 9, align="center"),
    Column("مدت اقامت", "duration", 20),
    Column("نفرات", "guests", 8, align="center"),
    Column("نام زائر", "guest_name", 20),
    Column("تلفن", "guest_phone", 16, align="center"),
    Column("ملیت", "nationality", 14),
    Column("استان", "province", 14),
    Column("شهر", "city", 14),
    Column("ثبت‌کننده", "created_by", 18),
    Column("یادداشت زائر", "guest_note", 26),
    Column("یادداشت داخلی", "staff_note", 26),
    Column("زمان ثبت", "created_at", 17, align="center"),
)

_SLOT_COLUMNS: tuple[Column, ...] = (
    Column("تاریخ", "day", 12, align="center"),
    Column("تاریخ شمسی", "date_jalali", 13, align="center"),
    Column("روز هفته", "weekday", 11, align="center"),
    Column("واحد", "unit", 8, align="center"),
    Column("طبقه", "floor", 7, align="center"),
    Column("عنوان واحد", "unit_title", 20),
    Column("نیم‌روز", "half", 14, align="center"),
    Column("ساعت شروع", "start", 10, align="center"),
    Column("ساعت پایان", "end", 10, align="center"),
    Column("وضعیت سانس", "status", 16, align="center"),
    Column("توضیح", "detail", 34),
)

_UNIT_COLUMNS: tuple[Column, ...] = (
    Column("واحد", "unit", 8, align="center"),
    Column("طبقه", "floor", 7, align="center"),
    Column("عنوان واحد", "unit_title", 22),
    Column("نوع", "unit_type", 12, align="center"),
    Column("ظرفیت", "capacity", 9, align="center"),
    Column("نیم‌روز کل", "total", 11, align="center"),
    Column("اشغال", "occupied", 9, align="center"),
    Column("آزاد", "free", 9, align="center"),
    Column("بافر نظافت", "buffer", 12, align="center"),
    Column("غیرقابل‌رزرو", "blocked", 13, align="center"),
    Column("نرخ اشغال", "occupancy", 12, "0.0%", align="center"),
    Column("شب‌های رزروشده", "nights", 15, align="center"),
)

_DAILY_COLUMNS: tuple[Column, ...] = (
    Column("تاریخ", "day", 12, align="center"),
    Column("تاریخ شمسی", "date_jalali", 13, align="center"),
    Column("روز هفته", "weekday", 11, align="center"),
    Column("پایان هفته", "is_weekend", 12, align="center"),
    Column("سطح تقاضا", "demand", 13, align="center"),
    Column("نیم‌روز کل", "total", 11, align="center"),
    Column("اشغال", "occupied", 9, align="center"),
    Column("آزاد", "free", 9, align="center"),
    Column("نرخ اشغال", "occupancy", 12, "0.0%", align="center"),
    Column("ورودی", "arrivals", 9, align="center"),
    Column("خروجی", "departures", 9, align="center"),
)

_ACTION_COLUMNS: tuple[Column, ...] = (
    Column("زمان", "moment", 17, align="center"),
    Column("کارمند", "actor", 20),
    Column("کنش", "kind", 14, align="center"),
    Column("کد رزرو", "code", 13, align="center"),
    Column("واحد", "unit", 8, align="center"),
    Column("از وضعیت", "from_status", 14, align="center"),
    Column("به وضعیت", "to_status", 14, align="center"),
    Column("دلیل ثبت‌شده", "reason", 42),
)

_SMS_COLUMNS: tuple[Column, ...] = (
    Column("زمان", "moment", 17, align="center"),
    Column("قالب پیام", "template", 20, align="center"),
    Column("تلفن", "phone", 16, align="center"),
    Column("وضعیت", "status", 16, align="center"),
    Column("ارائه‌دهنده", "provider", 14, align="center"),
    Column("تلاش‌ها", "attempts", 9, align="center"),
)


def _jalali_stamp(moment: datetime) -> str:
    """مُهر زمانی شمسی برای برگهٔ راهنما."""
    from django.utils import timezone as django_timezone

    from apps.core.jalali import clock_label, format_jalali

    local = django_timezone.localtime(moment)
    return f"{format_jalali(local.date(), numeric=True)} ساعت {clock_label(local)}"


def sheet_titles(workbook: Workbook) -> list[str]:
    """فهرست نام برگه‌ها — برای تست ساختار و برای مستندسازی."""
    return list(workbook.sheetnames)


def range_label(report_range: ReportRange) -> str:
    """برچسب کوتاه بازه — کمکی برای نام فایل و عنوان."""
    return report_range.label
