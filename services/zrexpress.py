"""
تكامل ZR Express API.

التوثيق: https://docs.zrexpress.app/reference/

⚠️ المصادقة تحتاج **قيمتين** (ماشي وحدة):
    X-Tenant  : معرّف حسابك عند ZR
    X-Api-Key : مفتاح الـAPI

الاثنين يتحطو فـ.env، ما يتكتبوش فالكود:
    ZR_TENANT_ID=xxxxx
    ZR_API_KEY=xxxxx

⚠️ فلسفة التصميم هنا: **الفشل ما يكسرش الطلبية**.
إذا ZR ما ردّش أو رجع خطأ، الطلبية تبقى محفوظة عندنا والأدمن يبعثها يدويًا.
ما نخسروش بيعة بسبب مشكل شبكة.
"""

import json
import os

import requests

BASE_URL = "https://api.zrexpress.app/api/v1.0"
TIMEOUT = 25


class ZRError(Exception):
    """خطأ من ZR Express — رسالتو تتعرض للأدمن بالعربية."""
    pass


def is_configured():
    """واش المفاتيح معمّرة فالإعدادات."""
    return bool(os.environ.get("ZR_TENANT_ID") and os.environ.get("ZR_API_KEY"))


def _headers():
    tenant = os.environ.get("ZR_TENANT_ID", "").strip()
    api_key = os.environ.get("ZR_API_KEY", "").strip()

    if not tenant or not api_key:
        raise ZRError(
            "مفاتيح ZR Express غير معمّرة. حط ZR_TENANT_ID و ZR_API_KEY فملف .env"
        )

    return {
        "X-Tenant": tenant,
        "X-Api-Key": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# ⚠️ نخزّنو آخر طلب ورد لـZR فالذاكرة باش صفحة التشخيص تعرضهم.
# ماشي فقاعدة البيانات: بيانات مؤقتة، وتتمسح مع إعادة التشغيل.
LAST_EXCHANGE = {}


def _request(method, path, **kwargs):
    """
    ينفّذ طلب لـZR ويترجم الأخطاء لرسائل عربية مفهومة.

    ⚠️ نترجمو أكواد HTTP لرسائل تقول للأدمن **واش يدير**، ماشي غير "خطأ 401".
    """
    url = f"{BASE_URL}{path}"

    try:
        response = requests.request(
            method, url, headers=_headers(), timeout=TIMEOUT, **kwargs
        )
    except requests.Timeout:
        raise ZRError("ZR Express ما ردّش فالوقت — عاود جرّب بعد شوية")
    except requests.RequestException as e:
        raise ZRError(f"ما قدرناش نوصلو لـZR Express: {e}")

    # نسجّلو التبادل قبل أي رمي خطأ
    try:
        body_preview = response.text[:4000]
    except Exception:
        body_preview = ""

    LAST_EXCHANGE.update({
        "method": method,
        "path": path,
        "payload": kwargs.get("json"),
        "status": response.status_code,
        "response": body_preview,
    })

    if response.status_code == 401:
        raise ZRError("مفاتيح ZR Express غير صحيحة — راجع ZR_TENANT_ID و ZR_API_KEY")
    if response.status_code == 403:
        raise ZRError("المفتاح ما عندوش الصلاحيات المطلوبة عند ZR Express")
    if response.status_code == 404:
        raise ZRError("ما لقيناش المطلوب عند ZR Express")

    if response.status_code >= 400:
        # ⚠️ ZR مبني على ASP.NET: أخطاء التحقق تجي فـ"errors" على شكل
        #    {"errors": {"customerPhone": ["The field is required"]}}
        #    و"title" يعطي غير الجملة العامة "One or more validation errors
        #    occurred" — بلا فايدة. لازم نقراو "errors" وإلا ما نعرفوش
        #    شنو الحقل الغالط.
        detail = ""
        try:
            payload = response.json()

            field_errors = payload.get("errors")
            if isinstance(field_errors, dict) and field_errors:
                parts = []
                for field, messages in field_errors.items():
                    if isinstance(messages, list):
                        text = "، ".join(str(m) for m in messages)
                    else:
                        text = str(messages)
                    parts.append(f"{field}: {text}")
                detail = " | ".join(parts)

            if not detail:
                detail = (payload.get("detail") or payload.get("message")
                          or payload.get("title") or response.text[:300])
        except Exception:
            detail = response.text[:300]

        raise ZRError(f"ZR Express رجع خطأ ({response.status_code}): {detail}")

    if not response.content:
        return {}

    try:
        return response.json()
    except Exception:
        return {}


def get_territories(max_pages=20):
    """
    قائمة المناطق عند ZR مع معرّفاتها — **كل الصفحات**.

    ⚠️ ZR يرجّع النتائج على صفحات. الطلب الأول كان يجيب 200 فقط ويظن
    أنها كل شيء. إذا المناطق هي البلديات (1541 فالجزائر)، كنا ناخذو
    13% منها فقط — والباقي ما يتربطش أبدًا.

    max_pages : حد أمان باش ما ندخلوش فحلقة لا نهائية إذا ZR بدّل الرد.
    """
    all_items = []
    page = 1

    while page <= max_pages:
        data = _request("POST", "/territories/search",
                        json={"page": page, "pageSize": 200})

        items = data.get("data") or data.get("items") or []
        if not items:
            break

        all_items.extend(items)

        # وقفنا إذا الصفحة ناقصة (يعني هي الأخيرة)
        if len(items) < 200:
            break

        page += 1

    return all_items


def inspect_territories(sample=3):
    """
    تشخيص: يرجّع عيّنة خام من بيانات ZR باش نعرفو أسماء الحقول الحقيقية.

    ⚠️ ضروري لأن التوثيق ما يوضّحش واش "territory" معناها ولاية ولا بلدية.
    بلا هاد المعرفة، أي ربط نبنيوه يكون تخمين.
    """
    items = get_territories()

    return {
        "count": len(items),
        "fields": sorted({k for it in items[:50] for k in it.keys()}),
        "sample": items[:sample],
    }


def get_delivery_rates():
    """
    أسعار التوصيل الفعلية لكل الوجهات (صفحة «Tarifs» عند ZR).

    ⚠️ هذا هو **مصدر التغطية الحقيقي** للتوصيل للمنزل: أي ولاية موجودة
    فجدول الأسعار = ZR يوصل ليها. ماشي حقل canSend ديال البلديات.
    """
    data = _request("GET", "/delivery-pricing/rates")
    if isinstance(data, list):
        return data
    return data.get("data") or data.get("items") or []


def _rate_wilaya_code(item):
    """يستخرج رمز الولاية من سطر سعر."""
    for key in ("wilayaCode", "stateCode", "code", "destinationCode", "number"):
        raw = item.get(key)
        if raw is not None and str(raw).strip().isdigit():
            return str(int(str(raw).strip())).zfill(2)

    postal = str(item.get("postalCode") or "").strip()
    if len(postal) >= 2 and postal[:2].isdigit():
        return postal[:2]

    return None


def _rate_price(item, *keys):
    """يقرا سعر من سطر — يرجع None إذا ماكانش ولا كان صفر (يعني غير متوفر)."""
    for key in keys:
        raw = item.get(key)
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        return value if value > 0 else None
    return None


def get_covered_wilayas():
    """
    الولايات اللي ZR يوصل ليها فعلاً، من جدول الأسعار.

    يرجّع dict: رمز الولاية → {"home": bool, "office": bool}

    ⚠️ سعر 0 معناه الخدمة غير متوفرة (مثلاً In Guezzam: مكتب 0 دج =
    ماكانش توصيل لمكتب تماك)، ماشي «توصيل مجاني».
    """
    coverage = {}

    for item in get_delivery_rates():
        code = _rate_wilaya_code(item)
        if not code:
            continue

        home = _rate_price(item, "homePrice", "home", "domicile", "deliveryPrice")
        office = _rate_price(item, "officePrice", "office", "stopDesk",
                             "stopdeskPrice", "desk")

        entry = coverage.setdefault(code, {"home": False, "office": False})
        entry["home"] = entry["home"] or home is not None
        entry["office"] = entry["office"] or office is not None

    return coverage


def _check_parcel_data(order):
    """
    يفحص بيانات الطلبية قبل ما نبعثوها لـZR.

    ⚠️ ZR يرد بـ400 «One or more validation errors» بلا ما يقول شنو ناقص
    فبعض الحالات. هاد الفحص يمسك المشاكل المعروفة ويعطي رسالة عربية
    واضحة تقول بالضبط شنو لازم يتصلّح.
    """
    problems = []

    digits = "".join(c for c in (order.customer_phone or "") if c.isdigit())
    if digits.startswith("00213"):
        digits = "0" + digits[5:]
    elif digits.startswith("213"):
        digits = "0" + digits[3:]

    valid_mobile = len(digits) == 10 and digits[:2] in ("05", "06", "07")
    valid_landline = len(digits) == 9 and digits[:2] in ("02", "03", "04")

    if not (valid_mobile or valid_landline):
        problems.append(
            f"رقم الهاتف «{order.customer_phone}» غير صحيح — "
            "ZR يقبل غير أرقام جزائرية (05/06/07 + 8 أرقام)"
        )

    if not order.customer_name or len(order.customer_name.strip()) < 3:
        problems.append("اسم الزبون قصير بزاف")

    if order.delivery_type == "office":
        if not order.pickup_point_name:
            problems.append("التوصيل لمكتب بلا اسم مكتب")
    else:
        if not order.commune:
            problems.append("التوصيل للمنزل بلا بلدية")

    if not order.total or order.total <= 0:
        problems.append("مبلغ الطلبية صفر")

    return problems


def build_parcel_payload(order, territory_id=None):
    """
    يبني الـpayload اللي يتبعث لـZR — منفصل باش صفحة التشخيص تعرضو
    بلا ما تبعث حاجة.
    """
    is_office = order.delivery_type == "office"

    # ⚠️ كل نوع توصيل يبعث معلومة مختلفة:
    #   • منزل → البلدية + العنوان الدقيق
    #   • مكتب → اسم المكتب (البلدية ما عندهاش معنى هنا)
    if is_office:
        address = order.pickup_point_name or order.wilaya_name
        commune = ""
    else:
        address = order.address_note or order.commune or order.wilaya_name
        commune = order.commune or ""

    # ⚠️ نبعثو الهاتف أرقام فقط — ZR يرفض المسافات والرموز
    phone = "".join(c for c in (order.customer_phone or "") if c.isdigit())
    if phone.startswith("00213"):
        phone = "0" + phone[5:]
    elif phone.startswith("213"):
        phone = "0" + phone[3:]

    payload = {
        "customerName": (order.customer_name or "").strip(),
        "customerPhone": phone,
        "address": address,
        "commune": commune,
        "wilaya": order.wilaya_name or "",
        "amount": order.total,
        "isStopDesk": is_office,
        "reference": order.order_number,
        "note": order.customer_note or "",
        "productsCount": order.item_count,
        "productName": "، ".join(item.item_name for item in order.items[:3])[:200],
    }

    if is_office and order.pickup_point_id:
        payload["pickupPointId"] = order.pickup_point_id

    if territory_id:
        payload["territoryId"] = territory_id

    return payload


def create_parcel(order, territory_id=None):
    """
    ينشئ طرد عند ZR Express من طلبية عندنا.

    يرجع dict فيه tracking_number وparcel_id.
    يرمي ZRError إذا فشل — والمستدعي يقرر واش يدير.
    """
    # نفحصو أولاً — أحسن رسالة عربية واضحة من 400 غامض
    problems = _check_parcel_data(order)
    if problems:
        raise ZRError("الطلبية ما تقبلهاش ZR: " + " | ".join(problems))

    payload = build_parcel_payload(order, territory_id)

    data = _request("POST", "/parcels", json=payload)

    tracking = (
        data.get("trackingNumber")
        or data.get("tracking_number")
        or (data.get("data") or {}).get("trackingNumber")
    )

    if not tracking:
        raise ZRError("ZR Express ما رجعش رقم تتبّع — راجع الطلبية عندهم")

    return {
        "tracking_number": tracking,
        "parcel_id": data.get("parcelId") or data.get("id") or (data.get("data") or {}).get("id"),
        "raw": data,
    }


def get_parcel_status(tracking_number):
    """
    حالة طرد برقم التتبّع.
    نستعملوها باش نحدّثو حالة الطلبية تلقائيًا بدل ما الأدمن يقلّب عند ZR.
    """
    data = _request("GET", f"/parcels/{tracking_number}")

    return {
        "state": data.get("stateName") or data.get("state") or "غير معروف",
        "state_id": data.get("stateId"),
        "updated_at": data.get("updatedAt") or data.get("lastUpdate"),
        "raw": data,
    }


def get_parcel_history(parcel_id):
    """تاريخ حالات الطرد — مفيد لعرض خط زمني مفصّل."""
    data = _request("GET", f"/parcels/{parcel_id}/state-history")
    return data.get("data") or data if isinstance(data, list) else []


def test_connection():
    """
    يجرّب الاتصال ويرجع (نجح؟، رسالة).
    نستعملوها فلوحة التحكم باش الأدمن يتأكد من المفاتيح قبل أول طلبية حقيقية.
    """
    if not is_configured():
        return False, "المفاتيح غير معمّرة فملف .env"

    try:
        territories = get_territories()
    except ZRError as e:
        return False, str(e)

    if not territories:
        return True, "الاتصال نجح، لكن ما رجعت حتى منطقة — راجع حسابك عند ZR"

    return True, f"✓ الاتصال نجح — لقينا {len(territories)} منطقة"


# ═══════════════════════════════════════════════════════════
# ربط ولاياتنا بمناطق ZR
# ═══════════════════════════════════════════════════════════

def _normalize_arabic(text):
    """
    يوحّد النص العربي باش المقارنة تخدم رغم اختلاف الكتابة.

    مثلاً "الجزائر" و "الجزایر" و "الجزائر  " يولّيو كيف كيف.
    بلا هاد التوحيد، أغلب الولايات ما تتربطش.
    """
    if not text:
        return ""

    text = str(text).strip()

    # توحيد أشكال الحروف اللي تتكتب بطرق مختلفة
    replacements = {
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
        "ى": "ي", "ئ": "ي", "ی": "ي",
        "ة": "ه",
        "ؤ": "و",
        "ـ": "",          # التطويل
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # نحيّدو التشكيل
    text = "".join(ch for ch in text if not ("\u064B" <= ch <= "\u0652"))

    # نحيّدو "ال" التعريف والمسافات الزائدة
    text = " ".join(text.split())
    if text.startswith("ال") and len(text) > 3:
        text = text[2:]

    return text.lower()


def _extract_territory_fields(item):
    """
    يستخرج (المعرّف، الاسم، رمز الولاية) من عنصر منطقة عند ZR.

    ⚠️ ما نعرفوش بالضبط أسماء الحقول اللي يرجعها ZR، فنجرّبو الاحتمالات
    المعروفة. هكذا الكود ما يتكسرش إذا بدّلو التسمية.
    """
    tid = item.get("id") or item.get("territoryId") or item.get("code")

    name = (item.get("name") or item.get("wilaya") or item.get("label")
            or item.get("nameAr") or item.get("designation") or "")

    # رمز الولاية (1-58) إذا كان موجود
    code = (item.get("wilayaCode") or item.get("wilaya_code")
            or item.get("stateCode") or item.get("number"))

    return tid, name, code


def find_territory_id(carrier_code, commune_external_id):
    """
    معرّف المنطقة اللي يتبعث لـZR = معرّف البلدية.

    ⚠️ الطلبية تخزّن المعرّف وقت الطلب، فحتى لو تبدّلت بيانات ZR بعدين،
    الإرسال يبقى يخدم بنفس المعرّف اللي اختارو الزبون.
    """
    return commune_external_id or None


# ═══════════════════════════════════════════════════════════
# المكاتب — من ملف محلي، بلا API
# ═══════════════════════════════════════════════════════════

_OFFICES_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "zr_offices.json")
_offices_cache = None


def get_offices():
    """
    كل مكاتب ZR (79 مكتب) — من ملف محلي `data/zr_offices.json`.

    ⚠️ ليش ملف محلي وماشي API؟
    ZR ما عندوش مسار API للمكاتب (جرّبنا 5 مسارات، كلهم رجعو 404).
    القائمة ثابتة وتتبدّل نادرًا، فنخزّنوها عندنا. باش تحدّثها:
    نسخ القائمة من صفحة «Nos Bureaux» عند ZR وحدّث الملف.

    ملاحظة مهمة: رمز الولاية ماخوذ من **رقم اسم المكتب**، ماشي من الرمز
    البريدي — بعض المكاتب الجديدة رمزها البريدي ديال الولاية القديمة
    (مثلاً المنيعة 58 رمزها البريدي 47001).
    """
    global _offices_cache

    if _offices_cache is None:
        with open(_OFFICES_FILE, encoding="utf-8") as f:
            _offices_cache = json.load(f)

    return _offices_cache


def get_pickup_points(wilaya_code=None):
    """مكاتب الاستلام لولاية معيّنة — للزبون فصفحة الطلب."""
    offices = [o for o in get_offices() if o.get("pickup")]

    if wilaya_code:
        target = str(wilaya_code).zfill(2)
        offices = [o for o in offices if o["wilaya_code"] == target]

    return [
        {
            "id": o["name"],          # الاسم هو المعرّف — يتبعث لـZR كيما هو
            "name": o["name"],
            "address": o.get("address") or o.get("commune", ""),
            "wilaya_code": o["wilaya_code"],
        }
        for o in offices
    ]


def get_office_wilayas():
    """رموز الولايات اللي فيها مكتب استلام واحد على الأقل."""
    return {o["wilaya_code"] for o in get_offices() if o.get("pickup")}


# ═══════════════════════════════════════════════════════════
# المزامنة — من بياناتنا المحلية، بلا API
# ═══════════════════════════════════════════════════════════

def sync_communes():
    """
    يبني تغطية ZR من **بياناتنا المحلية** — ما نطلبو والو من API.

    القاعدة (بالضبط كيما طلب بكير):

      🏠 التوصيل للمنزل
         أي ولاية عندها سعر توصيل للمنزل فجدول «أسعار التوصيل»
         → **كل بلدياتها** (من قائمتنا الرسمية 1541) متاحة.
         ما نسألو ZR على البلديات — عندنا القائمة.

      🏢 التوصيل للمكتب
         من ملف المكاتب المحلي (79 مكتب). الولاية اللي فيها مكتب
         يقبل استلام → خيار المكتب متاح فيها.

    ⚠️ صفر ولا فارغ فسعر المنزل = الشركة ما توصلش لهاد الولاية.
    """
    from models import db, Carrier, Commune, CarrierCommune, ShippingRate

    carrier = Carrier.query.filter_by(code="zrexpress").first()
    if not carrier:
        raise ZRError("شركة ZR Express ماكانتش فقاعدة البيانات")

    our_communes = Commune.query.all()
    if not our_communes:
        raise ZRError(
            "البلديات ماكانتش فقاعدة البيانات. شغّل: python seed_communes.py"
        )

    # ---------- 1) ولايات التوصيل للمنزل (من أسعارنا) ----------
    home_wilayas = {
        r.wilaya_code
        for r in ShippingRate.query.filter_by(carrier_id=carrier.id).all()
        if r.home_price and r.home_price > 0
    }

    if not home_wilayas:
        raise ZRError(
            "ماكانش ولا ولاية عندها سعر توصيل للمنزل. "
            "روح لـ«أسعار التوصيل» وعمّر الأسعار أولاً."
        )

    # ---------- 2) ولايات المكاتب (من الملف المحلي) ----------
    office_wilayas = get_office_wilayas()
    offices_total = len(get_offices())

    # ---------- 3) معرّفات البلديات (إثراء اختياري) ----------
    # ⚠️ هاد الخطوة **ما تقررش التغطية** — التغطية تقرّرت فوق من بياناتنا.
    # هنا غير نعمّرو external_id لأن ZR يحتاجو وقت إنشاء الطرد.
    # إذا فشلت، التغطية تبقى صحيحة والطلبيات تتبعث بالاسم.
    external_ids = {}
    id_warning = None

    try:
        index = {}
        for c in our_communes:
            index[(c.wilaya_code, _normalize_arabic(c.name_ar))] = c
            if c.name_fr:
                index.setdefault((c.wilaya_code, _normalize_arabic(c.name_fr)), c)

        for item in get_territories():
            if (item.get("level") or "").lower() != "commune":
                continue

            tid = item.get("id")
            postal = str(item.get("postalCode") or "").strip()
            if not tid or len(postal) < 2 or not postal[:2].isdigit():
                continue

            w = postal[:2]
            commune = (
                index.get((w, _normalize_arabic(item.get("nameArabic") or "")))
                or index.get((w, _normalize_arabic(item.get("name") or "")))
            )
            if commune is not None:
                # أول معرّف يكفي — ما نكتبوش فوقو بمنطقة فرعية أخرى
                external_ids.setdefault(commune.id, str(tid))
    except Exception as e:
        id_warning = (
            f"ما قدرناش نجيبو معرّفات البلديات من ZR ({e}). "
            "التغطية صحيحة، لكن الطلبيات الجديدة تتبعث بالاسم بلا معرّف."
        )

    # ---------- 4) نكتبو الروابط لكل بلدية ----------
    existing = {
        link.commune_id: link
        for link in CarrierCommune.query.filter_by(carrier_id=carrier.id).all()
    }

    home_count, office_count = 0, 0

    for commune in our_communes:
        has_home = commune.wilaya_code in home_wilayas
        has_office = commune.wilaya_code in office_wilayas

        link = existing.get(commune.id)
        if link is None:
            link = CarrierCommune(carrier_id=carrier.id, commune_id=commune.id)
            db.session.add(link)
            existing[commune.id] = link

        # نحافظو على المعرّف القديم إذا الجديد ما توفّرش
        new_id = external_ids.get(commune.id)
        if new_id:
            link.external_id = new_id

        link.has_home_delivery = has_home
        link.has_pickup_point = has_office
        link.can_send = has_home or has_office

        if has_home:
            home_count += 1
        if has_office:
            office_count += 1

    db.session.commit()

    return {
        "coverage_source": "أسعار التوصيل عندنا + ملف المكاتب المحلي",
        "wilayas_covered": len(home_wilayas),
        "office_wilayas": len(office_wilayas),
        "offices_total": offices_total,
        "home_communes": home_count,
        "office_communes": office_count,
        "matched_ids": len(external_ids),
        "our_total": len(our_communes),
        "warnings": [id_warning] if id_warning else [],
    }
