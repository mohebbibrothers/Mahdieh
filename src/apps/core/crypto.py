"""رمزنگاری میدانی و ایندکس کور — محافظت از دادهٔ حساس «در سکون».

تصمیم ADR-0004: کد ملی هرگز به‌صورت متن روشن ذخیره نمی‌شود. سه نیاز هم‌زمان:

    ۱. **محرمانگی** ⇒ AES-GCM با کلیدی خارج از کد (KMS/Vault در تولید).
       AES-GCM هم رمزنگاری و هم اصالت داده را تضمین می‌کند؛ دستکاری بدنه
       هنگام رمزگشایی «شکست» می‌دهد، نه دادهٔ خراب.
    ۲. **یکتایی و جست‌وجو** ⇒ ایندکس کور (HMAC-SHA256). هش سادهٔ کد ملی
       با دیکشنری معکوس (فقط ~۱۰^۱۰ حالت) شکسته می‌شود؛ HMAC با کلید
       محرمانه، جست‌وجو را ممکن و بازگردانی را ناممکن می‌کند.
    ۳. **چرخش کلید** ⇒ پیشوند نسخه در بدنهٔ رمزنگاری‌شده ذخیره می‌شود
       (``v1:<nonce>:<ciphertext>``) تا دادهٔ رمزنگاری‌شدهٔ قدیمی بی‌نیاز از
       رمزگشایی مجدد قابل تفکیک باشد و چرخش کلید بدون مهاجرت داده ممکن شود.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

KEY_VERSION = "v1"
_KEY_BYTES = 32
_NONCE_BYTES = 12
_TOKEN_PARTS = 3


def _encryption_key() -> bytes:
    """کلید ۳۲ بایتی AES-GCM را از تنظیمات می‌سازد.

    اگر مقدار تنظیم‌شده Base64 معتبر و ۳۲ بایتی باشد، همان استفاده می‌شود؛
    در غیر این صورت (کلید متنی توسعه) از SHA-256 عبور می‌کند تا کلید با طول
    درست و آنتروپی کامل ساخته شود. هیچ‌گاه مقدار خام در کد نمی‌ماند.
    """
    configured = str(getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "")
    if not configured:
        msg = "FIELD_ENCRYPTION_KEY تنظیم نشده است؛ رمزنگاری دادهٔ حساس ممکن نیست."
        raise ImproperlyConfigured(msg)
    try:
        decoded = base64.b64decode(configured, validate=True)
    except (binascii.Error, ValueError):
        decoded = b""
    if len(decoded) == _KEY_BYTES:
        return decoded
    return hashlib.sha256(configured.encode("utf-8")).digest()


def _hash_key() -> bytes:
    """کلید HMAC برای ایندکس کور (با پشتوانهٔ SECRET_KEY در نبود کلید اختصاصی)."""
    configured = str(getattr(settings, "FIELD_HASH_KEY", "") or "")
    if not configured:
        configured = str(settings.SECRET_KEY)
    return hashlib.sha256(configured.encode("utf-8")).digest()


def encrypt_value(plaintext: str | None) -> str | None:
    """رمزنگاری یک مقدار متنی؛ ورودی خالی ⇒ خروجی خالی (بدون هزینهٔ اضافه)."""
    if plaintext is None or plaintext == "":
        return plaintext
    aead = AESGCM(_encryption_key())
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = aead.encrypt(nonce, plaintext.encode("utf-8"), None)
    nonce_b64 = base64.b64encode(nonce).decode("ascii")
    body_b64 = base64.b64encode(ciphertext).decode("ascii")
    return f"{KEY_VERSION}:{nonce_b64}:{body_b64}"


def is_encrypted(value: str | None) -> bool:
    """آیا مقدار در قالب رمزنگاری‌شدهٔ این سامانه است؟"""
    if not value:
        return False
    parts = value.split(":")
    return len(parts) == _TOKEN_PARTS and parts[0] == KEY_VERSION


def decrypt_value(token: str | None, *, default: str = "") -> str:
    """رمزگشایی مقدار رمزنگاری‌شده.

    قاعدهٔ انتخاب‌شده: خرابی/دستکاری داده **باعث خطای صفحه نمی‌شود**؛ به‌جای
    آن، رویداد در لاگ ثبت و مقدار پیش‌فرض برگردانده می‌شود. دلیل: دادهٔ
    رمزنگاری‌شده ممکن است با کلید قدیمی نوشته شده باشد و نباید دسترسی کاربر
    به حساب خودش را ببندد — ولی سکوت هم ممنوع است، پس لاگ سطح ERROR.
    """
    if not token:
        return default
    if not is_encrypted(token):
        # دادهٔ قدیمی/متنی: مسیر مهاجرت تدریجی، بدون شکستن خواندن.
        return token
    _, nonce_b64, body_b64 = token.split(":", 2)
    try:
        nonce = base64.b64decode(nonce_b64)
        body = base64.b64decode(body_b64)
        aead = AESGCM(_encryption_key())
        return aead.decrypt(nonce, body, None).decode("utf-8")
    except Exception:
        logger.exception("field_decryption_failed")
        return default


def blind_index(value: str | None, *, namespace: str = "default") -> str:
    """ایندکس کور قابل‌جست‌وجو برای یک مقدار حساس (HMAC-SHA256).

    ``namespace`` باعث می‌شود یک مقدار یکسان در دو زمینهٔ مختلف دو ایندکس
    متفاوت بگیرد (جلوگیری از تطبیق بین‌داده‌ای).
    """
    if not value:
        return ""
    message = f"{namespace}:{value}".encode()
    return hmac.new(_hash_key(), message, hashlib.sha256).hexdigest()
