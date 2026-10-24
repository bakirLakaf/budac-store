"""
تكامل Yalidine Express — API الرسمي (v1).

التوثيق: https://yalidine.app/app/dev/docs/api/index.php
القاعدة:  https://api.yalidine.app/v1

المتغيّرات (فـ.env):
    YALIDINE_API_ID         معرّف الـAPI (من لوحة Yalidine ← API)
    YALIDINE_API_TOKEN      التوكن (سرّي)
    YALIDINE_FROM_WILAYA    اسم ولاية المرسل (أنت) كيما فـ/wilayas تاع Yalidine
                            بالحروف اللاتينية، مثلا: Batna  ·  Alger  ·  Oran
    YALIDINE_FREESHIPPING   «0» (افتراضي): الزبون يدفع ثمن المنتجات + ثمن التوصيل؛ نبعثو
                            سعر المنتجات برك وYalidine تزيد التوصيل (ما تخصم من رصيدك).
                            «1»: السعر المبعوث = مجموع الطلبية (يشمل التوصيل) والتوصيل
                            يتخصم من رصيدك — يحتاج رصيد كافي فـYalidine.
                            الطلبيات بتوصيل مجاني تتبعث دايمًا بـfreeshipping=true.
    YALIDINE_INSURANCE      «0» (افتراضي) — «1» يفعّل التأمين (1.5% من القيمة المصرّحة)
    YALIDINE_WEIGHT / _LENGTH / _WIDTH / _HEIGHT
                            مقاسات الطرد الافتراضية (كغ / سم). الافتراضي 1 / 20 / 15 / 10
    YALIDINE_BASE_URL       اختياري — HTTPS برك

⚠️ قواعد Yalidine المهمة:
  • الحدّ: 5 فالثانية / 50 فالدقيقة / 1000 فالساعة / 10000 فاليوم (429 كي نتعدّاو).
  • إنشاء الطرود يرجّع 200 حتى كي يفشل طرد: لازم نقراو `success` لكل طرد.
  • الحذف ممكن غير كي تكون آخر حالة «en préparation».
  • أسماء الولايات والبلديات لازم تكون **بالضبط** كيما يعرفوها (لاتينية).

⚠️ المفاتيح ما تتكتبش أبدًا فالسجلات ولا فرسائل الخطأ.
"""

import json
import os
import re
import time
import unicodedata

import requests

DEFAULT_BASE_URL = "https://api.yalidine.app/v1"
CARRIER_CODE = "yalidine"
CONNECT_TIMEOUT = 8
READ_TIMEOUT = 25
PAGE_SIZE = 1000
MAX_PAGES = 30
MAX_PRICE = 150000

# اسم موحّد للمنتج فالطرد (نفس منطق ZR/World: ما نبعثوش أسماء المنتجات)
PRODUCT_LABEL = "أكسسوارات"

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_TTL = 6 * 3600
_cache = {}      # مفتاح → (وقت, قيمة)


class YalidineError(Exception):
    """خطأ من Yalidine برسالة مفهومة للأدمن.

    `parcel_may_exist=True`: الطلب وصل ممكن والطرد ممكن اتنشأ فعلاً
    (انتهت المهلة ولا خطأ 5xx). فهاد الحالة ما نعاودوش الإرسال بلا تأكّد.
    """

    def __init__(self, message, parcel_may_exist=False):
        super().__init__(message)
        self.parcel_may_exist = parcel_may_exist


# ──────────────────────────── الإعدادات ────────────────────────────

def _api_id():
    return (os.environ.get("YALIDINE_API_ID") or "").strip()


def _api_token():
    return (os.environ.get("YALIDINE_API_TOKEN") or "").strip()


def is_configured():
    return bool(_api_id() and _api_token())


def _base_url():
    url = (os.environ.get("YALIDINE_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    # ⚠️ المفاتيح تتبعث فكل طلب — نقبلو HTTPS برك
    if not url.lower().startswith("https://"):
        return DEFAULT_BASE_URL
    return url


def _flag(name, default):
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _int_env(name, default):
    try:
        value = int((os.environ.get(name) or "").strip())
        return value if value >= 0 else default
    except ValueError:
        return default


def from_wilaya_name():
    return (os.environ.get("YALIDINE_FROM_WILAYA") or "").strip()


def canonical_from_wilaya():
    """اسم ولاية المرسل كيما يعرفو Yalidine بالضبط (مثلا «Oum El-Bouaghi» ← اسمهم الرسمي)."""
    raw = from_wilaya_name()
    try:
        for name in get_wilayas().values():
            if _norm_latin(name) == _norm_latin(raw):
                return name
    except YalidineError:
        pass
    return raw


# ──────────────────────────── الطلبات ────────────────────────────

def _request(method, path, params=None, json_body=None, may_create=False):
    """
    نداء واحد لـYalidine. يرمي YalidineError فكل فشل.

    may_create=True: النداء يخلق طردًا — كي تنتهي المهلة بعد ما بعثنا الطلب
    نعلّمو الخطأ `parcel_may_exist` باش ما نعاودوش بلا تأكّد.
    """
    if not is_configured():
        raise YalidineError(
            "مفاتيح Yalidine غير معمّرة. حط YALIDINE_API_ID و YALIDINE_API_TOKEN فملف .env")

    url = _base_url() + path
    headers = {
        "X-API-ID": _api_id(),
        "X-API-TOKEN": _api_token(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.request(
            method, url, params=params, json=json_body, headers=headers,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
    except requests.exceptions.ConnectTimeout:
        raise YalidineError("ما قدرناش نوصلو لـYalidine (انتهت مهلة الاتصال). عاود بعد شوية.")
    except requests.exceptions.ReadTimeout:
        raise YalidineError(
            "Yalidine ما ردّتش فالوقت المحدّد."
            + (" ⚠️ ممكن الطرد اتنشأ فعلاً — شوف لوحة Yalidine قبل ما تعاود الإرسال."
               if may_create else " عاود بعد شوية."),
            parcel_may_exist=may_create,
        )
    except requests.exceptions.RequestException:
        raise YalidineError("ما قدرناش نوصلو لـYalidine (مشكل شبكة). عاود بعد شوية.")

    status = resp.status_code
    if status in (401, 403):
        raise YalidineError("Yalidine رفضت المفاتيح (API ID ولا التوكن غير صالحين، ولا الـAPI "
                            "مطفية فحسابك). تأكّد منهم فلوحة Yalidine.")
    if status == 429:
        wait = resp.headers.get("Retry-After")
        raise YalidineError("تعدّينا حدّ الطلبات عند Yalidine."
                            + (f" استنى {wait} ثانية." if wait else " استنى دقيقة."))
    if status >= 500:
        raise YalidineError(f"Yalidine عندها مشكل (خطأ {status}). عاود بعد شوية.",
                            parcel_may_exist=may_create)

    try:
        data = resp.json()
    except ValueError:
        raise YalidineError("Yalidine رجّعت ردّ ماشي JSON — تأكّد من YALIDINE_BASE_URL.",
                            parcel_may_exist=may_create and status < 300)

    if status >= 400:
        message = None
        if isinstance(data, dict):
            message = data.get("message") or data.get("error")
            if isinstance(message, dict):
                message = message.get("message")
        raise YalidineError(str(message) if message else f"Yalidine رجّعت خطأ {status}.")

    return data


def _get_all(path, params=None):
    """يجيب كل صفحات قائمة (has_more) — يتوقف على الفراغ ولا الحدّ الأقصى."""
    items = []
    for page in range(1, MAX_PAGES + 1):
        query = dict(params or {})
        query.update({"page": page, "page_size": PAGE_SIZE})
        data = _request("GET", path, params=query)
        if isinstance(data, list):
            return items + [i for i in data if isinstance(i, dict)]
        rows = (data or {}).get("data") or []
        items.extend(r for r in rows if isinstance(r, dict))
        if not (data or {}).get("has_more") or not rows:
            break
    return items


def _cached(key, loader, force=False):
    """ذاكرة 6 ساعات. إذا فشل التحديث ونملكو نسخة قديمة نستعملوها (أحسن من والو)."""
    hit = _cache.get(key)
    if hit and not force and time.time() - hit[0] < _TTL:
        return hit[1]
    try:
        value = loader()
    except YalidineError:
        if hit:
            return hit[1]
        raise
    _cache[key] = (time.time(), value)
    return value


def clear_cache():
    _cache.clear()


# ──────────────────────────── التنظيف ────────────────────────────

def _norm_latin(text):
    """حروف لاتينية صغيرة بلا تشكيل ولا رموز ولا فراغات: «Bab-Ezzouar» = «bab ezzouar»."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text.lower())


_AR_TRANS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ـ": ""})


def _norm_ar(text):
    text = str(text or "").translate(_AR_TRANS)
    text = "".join(ch for ch in text if not ("\u064b" <= ch <= "\u0652" or ch == "\u0670"))
    return re.sub(r"\s+", "", text)


def normalize_phone(phone):
    """رقم جزائري بصيغة 0XXXXXXXXX (10 أرقام جوال / 9 أرضي) ولا None."""
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if digits.startswith("00213"):
        digits = "0" + digits[5:]
    elif digits.startswith("213"):
        digits = "0" + digits[3:]
    valid_mobile = len(digits) == 10 and digits[:2] in ("05", "06", "07")
    valid_landline = len(digits) == 9 and digits[:2] in ("02", "03", "04")
    return digits if (valid_mobile or valid_landline) else None


def split_name(full_name):
    """Yalidine تطلب firstname و familyname الاثنين. اسم من كلمة وحدة → نكرّروها."""
    words = str(full_name or "").split()
    if not words:
        return "", ""
    if len(words) == 1:
        return words[0], words[0]
    return words[0], " ".join(words[1:])


def _wilaya_int(order):
    try:
        n = int(str(order.wilaya_code).strip())
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 58 else None


def check_order_data(order):
    """فحص قبل الإرسال — رسائل عربية واضحة بدل خطأ غامض."""
    problems = []
    if not from_wilaya_name():
        problems.append("YALIDINE_FROM_WILAYA غير معمّر فملف .env (ولاية المرسل)")
    if not normalize_phone(order.customer_phone):
        problems.append(f"رقم الهاتف «{order.customer_phone}» غير صحيح (05/06/07 + 8 أرقام)")
    if not order.customer_name or len(order.customer_name.strip()) < 3:
        problems.append("اسم الزبون قصير بزاف")
    if _wilaya_int(order) is None:
        problems.append(f"رمز الولاية «{order.wilaya_code}» غير صحيح")
    if order.delivery_type == "office":
        if not order.pickup_point_name:
            problems.append("التوصيل لمكتب بلا اسم مكتب")
    elif not order.commune:
        problems.append("التوصيل للمنزل بلا بلدية")
    if not order.total or order.total <= 0:
        problems.append("مبلغ الطلبية صفر")
    elif order.total > MAX_PRICE:
        problems.append(f"المبلغ يفوق حدّ Yalidine ({MAX_PRICE} دج)")
    return problems


# ──────────────────────────── الولايات والبلديات والمكاتب ────────────────────────────

def get_wilayas(force=False):
    """{رقم الولاية: الاسم اللاتيني كيما يعرفو Yalidine}."""
    def load():
        rows = _get_all("/wilayas/")
        return {int(r["id"]): str(r["name"]) for r in rows
                if r.get("id") is not None and r.get("name")}
    return _cached("wilayas", load, force)


def get_communes(wilaya_id, force=False):
    """بلديات ولاية: كل عنصر فيه name, has_stop_desk, is_deliverable."""
    wid = int(wilaya_id)
    return _cached(("communes", wid),
                   lambda: _get_all("/communes/", {"wilaya_id": wid}), force)


def get_centers(force=False):
    """كل مكاتب Yalidine: center_id, name, address, commune_name, wilaya_id, wilaya_name."""
    return _cached("centers", lambda: _get_all("/centers/"), force)


_fr_names = None


def _french_names():
    """{(رمز الولاية، البلدية بالعربية مطبّعة): الاسم اللاتيني} من ملف البلديات المحلي."""
    global _fr_names
    if _fr_names is None:
        mapping = {}
        try:
            with open(os.path.join(_DATA_DIR, "algeria_communes.json"), encoding="utf-8") as f:
                for row in json.load(f):
                    key = (str(row.get("c", "")).zfill(2), _norm_ar(row.get("n")))
                    if row.get("a"):
                        mapping[key] = row["a"]
        except (OSError, ValueError):
            pass
        _fr_names = mapping
    return _fr_names


def commune_candidates(order):
    """الأسماء الممكنة لبلدية الطلبية بحروف لاتينية: الاسم المحفوظ + ترجمتو من الملف المحلي."""
    names = []
    raw = (order.commune or "").strip()
    if raw:
        names.append(raw)
        fr = _french_names().get((str(order.wilaya_code).zfill(2), _norm_ar(raw)))
        if fr:
            names.append(fr)
    return names


def find_commune(wilaya_id, candidates):
    """
    يلقى البلدية فقائمة Yalidine. تطابق تام (مطبّع) ثم جزئي **إذا كان واحد برك**
    (ما نخمّنوش). يرجّع None كي ما يلقاش ولا كان التباس.
    """
    wanted = [n for n in (_norm_latin(c) for c in candidates) if n]
    if not wanted:
        return None
    communes = [c for c in get_communes(wilaya_id)
                if c.get("name") and c.get("is_deliverable", True) not in (0, False)]
    for c in communes:
        if _norm_latin(c["name"]) in wanted:
            return c
    partial = [c for c in communes
               if any(len(w) >= 4 and (w in _norm_latin(c["name"]) or _norm_latin(c["name"]) in w)
                      for w in wanted)]
    return partial[0] if len(partial) == 1 else None


def find_center(name, wilaya_code):
    """المكتب من اسمو + ولايتو. يرمي YalidineError إذا ما لقاهش ولا كاين التباس."""
    target = _norm_latin(name)
    wid = int(str(wilaya_code))
    matches = [c for c in get_centers()
               if _norm_latin(c.get("name")) == target
               and str(c.get("wilaya_id", "")).strip() == str(wid)]
    ids = {str(c.get("center_id")) for c in matches}
    if not matches:
        raise YalidineError(f"المكتب «{name}» ما لقيناهش فقائمة مكاتب Yalidine.")
    if len(ids) > 1:
        raise YalidineError(f"فيه أكثر من مكتب بنفس الاسم «{name}» فهاد الولاية — ابعثها يدويًا.")
    return matches[0]


def centers_as_offices():
    """
    مكاتب Yalidine بنفس شكل ملفات المكاتب (يستعملها carrier_offices) —
    باش الزبون يختار مكتب فصفحة الطلب. قائمة فارغة إذا الـAPI ما معمّرش.
    """
    if not is_configured():
        return []
    offices = []
    for c in get_centers():
        if c.get("center_id") is None or not c.get("name"):
            continue
        offices.append({
            "name": str(c["name"]),
            "address": c.get("address") or c.get("commune_name") or "",
            "commune": c.get("commune_name") or "",
            "phone": "",
            "wilaya_code": str(c.get("wilaya_id", "")).zfill(2),
            "center_id": c["center_id"],
            "pickup": True,
        })
    return offices


def resolve_destination(order):
    """
    يرجّع القاموس تاع الوجهة: to_commune_name, to_wilaya_name, is_stopdesk, stopdesk_id.
    يرمي YalidineError برسالة واضحة إذا ما لقاش.
    """
    wid = _wilaya_int(order)
    wilayas = get_wilayas()
    wilaya_name = wilayas.get(wid)
    if not wilaya_name:
        raise YalidineError(f"الولاية رقم {wid} ما لقيناهاش عند Yalidine.")

    if order.delivery_type == "office":
        center = find_center(order.pickup_point_name, wid)
        commune_name = center.get("commune_name")
        if not commune_name:
            raise YalidineError(f"المكتب «{order.pickup_point_name}» ما عندوش بلدية فقائمة Yalidine.")
        # نتأكّدو أن البلدية فيها مكتب استلام فعلاً (رسالة واضحة بدل رفض غامض)
        found = find_commune(wid, [commune_name])
        if found and found.get("has_stop_desk") in (0, False):
            raise YalidineError(
                f"Yalidine ما عندهاش استلام من المكتب فبلدية «{found['name']}». "
                "بدّل لتوصيل للمنزل ولا ابعثها يدويًا.")
        return {
            "to_commune_name": commune_name,
            "to_wilaya_name": center.get("wilaya_name") or wilaya_name,
            "is_stopdesk": True,
            "stopdesk_id": int(center["center_id"]),
        }

    found = find_commune(wid, commune_candidates(order))
    if not found:
        raise YalidineError(
            f"البلدية «{order.commune}» ما لقيناهاش (ولا ما توصلهاش) عند Yalidine فولاية "
            f"{order.wilaya_name}. بدّل البلدية فالطلبية ولا ابعثها يدويًا.")
    return {
        "to_commune_name": found["name"],
        "to_wilaya_name": found.get("wilaya_name") or wilaya_name,
        "is_stopdesk": False,
        "stopdesk_id": None,
    }


# ──────────────────────────── العمليات ────────────────────────────

def _cut(text, n=250):
    return (str(text or "").strip())[:n]


def build_parcel(order, dest):
    """الطرد بالشكل اللي تطلبو Yalidine (القيم المنطقية لازم تكون boolean حقيقية)."""
    firstname, familyname = split_name(order.customer_name)
    total = int(round(order.total))

    # ⚠️ من يدفع التوصيل؟
    #  • الافتراضي (YALIDINE_FREESHIPPING=0): الزبون يدفع ثمن المنتجات + ثمن التوصيل.
    #    نبعثو لـYalidine سعر المنتجات برك (مجموع الطلبية − التوصيل) وهي تزيد
    #    ثمن التوصيل وتحصّل الكل من الزبون — بلا ما تخصم من رصيدك.
    #  • كي الطلبية فيها توصيل مجاني (سعر التوصيل 0 ولا كوبون توصيل)، نبعثو
    #    freeshipping=True والمتجر هو اللي يدفع التوصيل (يتخصم من رصيد Yalidine).
    #  • YALIDINE_FREESHIPPING=1: نفرضو النظام القديم دايمًا (price = المجموع).
    ship = int(round(getattr(order, "shipping_price", 0) or 0))
    ship_discount = int(round(getattr(order, "shipping_discount", 0) or 0))
    merchant_pays = _flag("YALIDINE_FREESHIPPING", False) or ship <= 0 or ship_discount > 0
    price = total if merchant_pays else max(total - ship, 0)

    address = _cut(order.address_note)
    if dest["is_stopdesk"]:
        address = _cut(f"مكتب: {order.pickup_point_name}")
    address = address or _cut(f"{dest['to_commune_name']}, {dest['to_wilaya_name']}")

    parcel = {
        "order_id": _cut(order.order_number, 60),
        "from_wilaya_name": canonical_from_wilaya(),
        "firstname": _cut(firstname, 100),
        "familyname": _cut(familyname, 100),
        "contact_phone": normalize_phone(order.customer_phone),
        "address": address,
        "to_commune_name": dest["to_commune_name"],
        "to_wilaya_name": dest["to_wilaya_name"],
        "product_list": PRODUCT_LABEL,
        "price": min(price, MAX_PRICE),                 # سعر المنتجات للتحصيل (Yalidine تزيد التوصيل)
        "do_insurance": _flag("YALIDINE_INSURANCE", False),
        "declared_value": min(price, MAX_PRICE),
        "length": _int_env("YALIDINE_LENGTH", 20),
        "width": _int_env("YALIDINE_WIDTH", 15),
        "height": _int_env("YALIDINE_HEIGHT", 10),
        "weight": _int_env("YALIDINE_WEIGHT", 1),
        "freeshipping": bool(merchant_pays),
        "is_stopdesk": bool(dest["is_stopdesk"]),
        "has_exchange": False,
    }
    if dest["is_stopdesk"]:
        parcel["stopdesk_id"] = dest["stopdesk_id"]
    return parcel


def _find_by_order_id(order_number):
    """يدوّر على طرد بالمرجع تاعنا — يستعمل فاستعادة الخطأ بعد انتهاء المهلة."""
    data = _request("GET", "/parcels/", params={"order_id": order_number})
    rows = data.get("data") if isinstance(data, dict) else data
    for row in rows or []:
        if isinstance(row, dict) and str(row.get("order_id", "")) == str(order_number) \
                and row.get("tracking"):
            return row
    return None


def create_order(order):
    """
    ينشئ الطرد عند Yalidine. يرجّع {"tracking_number": ..., "label_url": ...}.

    ⚠️ HTTP 200 ما يعنيش النجاح: نقراو `success` تاع طردنا فالرد.
    """
    problems = check_order_data(order)
    if problems:
        raise YalidineError("الطلبية ما تقبلهاش Yalidine: " + " | ".join(problems))

    dest = resolve_destination(order)
    parcel = build_parcel(order, dest)

    try:
        data = _request("POST", "/parcels/", json_body=[parcel], may_create=True)
    except YalidineError as e:
        if e.parcel_may_exist:
            # ممكن الطرد اتنشأ — نشوفو بالمرجع قبل ما نطلبو من الأدمن يتأكّد بيدو
            try:
                row = _find_by_order_id(order.order_number)
            except YalidineError:
                row = None
            if row:
                return {"tracking_number": str(row["tracking"]).strip(),
                        "label_url": row.get("label")}
        raise

    entry = None
    if isinstance(data, dict):
        entry = data.get(parcel["order_id"])
        if entry is None and len(data) == 1:
            entry = next(iter(data.values()))
    if not isinstance(entry, dict):
        raise YalidineError(
            "Yalidine رجّعت ردّ غير مفهوم. "
            f"شوف لوحة Yalidine على المرجع {order.order_number} قبل ما تعاود.",
            parcel_may_exist=True)

    if not entry.get("success"):
        raise YalidineError(str(entry.get("message") or "Yalidine رفضت الطرد."))

    tracking = entry.get("tracking")
    if not tracking:
        raise YalidineError(
            "Yalidine قالت نجح الإنشاء بصح ما رجّعتش رقم التتبّع. "
            f"شوف لوحة Yalidine على المرجع {order.order_number} قبل ما تعاود.",
            parcel_may_exist=True)
    return {"tracking_number": str(tracking).strip(), "label_url": entry.get("label")}


def get_parcel(tracking):
    """الطرد كامل من رقم التتبّع (فيه last_status و label)."""
    data = _request("GET", f"/parcels/{tracking}/")
    rows = data.get("data") if isinstance(data, dict) and "data" in data else data
    if isinstance(rows, dict):
        rows = [rows]
    for row in rows or []:
        if isinstance(row, dict) and str(row.get("tracking", "")).strip() == str(tracking).strip():
            return row
    raise YalidineError("Yalidine ما لقاتش هاد الطرد (ممكن اتمسح).")


def get_order_status(tracking):
    """حالة الطرد. يرجّع {"state": "<نص>", "raw": {...}}."""
    row = get_parcel(tracking)
    state = row.get("last_status")
    if state:
        return {"state": str(state), "raw": row}

    data = _request("GET", "/histories/", params={"tracking": tracking})
    rows = data.get("data") if isinstance(data, dict) else data
    rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("status")]
    if rows:
        rows.sort(key=lambda r: str(r.get("date_creation") or ""))
        return {"state": str(rows[-1]["status"]), "raw": row}
    raise YalidineError("Yalidine ما رجّعتش حالة لهاد الطرد.")


def get_label_url(tracking):
    """رابط البوردرو الرسمي. نقبلو غير روابط yalidine.app عبر HTTPS (الرابط فيه توكن)."""
    url = str(get_parcel(tracking).get("label") or "").strip()
    host = re.match(r"^https://([^/:?#]+)(?:[:/?#]|$)", url)
    if not host or not (host.group(1) == "yalidine.app" or host.group(1).endswith(".yalidine.app")):
        raise YalidineError("Yalidine ما رجّعتش رابط بوردرو صالح.")
    return url


def delete_parcel(tracking):
    """يمسح الطرد — ممكن غير كي آخر حالة «en préparation». يرجّع True/False."""
    data = _request("DELETE", f"/parcels/{tracking}/")
    rows = data if isinstance(data, list) else [data]
    for row in rows:
        if isinstance(row, dict) and str(row.get("tracking", "")).strip() == str(tracking).strip():
            return bool(row.get("deleted"))
    return False


def test_connection():
    """يختبر المفاتيح وولاية المرسل. يرجّع (ok, رسالة)."""
    if not is_configured():
        return False, "ما كاينش YALIDINE_API_ID / YALIDINE_API_TOKEN فملف .env"
    try:
        wilayas = get_wilayas(force=True)
    except YalidineError as e:
        return False, str(e)
    if not wilayas:
        return False, "رد غير متوقّع: قائمة الولايات فارغة"

    origin = from_wilaya_name()
    if not origin:
        return False, ("✓ المفاتيح صالحة، بصح YALIDINE_FROM_WILAYA ناقص — "
                       "حط ولاية المرسل (مثلا Batna) فملف .env")
    if _norm_latin(origin) not in {_norm_latin(n) for n in wilayas.values()}:
        return False, (f"✓ المفاتيح صالحة، بصح ولاية المرسل «{origin}» ما كاينش عند Yalidine. "
                       "اكتبها كيما فقائمتهم (مثلا Batna ولا Alger).")
    return True, "✓ المفاتيح صالحة — Yalidine مربوطة"


# ──────────────────────────── ترجمة الحالات ────────────────────────────

_DELIVERED = {"livre"}
_RETURNED = {"retourne_au_vendeur", "retour_recu", "retourne_vendeur"}
_IN_TRANSIT = {"expedie", "transfert", "centre", "en_localisation", "vers_wilaya",
               "recu_a_wilaya", "sorti_en_livraison", "en_attente_du_client",
               "en_attente", "tentative_echouee"}


def map_state_to_order_status(state, delivery_type="home"):
    """
    يترجم حالة Yalidine لحالة طلبية عندنا. None = ما نبدّلوش شي.

    الأمان أولًا:
      • «راجعة» (ترجّع المخزون) غير كي يكون الطرد رجع للبائع فعلاً.
        «Retour vers centre / vers vendeur» = مازال فالطريق → None.
      • «En préparation» و«En alerte» و«Echec livraison» → None (قرار الأدمن).
    """
    key = re.sub(r"[^a-z0-9]+", "_", unicodedata.normalize("NFKD", str(state or ""))
                 .encode("ascii", "ignore").decode().lower()).strip("_")
    if not key:
        return None
    if key in _RETURNED:
        return "returned"
    if key in _DELIVERED:
        return "delivered"
    if key in _IN_TRANSIT:
        return "shipped"
    return None
