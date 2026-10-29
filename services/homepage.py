"""
لوحة تحكم الصفحة الرئيسية: ترتيب الأقسام + إظهارها/إخفاؤها + خيارات كل قسم.

التخزين: StoreSetting["home_layout"] = JSON:
    [{"key": "hero", "enabled": true, "options": {}}, ...]

⚠️ القواعد (كلها لحماية الصفحة من إعداد غالط):
  • قائمة الأقسام ثابتة فالكود (SECTIONS). المخزّن يحدّد غير الترتيب والتفعيل والخيارات.
  • قسم جديد يتزاد فالكود مستقبلاً ← يبان وحدو فآخر القائمة عند كل التجّار (بلا هجرة).
  • قسم مخزّن ما بقاش فالكود ← يتجاهل.
  • JSON خاسر ← نرجعو للافتراضي (الصفحة ما تطيحش أبدًا).
  • `catalog` (شبكة المنتجات) ما ينخبّاش أبدًا: متجر بلا منتجات = صفحة فارغة.
  • الخيارات تتنظّف: عدد بين MIN و MAX، وضع من القائمة المسموحة برك.
"""

import json
import logging

from models import StoreSetting

logger = logging.getLogger(__name__)

SETTING_KEY = "home_layout"

# options: لكل قسم، الخيارات المتاحة. strip = شريط منتجات (الأكثر طلبًا / وصل حديثًا)
_STRIP_DEFAULTS = {"count": 6, "mode": "slider", "autoplay": True}

SECTIONS = (
    {"key": "hero", "label": "سلايدر العروض (أول الصفحة)",
     "hint": "العروض الخاصة اللي معلّم عليها «فوق». شكلها يتحدد فأدمن العروض.",
     "default_on": True, "can_hide": True, "options": {}},
    {"key": "trust", "label": "شريط الثقة",
     "hint": "توصيل 58 ولاية · الدفع عند الاستلام · 24/48 ساعة · جودة مضمونة",
     "default_on": True, "can_hide": True, "options": {}},
    {"key": "carriers", "label": "شركات التوصيل",
     "hint": "شعارات شركات التوصيل المتاحة.",
     "default_on": True, "can_hide": True, "options": {}},
    {"key": "testimonials", "label": "آراء الزبائن",
     "hint": "سلايدر تقييمات الزبائن المقبولة.",
     "default_on": True, "can_hide": True, "options": {}},
    {"key": "offers_cards", "label": "عروض خاصة (بطاقات)",
     "hint": "العروض اللي معلّم عليها «وسط».",
     "default_on": True, "can_hide": True, "options": {}},
    {"key": "best_sellers", "label": "الأكثر طلبًا",
     "hint": "أكثر المنتجات مبيعًا.",
     "default_on": True, "can_hide": True, "options": dict(_STRIP_DEFAULTS)},
    {"key": "newest", "label": "وصل حديثًا",
     "hint": "آخر المنتجات المضافة.",
     "default_on": True, "can_hide": True, "options": dict(_STRIP_DEFAULTS)},
    {"key": "categories", "label": "التصنيفات (موبايل)",
     "hint": "بطاقات التصنيفات فوق الشبكة. فالحاسوب التصنيفات فالشريط الجانبي.",
     "default_on": True, "can_hide": True, "options": {}},
    {"key": "recently_viewed", "label": "شفتها مؤخرًا",
     "hint": "منتجات شافها الزائر (من جهازو).",
     "default_on": False, "can_hide": True, "options": {}},
    {"key": "catalog", "label": "شبكة المنتجات",
     "hint": "كل المنتجات مع التصنيفات والترقيم. ما تتخباش.",
     "default_on": True, "can_hide": False, "options": {}},
    {"key": "spotlight", "label": "القسم الكبير «عرض خاص»",
     "hint": "العرض اللي مختار له «تحت الصفحة». (اللي «فوق» يبقى أول الصفحة.)",
     "default_on": True, "can_hide": True, "options": {}},
)

_BY_KEY = {s["key"]: s for s in SECTIONS}
COUNT_MIN, COUNT_MAX = 3, 12
MODES = ("slider", "grid")


def _clean_options(spec, raw):
    """يرجّع خيارات صالحة فقط (ما نثقوش فالمخزّن ولا فالفورم)."""
    opts = dict(spec["options"])
    if not opts:
        return {}
    raw = raw if isinstance(raw, dict) else {}
    try:
        count = int(raw.get("count", opts["count"]))
    except (TypeError, ValueError):
        count = opts["count"]
    opts["count"] = max(COUNT_MIN, min(COUNT_MAX, count))
    mode = raw.get("mode", opts["mode"])
    opts["mode"] = mode if mode in MODES else opts["mode"]
    autoplay = raw.get("autoplay", opts["autoplay"])
    opts["autoplay"] = bool(autoplay)
    return opts


def default_layout():
    return [{"key": s["key"], "enabled": s["default_on"],
             "options": _clean_options(s, {})} for s in SECTIONS]


def get_layout():
    """الترتيب الكامل (المفعّلة وغيرها) — للوحة التحكم."""
    raw = StoreSetting.get(SETTING_KEY, "") or ""
    try:
        saved = json.loads(raw) if raw else []
    except ValueError:
        logger.warning("home_layout: JSON خاسر — نرجعو للافتراضي")
        saved = []
    if not isinstance(saved, list):
        saved = []

    result, seen = [], set()
    for item in saved:
        key = item.get("key") if isinstance(item, dict) else None
        spec = _BY_KEY.get(key)
        if spec is None or key in seen:
            continue
        seen.add(key)
        enabled = bool(item.get("enabled", spec["default_on"])) or not spec["can_hide"]
        result.append({"key": key, "enabled": enabled,
                       "options": _clean_options(spec, item.get("options"))})
    # أقسام جديدة فالكود ما مخزّنةش: تتزاد فالآخر بقيمها الافتراضية
    for spec in SECTIONS:
        if spec["key"] not in seen:
            result.append({"key": spec["key"], "enabled": spec["default_on"],
                           "options": _clean_options(spec, {})})
    return result


def active_sections():
    """الأقسام المفعّلة بالترتيب — للصفحة الرئيسية."""
    return [s for s in get_layout() if s["enabled"]]


def section_options(key):
    for s in get_layout():
        if s["key"] == key:
            return s["options"]
    return {}


def save_layout_from_form(form):
    """
    يحفظ من فورم الأدمن. الحقول (لكل قسم i بالترتيب):
        order = [key, key, ...]            الترتيب
        on_<key>                           checkbox
        count_<key> / mode_<key> / autoplay_<key>
    """
    order = [k for k in form.getlist("order") if k in _BY_KEY]
    layout = []
    for key in order:
        spec = _BY_KEY[key]
        enabled = bool(form.get(f"on_{key}")) or not spec["can_hide"]
        raw = {"count": form.get(f"count_{key}"), "mode": form.get(f"mode_{key}"),
               "autoplay": bool(form.get(f"autoplay_{key}"))}
        layout.append({"key": key, "enabled": enabled, "options": _clean_options(spec, raw)})
    StoreSetting.set(SETTING_KEY, json.dumps(layout, ensure_ascii=False))
    return layout


def reset_layout():
    StoreSetting.set(SETTING_KEY, "")
