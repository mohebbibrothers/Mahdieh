"""گواهی اقامت — امضای دیجیتال، QR آنلاین و کد کوتاه آفلاین.

مسئلهٔ واقعی: گواهی اقامت زائرسرا در سفر (توقف بین‌راهی، ارائه به اداره) باید
**قابل تأیید** باشد، حتی وقتی اینترنت نیست. پس دو مسیر تأیید می‌سازیم:

    ۱) **آنلاین:** QR شامل توکن امضاشده است؛ بازکردن آن، صفحهٔ تأیید را با
       جزئیات رزرو نشان می‌دهد (و هر بازدید در حسابرسی ثبت می‌شود).
    ۲) **آفلاین:** روی گواهی یک «کد کوتاه» ۸ نویسه‌ای چاپ می‌شود که از هش
       SHA-256 محتوای گواهی ساخته می‌شود. بازرس/میز پذیرش با واردکردن کد رزرو
       و اطلاعات روی کاغذ، همان کد را بازمحاسبه و مقایسه می‌کند — بدون شبکه.

هیچ کلید جدیدی در محیط لازم نیست: امضا با ``SECRET_KEY`` سایت انجام می‌شود.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
import re
from typing import Any, Final

from django.core import signing
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.core.jalali import PERSIAN_MONTHS, clock_label, format_jalali
from apps.core.site import absolute_url
from apps.core.text import to_latin_digits, to_persian_digits
from apps.identity.validators import normalize_person_name

SALT: Final[str] = "mahdieh.certificate"
TOKEN_MAX_AGE_DAYS: Final[int] = 365 * 3
SHORT_CODE_ALPHABET: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # بدون حروف اشتباه‌پذیر


class CertificateError(Exception):
    """گواهی نامعتبر، دست‌کاری‌شده یا منقضی."""


@dataclass(frozen=True, slots=True)
class Certificate:
    """محتوای قابل‌چاپ گواهی — همان چیزی که روی کاغذ می‌رود."""

    code: str
    holder: str
    unit_code: str
    unit_title: str
    floor_number: int
    guests_count: int
    starts_at_local: str
    ends_at_local: str
    issued_on: str
    token: str
    short_code: str

    def fields(self) -> list[tuple[str, str]]:
        """جفت‌های برچسب/مقدار برای چاپ و بازمحاسبهٔ آفلاین."""
        return [
            ("کد رزرو", self.code),
            ("نام زائر", self.holder),
            ("واحد", f"{self.unit_code} — {self.unit_title}"),
            ("طبقه", str(self.floor_number)),
            ("تعداد نفرات", str(self.guests_count)),
            ("ورود", self.starts_at_local),
            ("خروج", self.ends_at_local),
        ]

    def payload(self) -> dict[str, Any]:
        """ساختار امضاشده (بدون خودِ امضا)."""
        return {
            "code": self.code,
            "holder": self.holder,
            "unit": self.unit_code,
            "floor": self.floor_number,
            "guests": self.guests_count,
            "starts": self.starts_at_local,
            "ends": self.ends_at_local,
        }


SHORT_CODE_SALT: Final[str] = "mahdieh.reservation.certificate.short-code.v2"


def _short_code(payload: dict[str, Any]) -> str:
    """کد کوتاه «بی‌اینترنت» — امضای کلیددار محتوای چاپی گواهی.

    روش: کلیدها مرتب و مقادیر به رشته تبدیل می‌شوند؛ سپس HMAC-SHA256 با کلیدی مشتق از
    ‎SECRET_KEY‎ (‎salted_hmac‎) و نمایش پایه-۳۲ با الفبای بی‌ابهام. تغییر هر فیلد روی کاغذ کد را
    عوض می‌کند و — برخلاف هش بی‌کلید نسخهٔ نخست (یادداشت ۷ طراح) — بدون کلید محرمانهٔ سرور
    نمی‌توان برای مشخصات ساختگی کد درست ساخت. بازرسی با ‎POST /api/v1/certificates/verify-offline‎.
    """
    canonical = "|".join(f"{key}={payload[key]}" for key in sorted(payload))
    digest = salted_hmac(SHORT_CODE_SALT, canonical, algorithm="sha256").digest()
    number = int.from_bytes(digest[:8], "big")
    chars: list[str] = []
    for _ in range(8):
        number, remainder = divmod(number, len(SHORT_CODE_ALPHABET))
        chars.append(SHORT_CODE_ALPHABET[remainder])
    return "".join(chars)


def build_certificate(reservation: Any) -> Certificate:
    """ساخت گواهی از یک رزرو — تنها نقطهٔ تولید محتوا."""
    start = timezone.localtime(reservation.starts_at)
    end = timezone.localtime(reservation.ends_at)
    holder = reservation.guest.display_name
    certificate = Certificate(
        code=reservation.code,
        holder=holder,
        unit_code=reservation.unit.code,
        unit_title=reservation.unit.title,
        floor_number=int(reservation.unit.floor.number),
        guests_count=int(reservation.guests_count),
        starts_at_local=f"{format_jalali(start)} · ساعت {clock_label(start)}",
        ends_at_local=f"{format_jalali(end)} · ساعت {clock_label(end)}",
        issued_on=format_jalali(timezone.localdate()),
        token="",  # nosec B106 — جای‌نگهدار امضاست، نه رمز عبور
        short_code="",
    )
    payload = certificate.payload()
    token = signing.dumps(payload, salt=SALT, compress=True)
    # ``replace`` جای ``__dict__`` نشسته است چون dataclass با ``slots=True``
    # دیکشنری نمونه ندارد — و همین ``slots`` است که مصرف حافظه را کم می‌کند.
    return replace(certificate, token=token, short_code=_short_code(payload))


def verify_token(token: str, *, max_age_days: int = TOKEN_MAX_AGE_DAYS) -> dict[str, Any]:
    """اعتبارسنجی آنلاین توکن QR؛ بازگشت محتوای گواهی."""
    try:
        payload = signing.loads(token, salt=SALT, max_age=timedelta(days=max_age_days))
    except signing.SignatureExpired as exc:
        raise CertificateError("اعتبار این گواهی به پایان رسیده است.") from exc
    except signing.BadSignature as exc:
        raise CertificateError("این گواهی معتبر نیست (امضا نادرست است).") from exc
    if not isinstance(payload, dict):  # pragma: no cover - محافظ
        raise CertificateError("این گواهی معتبر نیست.")
    return payload


def verify_short_code(payload: dict[str, Any], short_code: str) -> bool:
    """تأیید **آفلاین** کد کوتاه روی گواهی چاپی.

    بازرس همین تابع را (بدون شبکه) اجرا می‌کند: فیلدهای کاغذ را وارد می‌کند و
    کد بازمحاسبه‌شده با کد چاپ‌شده مقایسه می‌شود. مقایسه در زمان ثابت انجام
    می‌شود تا کانال جانبی نداشته باشد.
    """
    import hmac

    expected = _short_code({key: payload[key] for key in sorted(payload)})
    return hmac.compare_digest(expected, (short_code or "").strip().upper())


# ─── یکسان‌سازی ورودی بازرس ────────────────────────────────────────────────
# کد کوتاه از رشتهٔ دقیق گواهی ساخته می‌شود؛ اما بازرس «۱۴۰۵/۸/۷ ۸:۰۰» یا «۷ آبان
# ۱۴۰۵ · ساعت ۰۸:۰۰» یا با ارقام لاتین می‌نویسد. پیش از بازمحاسبه همه به شکل یگانهٔ
# چاپی برمی‌گردند تا گواهی واقعی به‌خاطر شکل نوشتن «نامعتبر» نشود (امنیت دست‌نخورده:
# هر مقدار متفاوت، کد دیگری می‌دهد).
_STAMP_SEP = r"\s*[·.\-,،]?\s*(?:ساعت)?\s*"
_STAMP_CLOCK = r"(\d{1,2})\s*[:٫.]?\s*(\d{2})"
_STAMP_NUMERIC: Final = re.compile(
    rf"^(\d{{4}})\s*[/\-.]\s*(\d{{1,2}})\s*[/\-.]\s*(\d{{1,2}}){_STAMP_SEP}{_STAMP_CLOCK}$"
)
_STAMP_NAMED: Final = re.compile(rf"^(\d{{1,2}})\s+(\S+)\s+(\d{{4}}){_STAMP_SEP}{_STAMP_CLOCK}$")
_MONTH_INDEX: Final[dict[str, int]] = {name: i for i, name in enumerate(PERSIAN_MONTHS, start=1)}
_LAST_HOUR: Final = 23
_LAST_MINUTE: Final = 59
_LAST_DAY: Final = 31


def _stamp_parts(text: str) -> tuple[int, int, int, int, int] | None:
    """(سال، ماه، روز، ساعت، دقیقه) از هر دو شکل عددی و نام‌ماهی؛ ناخوانا ⇒ ``None``."""
    if match := _STAMP_NUMERIC.match(text):
        year, month, day, hour, minute = (int(part) for part in match.groups())
    elif (match := _STAMP_NAMED.match(text)) and match.group(2) in _MONTH_INDEX:
        day, year, hour, minute = (int(match.group(i)) for i in (1, 3, 4, 5))
        month = _MONTH_INDEX[match.group(2)]
    else:
        return None
    in_range = 1 <= month <= len(PERSIAN_MONTHS) and 1 <= day <= _LAST_DAY
    if not (in_range and hour <= _LAST_HOUR and minute <= _LAST_MINUTE):
        return None
    return year, month, day, hour, minute


def canonical_stamp(value: str) -> str:
    """شکل یگانهٔ لحظهٔ چاپی گواهی: «۷ آبان ۱۴۰۵ · ساعت ۰۸:۰۰».

    ورودی ناخوانا دست‌نخورده (فقط فاصله‌پیراسته) برمی‌گردد تا مقایسه طبق معمول
    «نامعتبر» دهد، نه خطای سرور.
    """
    text = normalize_person_name(value)
    parts = _stamp_parts(to_latin_digits(text))
    if parts is None:
        return text
    year, month, day, hour, minute = parts
    return to_persian_digits(
        f"{day} {PERSIAN_MONTHS[month - 1]} {year} · ساعت {hour:02d}:{minute:02d}"
    )


def verify_fields(
    *,
    code: str,
    holder: str,
    unit: str,
    floor: int,
    guests: int,
    starts: str,
    ends: str,
    short_code: str,
) -> bool:
    """تأیید آفلاین از ورودی خام فرم بازرسی (بدون هیچ کوئری دیتابیس)."""
    payload = {
        "code": to_latin_digits(code).strip().upper(),
        # نام گواهی همان نام یکسان‌شدهٔ پروفایل است ⇒ ورودی بازرس هم با همان قاعده.
        "holder": normalize_person_name(holder),
        "unit": to_latin_digits(unit).strip().upper(),
        "floor": int(floor),
        "guests": int(guests),
        "starts": canonical_stamp(starts),
        "ends": canonical_stamp(ends),
    }
    return verify_short_code(payload, short_code)


def certificate_verify_url(certificate: Certificate, *, base_url: str | None = None) -> str:
    """نشانی تأیید آنلاین گواهی (برای چاپ زیر QR و درج در QR)."""
    return absolute_url(f"/verify/{certificate.token}/", base_url=base_url)


def certificate_qr_svg(
    certificate: Certificate, *, scale: int = 4, base_url: str | None = None
) -> str:
    """QR گواهی به‌شکل SVG درون‌خطی (بدون فایل و بدون CDN).

    محتوای QR یک URL کوتاه تأیید است، نه کل داده: کوچک‌تر می‌شود، در چاپ
    خوانا می‌ماند و محتوای گواهی روی خود کاغذ هم چاپ شده است.
    """
    import segno  # کتابخانهٔ سنگین، فقط هنگام ساخت QR بارگذاری می‌شود

    qr = segno.make(certificate_verify_url(certificate, base_url=base_url), error="m", micro=False)
    # توجه: ``svg_inline`` خودش ``xmldecl=False`` و ``svgns=False`` را پاس می‌دهد؛
    # تکرار آن‌ها خطای «آرگومان تکراری» می‌دهد.
    return qr.svg_inline(scale=scale, dark="#0b1220", light=None)


@dataclass(frozen=True, slots=True)
class OnlineVerification:
    """نتیجهٔ تأیید آنلاین — هم برای صفحهٔ SSR و هم برای API.

    ``crypto_valid``: امضای توکن درست است. ``mismatch``: امضا درست است ولی رکورد
    فعلی دیتابیس با محتوای امضاشده نمی‌خواند (رزرو جابه‌جا/حذف شده یا جعل).
    محتوای نمایش‌داده‌شده **فقط** از توکن امضاشده می‌آید.
    """

    crypto_valid: bool
    mismatch: bool
    error: str
    payload: dict[str, Any]
    reservation: Any

    @property
    def valid(self) -> bool:
        """گواهی معتبر و هم‌خوان با سامانه."""
        return self.crypto_valid and not self.mismatch

    def as_dict(self) -> dict[str, Any]:
        """نمای سریال‌پذیر (بدون شیء رزرو)."""
        return {
            "valid": self.valid,
            "crypto_valid": self.crypto_valid,
            "mismatch": self.mismatch,
            "error": self.error,
            "payload": self.payload if self.crypto_valid else {},
            "reservation_status": (
                self.reservation.status if self.reservation is not None else None
            ),
            "checked_at": timezone.localtime().isoformat(),
        }


def verify_online(token: str) -> OnlineVerification:
    """تأیید توکن QR و تطبیق با رکورد فعلی رزرو (کد، واحد، تعداد نفرات)."""
    from apps.reservation import selectors  # واردکردن تنبل: گواهی بدون ORM هم کار می‌کند

    try:
        payload = verify_token(token)
    except CertificateError as exc:
        return OnlineVerification(
            crypto_valid=False, mismatch=False, error=str(exc), payload={}, reservation=None
        )
    reservation = None
    mismatch = False
    if payload.get("code"):
        reservation = selectors.reservation_by_code(str(payload["code"]))
        if reservation is None:
            mismatch = True
        else:
            current = {
                "code": reservation.code,
                "unit": reservation.unit.code,
                "guests": int(reservation.guests_count),
            }
            mismatch = any(str(payload.get(key)) != str(value) for key, value in current.items())
    return OnlineVerification(
        crypto_valid=True, mismatch=mismatch, error="", payload=payload, reservation=reservation
    )
