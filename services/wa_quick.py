"""
واتساب سريع (عام): رقم هاتف + أي رسالة، بلا ما تكون مربوطة بطلبية.

يخدم من خانة «💬 واتساب سريع» فلوحة «نظرة عامة». نفس فلسفة أزرار الطلبيات:
نبنيو رابط wa.me برسالة جاهزة، والأدمن هو اللي يضغط «إرسال» فتطبيق واتساب.
(الإرسال التلقائي بالـAPI ما نستعملوهش هنا: Meta ما تسمح بنص حرّ للزبون
 إلا فـ24 ساعة من رسالتو، وخارجها لازم نماذج معتمدة.)

⚠️ الإيموجي الملوّن: رابط wa.me يقطعو ويبان «◆». علاه نرفضو الإيموجي الملوّن
   وندّيو الأدمن للرموز النصّية (✓ ✿ ★ ✱ ...) — نفس قاعدة wa_templates.
"""
import re
from urllib.parse import quote

from services import wa_templates as wa

MAX_LEN = wa.MAX_LEN

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# سلام {name}، — إذا ما كاينش اسم نحيّو {name} وتبقى «سلام،»
_HELLO_WITH_NAME = "سلام {name}،\nمعاك {store}."
_HELLO_NO_NAME = "سلام،\nمعاك {store}."

# نماذج عامة (ماشي خاصة بطلبية). {hello} = التحية، {link} = رابط المتجر.
PRESETS = [
    {"key": "welcome", "label": "ترحيب", "text":
        "{hello}\n\nمرحبا بيك عندنا. قولّي واش تحتاج ونعاونك بكل سرور.\n\nمتجرنا: {link}"},
    {"key": "thanks", "label": "شكر على الثقة", "text":
        "{hello}\n\nشكرا على ثقتك فينا {heart}\nكي تحتاج أي حاجة، كتبلنا هنا ونردّو عليك."},
    {"key": "inquiry", "label": "رد على استفسار", "text":
        "{hello}\n\nشكرا على سؤالك {write}\n\n(اكتب الجواب هنا)\n\nكي تحب نكمّلو الطلب، قولّي ونعمّرو معاك المعلومات."},
    {"key": "new", "label": "منتجات جديدة", "text":
        "{hello}\n\nوصلتنا منتجات جديدة {sparkles}\nشوفها هنا: {link}"},
    {"key": "promo", "label": "عرض / تخفيض", "text":
        "{hello}\n\nعندنا عروض تستاهل تشوفها {star}\n{link}\n\nكي يعجبك شي حاجة، ابعثلي اسمها ونكمّلو الطلب."},
    {"key": "howto", "label": "كيفاش نطلب", "text":
        "{hello}\n\nباش تطلب:\n{check} اختار المنتج من {link}\n{check} عمّر الاسم والهاتف والولاية\n{check} نتصلو بيك نأكّدو الطلبية"},
    {"key": "callback", "label": "نتصل بيك", "text":
        "{hello}\n\n{phone} نحبّو نتصلو بيك — وقتاش يريحك؟"},
    {"key": "delay", "label": "اعتذار عن التأخير", "text":
        "{hello}\n\n{warn} نعتذرو على التأخير. راك مهم عندنا ونخدمو باش نعوّضوهالك."},
    {"key": "opinion", "label": "طلب رأي", "text":
        "{hello}\n\nرأيك يهمّنا {heart}\nجاوبني بكلمة وحدة: كيفاش لقيت خدمتنا؟"},
]


# ───────────────────────── الرقم ─────────────────────────
def parse_phone(raw):
    """
    رقم مكتوب بأي طريقة ← (رقم دولي بلا «+»، رسالة خطأ).
    يفهم: 0555123456 · 555123456 · +213 555 12 34 56 · 00213555123456 · 213555123456
    وأرقام عربية (٠٥٥٥...). الرقم الأجنبي (+33...) يتقبّل كيما هو.
    """
    text = str(raw or "").translate(_AR_DIGITS).strip()
    if not text:
        return None, "اكتب رقم الهاتف"
    international = text.startswith("+") or text.startswith("00")
    digits = re.sub(r"\D", "", text)
    if text.startswith("00"):
        digits = digits[2:]
    if not digits:
        return None, "الرقم غير صالح"

    if digits.startswith("213") and (international or len(digits) == 12):
        national = digits[3:]
        if national.startswith("0"):          # «+213 0555...» غلطة شائعة
            national = national[1:]
    elif international:
        # رقم أجنبي: نخلّيوه كيما هو بشرط الطول المعقول
        if 8 <= len(digits) <= 15:
            return digits, ""
        return None, "الرقم الدولي غير صالح (الطول غالط)"
    elif digits.startswith("0") and len(digits) == 10:
        national = digits[1:]
    elif len(digits) == 9:
        national = digits
    else:
        return None, "الرقم غير صالح — اكتبو بصيغة 0555123456 ولا +213555123456"

    if len(national) != 9:
        return None, "الرقم الجزائري لازم 9 أرقام بعد الصفر (مثلا 0555123456)"
    if national[0] not in "567":
        return None, "هذا رقم ثابت ماشي موبايل — واتساب ما يخدمش عليه"
    return "213" + national, ""


def pretty_phone(intl):
    """213555123456 ← 0555 12 34 56 (للعرض برك)."""
    if intl.startswith("213") and len(intl) == 12:
        n = "0" + intl[3:]
        return f"{n[:4]} {n[4:6]} {n[6:8]} {n[8:]}"
    return "+" + intl


# ───────────────────────── النص ─────────────────────────
def check_text(text):
    """يرجّع رسالة خطأ ولا '' إذا النص صالح."""
    text = (text or "").strip()
    if not text:
        return "اكتب الرسالة"
    if len(text) > MAX_LEN:
        return f"الرسالة طويلة بزاف ({len(text)} حرف) — الحد {MAX_LEN}."
    bad = sorted({c for c in text if ord(c) > 0xFFFF or c in wa._RISKY_CHARS})
    if bad:
        return ("فيها إيموجي ملوّن (" + " ".join(bad[:6]) + ") — رابط واتساب يقطعو ويبان «◆». "
                "استعمل الرموز النصّية اللي تحت الخانة (✓ ✿ ★ ...).")
    return ""


def render_message(text, name="", link=""):
    """يعوّض {name} {store} {link} {hello} والرموز {check}... ويخلّي المجهول كيما هو."""
    name = (name or "").strip()
    hello = (_HELLO_WITH_NAME if name else _HELLO_NO_NAME)
    ctx = {"name": name, "store": wa.BRAND, "link": link, "hello": hello}
    # {hello} نفسو فيه {name} و{store} — نعوّضو مرتين
    first = wa.render_text(text, ctx)
    out = wa.render_text(first, ctx)
    if not name:
        out = out.replace("سلام ،", "سلام،")
    return out


def prepare(phone, text, name="", link=""):
    """
    (ok, payload). ok=True ← {"url", "phone", "phone_display", "text"}.
    ok=False ← {"error"}.
    """
    intl, err = parse_phone(phone)
    if err:
        return False, {"error": err}
    message = render_message(text, name, link)
    err = check_text(message)
    if err:
        return False, {"error": err}
    return True, {
        "url": f"https://wa.me/{intl}?text={quote(message, safe='')}",
        "phone": intl,
        "phone_display": pretty_phone(intl),
        "text": message,
    }


# ───────────────────────── للواجهة ─────────────────────────
def ui_context(link=""):
    """نماذج جاهزة (الرموز والمتجر والرابط معوّضين، وتبقى {hello} للاسم) + شرائح الرموز."""
    presets = []
    for p in PRESETS:
        ctx = {"store": wa.BRAND, "link": link}
        text = wa.render_text(p["text"], ctx)   # {hello} يبقى كيما هو (مجهول)
        presets.append({"key": p["key"], "label": p["label"], "text": text})
    symbols = [{"char": label[0], "label": label[1:].strip()} for label in wa.SYMBOL_HELP.values()]
    return {
        "presets": presets,
        "symbols": symbols,
        "hello_with_name": _HELLO_WITH_NAME.replace("{store}", wa.BRAND),
        "hello_no_name": _HELLO_NO_NAME.replace("{store}", wa.BRAND),
        "max_len": MAX_LEN,
    }
