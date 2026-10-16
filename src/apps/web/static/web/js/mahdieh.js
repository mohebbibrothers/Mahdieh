/* ═══════════════════════════════════════════════════════════════════════════
   مهدیه — نقش‌افزار سبک رابط کاربری
   ───────────────────────────────────────────────────────────────────────────
   قاعده: هیچ کتابخانهٔ سنگین، هیچ درخواست شبکه‌ای خارجی. هرچه اینجا هست،
   «بهبود تدریجی» است: اگر اجرا نشود، سایت کامل و قابل استفاده می‌ماند.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  const FA_DIGITS = ["۰", "۱", "۲", "۳", "۴", "۵", "۶", "۷", "۸", "۹"];
  const MONTHS = [
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
  ];

  /** تبدیل ارقام لاتین به فارسی. */
  function fa(value) {
    return String(value).replace(/\d/g, (digit) => FA_DIGITS[Number(digit)]);
  }

  /* ── تبدیل تقویم میلادی به جلالی ─────────────────────────────────────────
     الگوریتم «jalaali-js» (خالد احمدی، پروانهٔ MIT) — همان الگوریتمی که سمت
     سرور با آن آزموده شده است. کپی سبک و بدون وابستگی، فقط برای نمایش.
     ────────────────────────────────────────────────────────────────────── */
  const BREAKS = [
    -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324,
    2394, 2456, 3178,
  ];

  function div(a, b) {
    return Math.trunc(a / b);
  }
  function mod(a, b) {
    return a - Math.trunc(a / b) * b;
  }

  function jalCal(jy, withoutLeap) {
    const bl = BREAKS.length;
    const gy = jy + 621;
    let leapJ = -14;
    let jp = BREAKS[0];
    let jump = 0;
    let leap = 0;
    let n;
    let i;
    if (jy < jp || jy >= BREAKS[bl - 1]) return null;
    for (i = 1; i < bl; i += 1) {
      const jm = BREAKS[i];
      jump = jm - jp;
      if (jy < jm) break;
      leapJ = leapJ + div(jump, 33) * 8 + div(mod(jump, 33), 4);
      jp = jm;
    }
    n = jy - jp;
    leapJ = leapJ + div(n, 33) * 8 + div(mod(n, 33) + 3, 4);
    if (mod(jump, 33) === 4 && jump - n === 4) leapJ += 1;
    const leapG = div(gy, 4) - div((div(gy, 100) + 1) * 3, 4) - 150;
    const march = 20 + leapJ - leapG;
    if (!withoutLeap) {
      if (jump - n < 6) n = n - jump + div(jump + 4, 33) * 33;
      leap = mod(mod(n + 1, 33) - 1, 4);
      if (leap === -1) leap = 4;
    }
    return { leap: leap, gy: gy, march: march };
  }

  function g2d(gy, gm, gd) {
    let d =
      div((gy + div(gm - 8, 6) + 100100) * 1461, 4) +
      div(153 * mod(gm + 9, 12) + 2, 5) +
      gd -
      34840408;
    d = d - div(div(gy + 100100 + div(gm - 8, 6), 100) * 3, 4) + 752;
    return d;
  }

  function d2g(jdn) {
    let j = 4 * jdn + 139361631;
    j = j + div(div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908;
    const i = div(mod(j, 1461), 4) * 5 + 308;
    const gd = div(mod(i, 153), 5) + 1;
    const gm = mod(div(i, 153), 12) + 1;
    const gy = div(j, 1461) - 100100 + div(8 - gm, 6);
    return { gy: gy, gm: gm, gd: gd };
  }

  function d2j(jdn) {
    const gy = d2g(jdn).gy;
    let jy = gy - 621;
    const r = jalCal(jy, false);
    if (!r) return null;
    const jdn1f = g2d(gy, 3, r.march);
    let k = jdn - jdn1f;
    let jm;
    let jd;
    if (k >= 0) {
      if (k <= 185) {
        jm = 1 + div(k, 31);
        jd = mod(k, 31) + 1;
        return { jy: jy, jm: jm, jd: jd };
      }
      k -= 186;
    } else {
      jy -= 1;
      k += 179;
      if (r.leap === 1) k += 1;
    }
    jm = 7 + div(k, 30);
    jd = mod(k, 30) + 1;
    return { jy: jy, jm: jm, jd: jd };
  }

  function toJalali(gy, gm, gd) {
    return d2j(g2d(gy, gm, gd));
  }

  /** برچسب فارسی یک تاریخ میلادی: «۱۴۰۵/۰۷/۲۳ — ۲۳ مهر ۱۴۰۵». */
  function jalaliLabel(isoDate) {
    if (!isoDate) return "";
    const parts = isoDate.split("-").map(Number);
    if (parts.length !== 3 || parts.some((value) => Number.isNaN(value))) return "";
    const jalali = toJalali(parts[0], parts[1], parts[2]);
    if (!jalali) return "";
    const numeric =
      fa(jalali.jy) +
      "/" +
      fa(String(jalali.jm).padStart(2, "0")) +
      "/" +
      fa(String(jalali.jd).padStart(2, "0"));
    return numeric + " — " + fa(jalali.jd) + " " + MONTHS[jalali.jm - 1] + " " + fa(jalali.jy);
  }

  /** نمایش برچسب جلالی زیر هر ورودی تاریخ/زمان داخل فرم. */
  function mountDateHints(root) {
    (root || document).querySelectorAll("input[type='date']").forEach((input) => {
      if (input.dataset.hintMounted === "1") return;
      input.dataset.hintMounted = "1";
      const hint = document.createElement("span");
      hint.className = "field__hint";
      hint.setAttribute("data-jalali-hint", "1");
      const render = () => {
        hint.textContent = jalaliLabel(input.value);
      };
      input.insertAdjacentElement("afterend", hint);
      input.addEventListener("change", render);
      input.addEventListener("input", render);
      render();
    });
  }

  /** کپی متن در کلیپ‌بورد با بازخورد کوتاه — برای کد پیگیری. */
  function mountCopyButtons(root) {
    (root || document).querySelectorAll("[data-copy]").forEach((button) => {
      if (button.dataset.copyMounted === "1") return;
      button.dataset.copyMounted = "1";
      button.addEventListener("click", () => {
        const value = button.getAttribute("data-copy") || "";
        const original = button.textContent;
        const done = () => {
          button.textContent = "کپی شد ✓";
          setTimeout(() => {
            button.textContent = original;
          }, 1600);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(value).then(done, done);
        } else {
          done();
        }
      });
    });
  }

  /** شمارش معکوس تا ارسال دوبارهٔ کد. */
  function mountCountdowns(root) {
    (root || document).querySelectorAll("[data-countdown]").forEach((node) => {
      if (node.dataset.countdownMounted === "1") return;
      node.dataset.countdownMounted = "1";
      let remaining = Number(node.getAttribute("data-countdown")) || 0;
      const label = node.querySelector("[data-countdown-label]") || node;
      const tick = () => {
        if (remaining <= 0) {
          label.textContent = "می‌توانید کد تازه بگیرید";
          node.classList.remove("muted");
          return;
        }
        label.textContent = "ارسال دوباره تا " + fa(remaining) + " ثانیه";
        remaining -= 1;
        setTimeout(tick, 1000);
      };
      tick();
    });
  }

  /** کد یک‌بارمصرف: تمرکز خودکار و ارسال در پنج رقم. */
  function mountOtpInputs(root) {
    (root || document).querySelectorAll("input.otp-input").forEach((input) => {
      if (input.dataset.otpMounted === "1") return;
      input.dataset.otpMounted = "1";
      input.setAttribute("autocomplete", "one-time-code");
      const form = input.closest("form");
      input.addEventListener("input", () => {
        const digits = input.value.replace(/\D/g, "").slice(0, 5);
        input.value = digits;
        if (digits.length === 5 && form) {
          form.requestSubmit ? form.requestSubmit() : form.submit();
        }
      });
    });
  }

  /** پیکربندی HTMX: هدر CSRF، نشانگر بارگذاری و بازخورد خطا. */
  function configureHtmx() {
    if (!window.htmx) return;
    document.body.addEventListener("htmx:configRequest", (event) => {
      const token = document.body.getAttribute("data-csrf") || "";
      if (token) event.detail.headers["X-CSRFToken"] = token;
    });
    document.body.addEventListener("htmx:afterSwap", (event) => {
      enhance(event.detail.target);
    });
    document.body.addEventListener("htmx:responseError", (event) => {
      const target = event.detail.target;
      if (target) {
        target.innerHTML =
          '<div class="flash flash--error">ارتباط با سرور برقرار نشد؛ یک بار دیگر تلاش کنید.</div>';
      }
    });
  }

  /** تقویت تدریجی همهٔ اجزای صفحه. */
  function enhance(root) {
    mountDateHints(root);
    mountCopyButtons(root);
    mountCountdowns(root);
    mountOtpInputs(root);
  }

  document.addEventListener("DOMContentLoaded", () => {
    configureHtmx();
    enhance(document);
  });

  window.Mahdieh = {
    fa: fa,
    jalaliLabel: jalaliLabel,
    enhance: enhance,
  };
})();
