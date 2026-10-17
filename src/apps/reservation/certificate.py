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
import hashlib
from typing import Any, Final

from django.core import signing
from django.utils import timezone

from apps.core.jalali import clock_label, format_jalali
from apps.core.site import absolute_url

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


def _short_code(payload: dict[str, Any]) -> str:
    """کد کوتاه آفلاین — از هش پایدار محتوای گواهی.

    روش: کلیدها مرتب و مقادیر نرمال‌شده به رشته تبدیل می‌شوند؛ سپس SHA-256 و
    نمایش پایه-۳۲ با الفبای بی‌ابهام. خودِ محتوا در هش می‌آید، پس با تغییر هر
    فیلد روی کاغذ، کد عوض می‌شود و جعل آشکار می‌گردد.
    """
    canonical = "|".join(f"{key}={payload[key]}" for key in sorted(payload))
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
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
        "code": code.strip().upper(),
        "holder": holder.strip(),
        "unit": unit.strip(),
        "floor": int(floor),
        "guests": int(guests),
        "starts": starts.strip(),
        "ends": ends.strip(),
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
