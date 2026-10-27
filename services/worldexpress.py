"""
تكامل World Express — عن طريق منصة ECOTRACK.

World Express تخدم بمنصة ECOTRACK، والتوثيق العمومي تاعها:
    https://documenter.getpostman.com/view/14517169/Tz5je15g
رابط منصة World:  https://world-express.ecotrack.dz

المتغيّرات (فـ.env):
    WORLD_API_TOKEN     التوكن (من لوحة ECOTRACK ← API ← «Voir Token»)
    WORLD_BASE_URL      اختياري — الافتراضي https://world-express.ecotrack.dz
    WORLD_AUTO_VALIDATE اختياري — «1» يعني نأكّدو الإرسال (Expédier) تلقائيًا
                        بعد إنشاء الطرد. الافتراضي «0»: الأدمن يضغط الزر.

⚠️ قواعد ECOTRACK المهمة:
  • الحدّ: 50 طلب فالدقيقة / 1500 فالساعة / 15000 فاليوم (429 كي نتعدّاو).
  • الطرد يتنشأ «prête à expédier». كي نأكّدوه (valid/order) ما يتبدّلش
    ولا يتمسحش.
  • الرد يجي بـ200 حتى فالخطأ: `{"success": false, "error": 10002, ...}`.

⚠️ التوكن ما يتكتبش أبدًا فالسجلات ولا فرسائل الخطأ.
"""

import os
import re
import time
import unicodedata

import requests

DEFAULT_BASE_URL = "https://world-express.ecotrack.dz"
CARRIER_CODE = "worldexpress"
CONNECT_TIMEOUT = 8
READ_TIMEOUT = 25
MAX_LABEL_BYTES = 5 * 1024 * 1024

# اسم موحّد للمنتج فالطرد (نفس منطق ZR: ما نبعثوش أسماء المنتجات للشركة)
PRODUCT_LABEL = "أكسسوارات"

_COMMUNES_TTL = 6 * 3600
_communes_cache = {}   # wilaya_code(str|None) → (وقت, قائمة)


class WorldError(Exception):
    """خطأ من World Express / ECOTRACK برسالة مفهومة للأدمن.

    `parcel_may_exist=True` معناها: الطلب وصل ممكن، والطرد ممكن اتنشأ فعلاً
    (مثلاً انتهت المهلة وحنا ننتظرو الرد). فهاد الحالة **ما نعاودوش الإرسال
    قبل ما الأدمن يشوف لوحة ECOTRACK** — باش ما يتخلقش طرد مضاعف.
    """

    def __init__(self, message, parcel_may_exist=False):
        super().__init__(message)
        self.parcel_may_exist = parcel_may_exist


# ──────────────────────────── الإعدادات ────────────────────────────

def _token():
    return (os.environ.get("WORLD_API_TOKEN") or "").strip()


def is_configured():
    return bool(_token())


def _base_url():
    url = (os.environ.get("WORLD_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    # ⚠️ التوكن يتبعث فكل طلب — نقبلو HTTPS برك
    if not url.lower().startswith("https://"):
        return DEFAULT_BASE_URL
    return url


def auto_validate_enabled():
    return (os.environ.get("WORLD_AUTO_VALIDATE") or "").strip().lower() in (
        "1", "true", "yes", "on")


# ──────────────────────────── الطلبات ────────────────────────────

def _error_text_from(data):
    """يستخرج رسالة مفهومة من رد خطأ ECOTRACK (422 ولا success=false)."""
    if not isinstance(data, dict):
        return None
    parts = []
    errors = data.get("errors")
    if isinstance(errors, dict):
        for field, msgs in errors.items():
            if isinstance(msgs, (list, tuple)):
                parts.extend(str(m) for m in msgs)
            else:
                parts.append(str(msgs))
    if not parts and data.get("message"):
        parts.append(str(data["message"]))
    text = " | ".join(parts)
    code = data.get("error")
    if code and text:
        text += f" (كود {code})"
    return text or None


def _request(method, path, params=None, json_body=None, raw=False,
             may_create=False):
    """
    نداء واحد لـECOTRACK. يرمي WorldError فكل فشل.

    may_create=True: النداء يخلق طردًا — إذا انتهت المهلة بعد ما بعثنا الطلب،
    نعلّمو الخطأ `parcel_may_exist` باش ما نعاودوش بلا تأكّد.
    """
    token = _token()
    if not token:
        raise WorldError("توكن World Express غير معمّر. حطّ WORLD_API_TOKEN فملف .env")

    url = _base_url() + path
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    try:
        resp = requests.request(
            method, url, params=params, json=json_body, headers=headers,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
    except requests.exceptions.ConnectTimeout:
        raise WorldError("ما قدرناش نوصلو لـWorld Express (انتهت مهلة الاتصال). عاود بعد شوية.")
    except requests.exceptions.ReadTimeout:
        raise WorldError(
            "World Express ما ردّتش فالوقت المحدّد."
            + (" ⚠️ ممكن الطرد اتنشأ فعلاً — شوف لوحة ECOTRACK قبل ما تعاود الإرسال."
               if may_create else " عاود بعد شوية."),
            parcel_may_exist=may_create,
        )
    except requests.exceptions.RequestException:
        raise WorldError("ما قدرناش نوصلو لـWorld Express (مشكل شبكة). عاود بعد شوية.")

    status = resp.status_code
    if status in (401, 403):
        raise WorldError("World Express رفضت التوكن (غير صالح ولا الـAPI مطفية فحسابك). "
                         "تأكّد من التوكن فلوحة ECOTRACK.")
    if status == 429:
        wait = resp.headers.get("Retry-After")
        raise WorldError("تعدّينا حدّ الطلبات عند World Express (50 فالدقيقة)."
                         + (f" استنى {wait} ثانية." if wait else " استنى دقيقة."))
    if status >= 500:
        raise WorldError(f"World Express عندها مشكل (خطأ {status}). عاود بعد شوية.",
                         parcel_may_exist=may_create)

    if raw:
        if status != 200:
            raise WorldError(f"World Express رجّعت خطأ {status}.")
        return resp

    try:
        data = resp.json()
    except ValueError:
        raise WorldError("World Express رجّعت ردّ ماشي JSON — تأكّد من WORLD_BASE_URL.",
                         parcel_may_exist=may_create and status < 300)

    if status >= 400:
        raise WorldError(_error_text_from(data) or f"World Express رجّعت خطأ {status}.")

    if isinstance(data, dict) and data.get("success") is False:
        raise WorldError(_error_text_from(data) or "World Express رفضت الطلب.")

    return data


# ──────────────────────────── التنظيف والفحص ────────────────────────────

def _plain(text):
    """نص بحروف صغيرة وبلا تشكيل ولا رموز — باش مقارنة أسماء البلديات تكون مليحة."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def normalize_phone(phone):
    """يرجّع رقم جزائري بصيغة 0XXXXXXXXX (9-10 أرقام) ولا None إذا غير صالح."""
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if digits.startswith("00213"):
        digits = "0" + digits[5:]
    elif digits.startswith("213"):
        digits = "0" + digits[3:]
    valid_mobile = len(digits) == 10 and digits[:2] in ("05", "06", "07")
    valid_landline = len(digits) == 9 and digits[:2] in ("02", "03", "04")
    return digits if (valid_mobile or valid_landline) else None


def _wilaya_int(order):
    try:
        n = int(str(order.wilaya_code).strip())
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 58 else None


def check_order_data(order):
    """فحص قبل الإرسال — رسائل عربية واضحة بدل 422 غامض."""
    problems = []
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
    return problems


# ──────────────────────────── البلديات ────────────────────────────

def get_communes(wilaya_code=None, force=False):
    """قائمة بلديات ECOTRACK (مخزّنة 6 ساعات). كل عنصر: nom, wilaya_id, has_stop_desk, is_active."""
    key = str(int(wilaya_code)) if wilaya_code else None
    cached = _communes_cache.get(key)
    if cached and not force and time.time() - cached[0] < _COMMUNES_TTL:
        return cached[1]

    params = {"wilaya_id": key} if key else None
    data = _request("GET", "/api/v1/get/communes", params=params)
    # الرد قاموس مفاتيحو أرقام {"0": {...}, "1": {...}} — نقبلو كذلك القائمة
    items = list(data.values()) if isinstance(data, dict) else list(data or [])
    items = [i for i in items if isinstance(i, dict) and i.get("nom")]
    _communes_cache[key] = (time.time(), items)
    return items


def clear_cache():
    _communes_cache.clear()


def find_commune(wilaya_code, name):
    """
    يلقى البلدية فقائمة ECOTRACK بالاسم (بلا حساسية للحروف والتشكيل).
    يفضّل التطابق التام، وإلا يقبل تطابق جزئي **إذا كان واحد برك** (ما نخمّنوش).
    """
    target = _plain(name)
    if not target:
        return None
    active = [c for c in get_communes(wilaya_code) if c.get("is_active")]
    exact = [c for c in active if _plain(c["nom"]) == target]
    if exact:
        return exact[0]
    partial = [c for c in active
               if target in _plain(c["nom"]) or _plain(c["nom"]) in target]
    return partial[0] if len(partial) == 1 else None


def resolve_destination(order):
    """
    يرجّع (اسم_البلدية_كيما_يعرفها_ECOTRACK, stop_desk) للطلبية.

    • منزل: بلدية الطلبية.
    • مكتب: بلدية المكتب (من data/world_offices.json) + stop_desk=1.
    يرمي WorldError برسالة واضحة إذا ما تلقاتش.
    """
    stop_desk = 1 if order.delivery_type == "office" else 0
    wilaya = _wilaya_int(order)

    if stop_desk:
        from services.carrier_offices import get_office
        office = get_office(CARRIER_CODE, order.pickup_point_name, order.wilaya_code)
        if not office or not office.get("commune"):
            raise WorldError(
                f"المكتب «{order.pickup_point_name}» ما لقيناهش فقائمة مكاتب World Express.")
        raw_name = office["commune"]
    else:
        raw_name = order.commune

    try:
        found = find_commune(wilaya, raw_name)
    except WorldError:
        # القائمة ما جاتش (شبكة...) — نبعثو الاسم كيما هو، وECOTRACK يفحص
        return raw_name, stop_desk

    if not found:
        raise WorldError(
            f"البلدية «{raw_name}» ما لقيناهاش (ولا مطفية) عند World Express فولاية "
            f"{order.wilaya_name}. بدّل البلدية فالطلبية ولا ابعثها يدويًا.")
    if stop_desk and not found.get("has_stop_desk"):
        raise WorldError(
            f"World Express ما عندهاش مكتب استلام فبلدية «{found['nom']}». "
            "بدّل لتوصيل للمنزل ولا ابعثها يدويًا.")
    return found["nom"], stop_desk


# ──────────────────────────── العمليات ────────────────────────────

def _cut(text, n=255):
    return (str(text or "").strip())[:n]


def build_order_params(order, commune_name, stop_desk):
    """معاملات `POST /api/v1/create/order` (التوثيق: كلها فالـquery)."""
    address = order.address_note
    if stop_desk:
        from services.carrier_offices import get_office
        office = get_office(CARRIER_CODE, order.pickup_point_name, order.wilaya_code)
        address = f"مكتب: {order.pickup_point_name}" + (
            f" — {office.get('address')}" if office and office.get("address") else "")
    address = _cut(address) or _cut(f"{commune_name}, {order.wilaya_name}")

    params = {
        "reference": _cut(order.order_number),
        "nom_client": _cut(order.customer_name),
        "telephone": normalize_phone(order.customer_phone),
        "adresse": address,
        "commune": commune_name,
        "code_wilaya": _wilaya_int(order),
        "montant": int(order.total),           # المبلغ للتحصيل، يشمل التوصيل
        "produit": PRODUCT_LABEL,
        "type": 1,                              # 1 = Livraison
        "stop_desk": stop_desk,
    }
    note = _cut(order.customer_note)
    if note:
        params["remarque"] = note
    return params


def create_order(order):
    """
    ينشئ الطرد عند World Express. يرجّع {"tracking_number": ...}.

    ⚠️ الطرد يتنشأ «prête à expédier» — لازم `validate_order` باش يتسلّم للشركة.
    """
    problems = check_order_data(order)
    if problems:
        raise WorldError("الطلبية ما تقبلهاش World Express: " + " | ".join(problems))

    commune_name, stop_desk = resolve_destination(order)
    params = build_order_params(order, commune_name, stop_desk)

    data = _request("POST", "/api/v1/create/order", params=params, may_create=True)

    tracking = data.get("tracking") if isinstance(data, dict) else None
    if not tracking:
        # success=true بلا رقم: الطرد اتنشأ على الأرجح — ما نخلّيوش الأدمن يعاود
        raise WorldError(
            "World Express قالت نجح الإنشاء بصح ما رجّعتش رقم التتبّع. "
            f"شوف لوحة ECOTRACK على المرجع {order.order_number} قبل ما تعاود.",
            parcel_may_exist=True,
        )
    return {"tracking_number": str(tracking).strip()}


def validate_order(tracking, ask_collection=None):
    """يأكّد الإرسال (Expédier) — بعدها ما يتبدّلش الطرد ولا يتمسح."""
    params = {"tracking": tracking}
    if ask_collection is not None:
        params["ask_collection"] = 1 if ask_collection else 0
    data = _request("POST", "/api/v1/valid/order", params=params)
    return (data or {}).get("message") or "تمّ التأكيد"


def get_order_status(tracking):
    """
    حالة الطرد. يرجّع {"state": "<كود>", "raw": {...}}.

    الأول `get/orders?tracking=` (فيه الحالة كاملة). الطرود المؤرشفة ما تظهرش
    هناك، فنرجعو لـ`get/tracking/info` ونأخذو آخر نشاط.
    """
    data = _request("GET", "/api/v1/get/orders", params={"tracking": tracking})
    rows = data.get("data") if isinstance(data, dict) else None
    for row in rows or []:
        if str(row.get("tracking", "")).strip() == str(tracking).strip() and row.get("status"):
            return {"state": str(row["status"]), "raw": row}

    info = _request("GET", "/api/v1/get/tracking/info", params={"tracking": tracking})
    activity = (info or {}).get("activity") or []
    for item in reversed(activity):
        st = item.get("status")
        if st and st != "notification_on_order":
            return {"state": str(st), "raw": info}
    raise WorldError("World Express ما رجّعتش حالة لهاد الطرد.")


def get_label_pdf(tracking):
    """البوردرو الرسمي (PDF) — نتأكّدو أنو PDF حقيقي وصغير."""
    resp = _request("GET", "/api/v1/get/order/label",
                    params={"tracking": tracking}, raw=True)
    data = resp.content or b""
    if len(data) > MAX_LABEL_BYTES:
        raise WorldError("ملف البوردرو كبير بزاف.")
    if not data.startswith(b"%PDF"):
        raise WorldError("World Express ما رجّعتش PDF للبوردرو.")
    return data


def test_connection():
    """يختبر التوكن. يرجّع (ok, رسالة)."""
    if not is_configured():
        return False, "ما كاينش WORLD_API_TOKEN فملف .env"
    try:
        # التوثيق يستعمل api_token كـquery فهاد النداء + نبعثو Bearer كذلك
        data = _request("GET", "/api/v1/validate/token",
                        params={"api_token": _token()})
    except WorldError as e:
        # الرد INVALID_TOKEN يجي success=false → يتحوّل لخطأ
        return False, str(e)
    msg = (data or {}).get("message")
    if msg == "VALID_TOKEN":
        return True, "✓ التوكن صالح — World Express مربوطة"
    return False, f"رد غير متوقّع: {msg}"


# ──────────────────────────── ترجمة الحالات ────────────────────────────

_DELIVERED = {"livre_non_encaisse", "encaisse_non_paye", "paiements_prets",
              "paye_et_archive", "livred", "encaissed", "payed"}
_RETURNED = {"retour_recu", "retour_archive", "return_received"}
_IN_TRANSIT = {"en_ramassage", "en_preparation_stock", "vers_hub", "en_hub",
               "vers_wilaya", "en_preparation", "en_livraison",
               "picked", "accepted_by_carrier", "dispatched_to_driver",
               "attempt_delivery"}


def map_state_to_order_status(state, delivery_type="home"):
    """
    يترجم حالة ECOTRACK لحالة طلبية عندنا. None = ما نبدّلوش شي.

    الأمان أولًا:
      • «راجعة» (ترجّع المخزون) فقط كي الطرد وصلنا فعلاً (retour_recu/archive).
        retour_chez_livreur / en_traitement / transit = مازال فالطريق → None.
      • «annule» و«suspendu» و«prete_a_expedier» → None.
      • ما نحطّوش «فالمكتب» تلقائيًا: ECOTRACK ما عندها حالة صريحة للمكتب.
    """
    key = _plain(state).replace(" ", "_")
    if not key:
        return None
    if key in _RETURNED:
        return "returned"
    if key in _DELIVERED:
        return "delivered"
    if key in _IN_TRANSIT:
        return "shipped"
    return None
