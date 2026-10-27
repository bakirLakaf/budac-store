"""
نماذج رسائل واتساب — الأدمن يعدّلها من تبويب «رسائل واتساب».

كيفاش تخدم؟
  • لكل نوع رسالة (تأكيد، شحن، شكر...) نص افتراضي هنا (DEFAULTS).
  • الأدمن يقدر يبدّل النص فالقاعدة (StoreSetting، المفتاح wa_tpl_<kind>).
    إذا ما بدّلش، نستعملو الافتراضي. وزر «رجّع الأصلي» يمسح تعديلو.
  • النص فيه متغيّرات بين قوسين، مثلاً {name} و{order_number}، تتعوّض
    وقت الإرسال ببيانات الطلبية.

⚠️ ما نستعملوش str.format() عمدًا: الأدمن يكتب النص، وخطأ صغير فقوس
   ({ ولا }) كان يوقّف الإرسال كامل (KeyError). دروك نعوّضو غير المتغيّرات
   المعروفة، وأي حاجة أخرى تبقى كيما هي.

⚠️ الإيموجي: رابط wa.me يقطع الإيموجي الملوّن (4 بايت) ويبان «◆». علاه
   الأدمن يستعمل رموز نصّية بين قوسين: {check} {heart} {star}... وكل
   حفظ يرفض الإيموجي الملوّن بدل ما يتخرّب عند الزبون.
"""

import re

# اسم المتجر فرسائل الواتساب: ديما «معاك BUDAC» (طلب صاحب المتجر)،
# مهما كان STORE_NAME فالإعدادات.
BRAND = "BUDAC"

# أقصى طول للنموذج: رابط wa.me الطويل بزاف (العربية تتضاعف 6 مرات
# فالرابط بعد الترميز) وبعض المتصفحات تقطعو.
MAX_LEN = 1200

# ───────────────────────── الرموز النصّية ─────────────────────────
# نفس `Order.EMOJI` (3 بايت، بلا نسخة إيموجي) — شوف الشرح فـmodels.py.
SYMBOL_HELP = {
    "check": "✓ صح",
    "box": "▪ طلبية",
    "truck": "➤ فالطريق",
    "pin": "⚑ عنوان",
    "phone": "✆ اتصال",
    "clock": "» وقت",
    "money": "◉ مبلغ",
    "heart": "✿ شكر",
    "star": "★ نجمة",
    "warn": "✱ تنبيه",
    "back": "« رجوع",
    "sparkles": "✶ لمعة",
    "write": "✎ كتابة",
}

# ───────────────────────── المتغيّرات ─────────────────────────
# key → (وصف عربي، مثال للمعاينة)
VARIABLES = {
    "name": ("اسم الزبون", "أمين"),
    "store": ("اسم المتجر (BUDAC)", BRAND),
    "order_number": ("رقم الطلبية", "BD-12345678"),
    "items": ("قائمة المنتجات (سطر لكل منتج)", "• مقبض ذهبي × 2\n• شريط كريستال × 1"),
    "total": ("المجموع مع التوصيل", "2,590 دج"),
    "delivery": ("نوع التوصيل والولاية", "توصيل للمنزل - الجزائر"),
    "wilaya": ("الولاية", "الجزائر"),
    "carrier": ("شركة التوصيل", "ZR Express"),
    "tracking_line": ("سطر رقم التتبّع (يختفي إذا ما كاينش)", "\nرقم التتبّع: *ZR-123456*"),
    "where": ("مكان الاستلام (المكتب ولا الولاية)", "مكتب الجزائر وسط"),
    "link": ("الرابط (تقييم / المتجر حسب الرسالة)", "https://budacdz.com"),
    "track_link": ("رابط تتبّع الطلبية الخاص (يفتح مباشرة بلا رقم ولا هاتف)", "https://budacdz.com/t/AbC123xyz"),
    "code": ("كود الكوبون", "AM-7K2QX"),
    "discount": ("قيمة الخصم", "10%"),
    "min_line": ("شرط الحد الأدنى (يختفي إذا ما كاينش)", " (لطلبية قيمتها *5,000 دج* فما فوق، بلا التوصيل)"),
    "until_line": ("سطر الصلاحية (يختفي إذا بلا انتهاء)", "\n» صالح حتى *31/10/2026*"),
    "cart_items": ("المنتجات اللي تركها فالسلة", "مقبض ذهبي، شريط كريستال"),
}

_GREETING = "سلام {name}،\nمعاك {store}.\n\n"

# ───────────────────────── الأنواع والنصوص الافتراضية ─────────────────────────
# كل نوع: label (عنوان)، when (متى تستعملها)، vars (المتغيّرات المناسبة)،
# required (لازم تبقى فالنص)، default (النص الأصلي).
KINDS = [
    {
        "key": "confirm",
        "label": "تأكيد الطلبية",
        "when": "كي توصلك طلبية جديدة وتحب تأكّدها مع الزبون",
        "vars": ["name", "order_number", "items", "total", "delivery"],
        "required": [],
        "default": (
            _GREETING +
            "{box} وصلتنا طلبيتك رقم *{order_number}*:\n\n"
            "{items}\n\n"
            "{money} المجموع مع التوصيل: *{total}*\n"
            "{pin} {delivery}\n\n"
            "نأكّدو الطلبية؟"
        ),
    },
    {
        "key": "modify",
        "label": "تعديل الطلبية",
        "when": "كي تحب تسقسي الزبون واش يزيد ولا ينقّص شي حاجة",
        "vars": ["name", "order_number", "items", "total"],
        "required": [],
        "default": (
            _GREETING +
            "{box} بخصوص طلبيتك *{order_number}*:\n\n"
            "{items}\n\n"
            "{money} المجموع: *{total}*\n\n"
            "حبيت نسقسيك - تحب تزيد ولا تنقّص شي حاجة قبل ما نبعثوها؟"
        ),
    },
    {
        "key": "noreply",
        "label": "ما جاوبش على الهاتف",
        "when": "كي تتصل بالزبون وما يردّش",
        "vars": ["name", "order_number"],
        "required": [],
        "default": (
            _GREETING +
            "{phone} حاولنا نتصلو بيك على طلبيتك *{order_number}* وما لقيناكش.\n\n"
            "قولّي وقتاش يريحك ونعاودو نتصلو، ولا جاوبني هنا ونأكّدوها مباشرة."
        ),
    },
    {
        "key": "missing",
        "label": "منتج ما بقاش موجود",
        "when": "كي منتج من الطلبية ينفد",
        "vars": ["name", "order_number"],
        "required": [],
        "default": (
            _GREETING +
            "{warn} بخصوص طلبيتك *{order_number}* - نعتذرلك، كاين منتج ما بقاش موجود درك.\n\n"
            "عندك خيارين:\n"
            "1. نبدّلوه بحاجة أخرى تعجبك\n"
            "2. نبعثو الباقي ونحيّدو ثمنو من المجموع\n\n"
            "واش تفضّل؟"
        ),
    },
    {
        "key": "shipped",
        "label": "الطلبية خرجت (شحن)",
        "when": "كي تبعث الطلبية لشركة التوصيل",
        "vars": ["name", "order_number", "carrier", "tracking_line", "total", "track_link"],
        "required": [],
        "default": (
            _GREETING +
            "{truck} طلبيتك *{order_number}* خرجت اليوم مع {carrier}، بإذن الله.{tracking_line}\n\n"
            "{clock} توصلك فحدود 24 لـ48 ساعة، خلّي هاتفك مفتوح باش يتصل بيك الموزّع.\n\n"
            "{money} المبلغ عند الاستلام: *{total}*\n\n"
            "{box} تتبّع طلبيتك مباشرة: {track_link}"
        ),
    },
    {
        "key": "arrived",
        "label": "وصلت لمكتب الاستلام",
        "when": "كي الطلبية توصل للمكتب ويقدر الزبون يهزّها",
        "vars": ["name", "order_number", "where", "total", "track_link"],
        "required": [],
        "default": (
            _GREETING +
            "{check} طلبيتك *{order_number}* وصلت لـ*{where}*.\n\n"
            "تقدر تروح تهزّها - جيب معاك بطاقة التعريف.\n"
            "{money} المبلغ: *{total}*\n\n"
            "{box} تفاصيل الطلبية: {track_link}\n\n"
            "نستنّاوك."
        ),
    },
    {
        "key": "returned",
        "label": "الطرد رجع",
        "when": "كي الطرد يرجع لينا بلا ما يتسلّم (رسالة حسّاسة — بلا لوم)",
        "vars": ["name", "order_number"],
        "required": [],
        "default": (
            _GREETING +
            "{back} طلبيتك *{order_number}* رجعت لينا بلا ما تتسلّم.\n\n"
            "إذا مازلت حاب تاخدها، قولّي ونعاودو نبعثوها - نتأكدو برك من العنوان والوقت المناسب.\n\n"
            "وإذا بدّلت رأيك، ما كاين والو، عرّفني برك."
        ),
    },
    {
        "key": "thanks",
        "label": "شكر بعد التسليم",
        "when": "بعد ما الزبون يهزّ طلبيتو",
        "vars": ["name", "store"],
        "required": [],
        "default": (
            _GREETING +
            "{heart} يعطيك الصحة على ثقتك فينا، ونتمنى المنتج عجبك.\n\n"
            "إذا عندك أي ملاحظة، قولّي - رأيك يهمّنا.\n\n"
            "ونفرحو بيك مرة أخرى فـ{store}."
        ),
    },
    {
        "key": "review",
        "label": "طلب تقييم",
        "when": "باش تطلب من الزبون يكتب رأيو فالمتجر ({link} = صفحة التقييمات)",
        "vars": ["name", "order_number", "link"],
        "required": ["link"],
        "default": (
            _GREETING +
            "{check} طلبيتك رقم *{order_number}* وصلتك، ونتمنى تكون كيما تستنّى.\n\n"
            "{star} إذا عندك دقيقة، رأيك على المنتج والخدمة يهمّنا - يفيد الزبائن الجدد ويعاوننا نتحسّنو:\n"
            "{link}\n\n"
            "وإذا كان عندك شي ملاحظة، ابعثهالي هنا مباشرة ونتكفّلو بيها.\n\n"
            "{heart} شكرًا على ثقتك."
        ),
    },
    {
        "key": "reward",
        "label": "كوبون مكافأة",
        "when": "كوبون خصم شخصي للزبون اللي وصلتو طلبيتو ({link} = رابط المتجر)",
        "vars": ["name", "order_number", "discount", "min_line", "code", "until_line", "link"],
        "required": ["code"],
        "default": (
            _GREETING +
            "{heart} طلبيتك *{order_number}* وصلتك، ونتمنى عجبتك!\n\n"
            "حبينا نكافيوك على ثقتك فينا {sparkles}\n"
            "عندك خصم *{discount}* تاعك وحدك على طلبيتك الجاية{min_line}.\n\n"
            "{star} الكود تاعك: *{code}*\n"
            "{check} يخدم مرة وحدة برك، وبهاد الرقم تاعك{until_line}\n\n"
            "كي تحب تطلب، اكتب الكود فالسلة قبل ما تأكّد الطلبية:\n"
            "{link}\n\n"
            "بالصحة والراحة، ونستناوك مرة أخرى {heart}"
        ),
    },
    {
        "key": "abandoned",
        "label": "سلة متروكة",
        "when": "كي زبون يترك سلتو بلا ما يكمّل الطلب",
        "vars": ["name", "cart_items"],
        "required": [],
        "default": (
            _GREETING +
            "لاحظنا بلي كنت قريب تكمّل طلبيتك: {cart_items}.\n"
            "واش تحتاج مساعدة باش تكمّلها؟"
        ),
    },
]

KIND_BY_KEY = {k["key"]: k for k in KINDS}

# إيموجي بنسخة ملوّنة فالواتساب — كانت تتخرّب (شوف models.Order.EMOJI)
_RISKY_CHARS = set("✅⭐❤⚠✔☑⏰⌚☎✉✈☀☁⚡♥➡↩⏳⌛✨❗❓‼")

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


# ───────────────────────── التخزين ─────────────────────────

def _setting_key(kind):
    return f"wa_tpl_{kind}"


def get_template(kind):
    """النص الحالي لنوع رسالة: تعديل الأدمن، وإلا الافتراضي."""
    from models import StoreSetting

    spec = KIND_BY_KEY.get(kind)
    if spec is None:
        return None
    custom = StoreSetting.get(_setting_key(kind))
    if custom is not None and custom.strip():
        return custom
    return spec["default"]


def is_customized(kind):
    from models import StoreSetting

    value = StoreSetting.get(_setting_key(kind))
    return bool(value and value.strip())


def validate_template(kind, text):
    """يرجع رسالة خطأ بالعربية، أو None إذا النص مقبول."""
    spec = KIND_BY_KEY.get(kind)
    if spec is None:
        return "نوع رسالة غير معروف"

    text = (text or "").strip()
    if not text:
        return "النص فارغ — إذا حبيت الأصلي اضغط «رجّع الأصلي»"
    if len(text) > MAX_LEN:
        return f"النص طويل بزاف ({len(text)} حرف) — الحد {MAX_LEN}"

    for ch in text:
        if ord(ch) > 0xFFFF or ch == "\ufe0f" or ch in _RISKY_CHARS:
            return (
                "فيه إيموجي ملوّن — يتخرّب فواتساب ويبان «◆» عند الزبون. "
                "استعمل الرموز النصّية بين قوسين، مثلاً {heart} {check} {star}"
            )

    for needed in spec["required"]:
        if "{" + needed + "}" not in text:
            desc = VARIABLES.get(needed, (needed,))[0]
            return f"لازم يبقى فالنص «{{{needed}}}» ({desc}) — بلاه الرسالة ما تفيدش"
    return None


def save_template(kind, text):
    """يحفظ تعديل الأدمن (بلا commit). يرفع ValueError برسالة عربية."""
    from models import StoreSetting

    error = validate_template(kind, text)
    if error:
        raise ValueError(error)
    text = text.replace("\r\n", "\n").strip()
    if text == KIND_BY_KEY[kind]["default"]:
        reset_template(kind)       # نفس الأصلي — ما نخزّنوش نسخة زايدة
        return
    StoreSetting.set(_setting_key(kind), text)


def reset_template(kind):
    """يمسح تعديل الأدمن ← يرجع الأصلي (بلا commit)."""
    from models import StoreSetting, db

    row = StoreSetting.query.filter_by(key=_setting_key(kind)).first()
    if row is not None:
        db.session.delete(row)


# ───────────────────────── العرض (rendering) ─────────────────────────

def _symbols():
    from models import Order
    return dict(Order.EMOJI)


def render_text(text, context):
    """
    يعوّض {المتغيّرات} فالنص. المجهول يبقى كيما هو (ما يطيّحش الإرسال).
    ثم ينظّف: أكثر من سطرين فارغين متتاليين يولّيو سطر فارغ وحد.
    """
    ctx = {**_symbols(), **context}

    def replace(match):
        key = match.group(1)
        return str(ctx[key]) if key in ctx else match.group(0)

    result = _PLACEHOLDER.sub(replace, text)
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def sample_context():
    """بيانات تجريبية للمعاينة فصفحة الأدمن."""
    return {key: sample for key, (_desc, sample) in VARIABLES.items()}


def _track_link(order, site):
    """رابط التتبّع الخاص. يحفظ التوكن فالقاعدة إذا الطلبية قديمة وما عندهاش."""
    from models import db

    had_token = bool(order.track_token)
    link = order.track_url(site)
    if not had_token:
        try:
            db.session.commit()
        except Exception:   # noqa: BLE001 — الرسالة تتبعث، والتوكن يتعمّر مع startup
            db.session.rollback()
    return link


def render_for_order(order, kind, base_url="", coupon=None):
    """
    رسالة جاهزة لطلبية. `coupon` ضروري لنوع reward.
    نوع مجهول ← رسالة عامة (أحسن من خطأ).
    """
    site = base_url.rstrip("/") if base_url else "https://budacdz.com"
    text = get_template(kind)

    tracking = order.tracking_number
    ctx = {
        "store": BRAND,
        "name": order.customer_name or "خويا",
        "order_number": order.order_number,
        # ⚠️ روابط المنتجات تتزاد غير إذا عطيناه base_url (نفس السلوك القديم)
        "items": order._items_lines(with_links=bool(base_url), base_url=base_url),
        "total": order.total_display,
        "delivery": f"{order.delivery_type_label} - {order.wilaya_name}",
        "wilaya": order.wilaya_name,
        "carrier": order.carrier_name or "شركة التوصيل",
        "tracking_line": f"\nرقم التتبّع: *{tracking}*" if tracking else "",
        "where": order.pickup_point_name or order.wilaya_name,
        # ⚠️ الواتساب ما يخدمش رابطًا نسبيًا — دايمًا رابط كامل
        "link": f"{site}/reviews" if kind == "review" else site,
        "track_link": _track_link(order, site),
    }

    if coupon is not None:
        symbols = _symbols()
        ctx["code"] = coupon.code
        ctx["discount"] = coupon.value_display
        ctx["until_line"] = (
            f"\n{symbols['clock']} صالح حتى *{coupon.ends_at.strftime('%d/%m/%Y')}*"
            if coupon.ends_at else ""
        )
        ctx["min_line"] = (
            f" (لطلبية قيمتها *{coupon.min_order_amount:,} دج* فما فوق، بلا التوصيل)"
            if coupon.min_order_amount else ""
        )

    if text is None:
        return render_text(_GREETING + "بخصوص طلبيتك *{order_number}*...", ctx)
    return render_text(text, ctx)


def render_for_abandoned_cart(cart):
    """رسالة السلة المتروكة."""
    names = "، ".join(item.get("name", "") for item in cart.items[:3])
    ctx = {
        "store": BRAND,
        "name": cart.customer_name or "خويا",
        "cart_items": names,
    }
    return render_text(get_template("abandoned"), ctx)
