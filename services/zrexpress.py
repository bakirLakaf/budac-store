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

    if response.status_code == 401:
        raise ZRError("مفاتيح ZR Express غير صحيحة — راجع ZR_TENANT_ID و ZR_API_KEY")
    if response.status_code == 403:
        raise ZRError("المفتاح ما عندوش الصلاحيات المطلوبة عند ZR Express")
    if response.status_code == 404:
        raise ZRError("ما لقيناش المطلوب عند ZR Express")

    if response.status_code >= 400:
        # ZR يرجع تفاصيل الخطأ فـ"detail" أو "title"
        try:
            payload = response.json()
            detail = payload.get("detail") or payload.get("title") or response.text[:200]
        except Exception:
            detail = response.text[:200]
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


def create_parcel(order, territory_id=None):
    """
    ينشئ طرد عند ZR Express من طلبية عندنا.

    order        : كائن Order
    territory_id : معرّف الولاية عند ZR (إذا معروف)

    يرجع dict فيه trackingNumber وparcelId.
    يرمي ZRError إذا فشل — والمستدعي يقرر واش يدير.
    """
    payload = {
        "customerName": order.customer_name,
        "customerPhone": order.customer_phone,
        "address": order.address_note or order.commune or order.wilaya_name,
        "commune": order.commune or "",
        "amount": order.total,
        "isStopDesk": order.delivery_type == "office",
        "reference": order.order_number,
        "note": order.customer_note or "",
        "productsCount": order.item_count,
        "productName": "، ".join(item.item_name for item in order.items[:3])[:200],
    }

    if territory_id:
        payload["territoryId"] = territory_id

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


def sync_communes():
    """
    يزامن تغطية ZR: التوصيل للمنزل والمكتب.

    ⚠️ قاعدة التصميم (مصححة بعد ما تبيّن أن canSend ديال البلديات غالط):

      • التوصيل للمنزل → **كل بلديات الولاية** اللي ZR يوصل ليها.
        المصدر: جدول أسعار ZR (صفحة Tarifs). إذا الولاية موجودة فيه،
        كل بلدياتها متاحة. ZR يوصل للبلدية بالاسم، ما يحتاجش معرّف
        لكل بلدية.

      • التوصيل للمكتب → **من قائمة المكاتب الحقيقية** (Nos Bureaux).
        ماشي من البلديات. الزبون يختار مكتب، والولاية اللي ماكانش فيها
        مكتب ما تعرضش هاد الخيار.

    المعرّفات (territories) نستعملوهم غير باش نعمّرو external_id لما
    يتوفّر — يسهّل إنشاء الطرد، لكن غيابو ما يمنعش التوصيل.
    """
    from models import db, Carrier, Commune, CarrierCommune

    carrier = Carrier.query.filter_by(code="zrexpress").first()
    if not carrier:
        raise ZRError("شركة ZR Express ماكانتش فقاعدة البيانات")

    our_communes = Commune.query.all()
    if not our_communes:
        raise ZRError(
            "البلديات ماكانتش فقاعدة البيانات. "
            "شغّل: python seed_communes.py"
        )

    # ---------- 1) تغطية التوصيل للمنزل (من الأسعار) ----------
    coverage = {}
    coverage_source = "أسعار ZR"
    try:
        coverage = get_covered_wilayas()
    except ZRError as e:
        coverage_error = str(e)
    else:
        coverage_error = None

    # ---------- 2) الولايات اللي فيها مكاتب ----------
    office_wilayas = set()
    office_count = 0
    office_error = None
    try:
        offices = get_offices()
        office_count = len(offices)
        office_wilayas = {
            o["wilaya_code"] for o in offices
            if o["accepts_pickup"] and o["wilaya_code"]
        }
    except ZRError as e:
        office_error = str(e)

    # ⚠️ إذا الأسعار ما جاوش، نبنيو التغطية من الولايات اللي فيها مكاتب
    # + الولايات اللي ظهرت فالمناطق. أحسن من تعطيل كلش.
    if not coverage:
        coverage_source = "المكاتب والمناطق (الأسعار ما توفّرتش)"
        for code in office_wilayas:
            coverage[code] = {"home": True, "office": True}

    # ---------- 3) معرّفات البلديات (اختيارية) ----------
    external_ids = {}
    territories_error = None
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
            if not tid:
                continue

            postal = str(item.get("postalCode") or "").strip()
            w_code = postal[:2] if len(postal) >= 2 and postal[:2].isdigit() else None
            if not w_code:
                continue

            commune = (
                index.get((w_code, _normalize_arabic(item.get("nameArabic") or "")))
                or index.get((w_code, _normalize_arabic(item.get("name") or "")))
            )
            if commune is None:
                continue

            # أول معرّف يكفي — ما نكتبوش فوقو بمنطقة فرعية أخرى
            external_ids.setdefault(commune.id, str(tid))

            # الولاية عندها منطقة عند ZR → تأكيد إضافي للتغطية
            coverage.setdefault(w_code, {"home": True, "office": False})
    except ZRError as e:
        territories_error = str(e)

    # ---------- 4) نكتبو الروابط ----------
    existing = {
        link.commune_id: link
        for link in CarrierCommune.query.filter_by(carrier_id=carrier.id).all()
    }

    home_count, office_count_communes = 0, 0

    for commune in our_communes:
        entry = coverage.get(commune.wilaya_code)

        has_home = bool(entry and entry.get("home"))
        # المكتب: متاح إذا الولاية فيها مكتب فعلي
        has_office = commune.wilaya_code in office_wilayas
        if entry and entry.get("office") and office_wilayas:
            has_office = has_office and True

        link = existing.get(commune.id)
        if link is None:
            link = CarrierCommune(carrier_id=carrier.id, commune_id=commune.id)
            db.session.add(link)
            existing[commune.id] = link

        link.external_id = external_ids.get(commune.id) or link.external_id or ""
        link.has_home_delivery = has_home
        link.has_pickup_point = has_office
        link.can_send = has_home or has_office

        if has_home:
            home_count += 1
        if has_office:
            office_count_communes += 1

    db.session.commit()

    covered_wilayas = sorted(
        code for code, v in coverage.items() if v.get("home")
    )

    warnings = [w for w in (coverage_error, office_error, territories_error) if w]

    return {
        "coverage_source": coverage_source,
        "wilayas_covered": len(covered_wilayas),
        "office_wilayas": len(office_wilayas),
        "offices_total": office_count,
        "home_communes": home_count,
        "office_communes": office_count_communes,
        "matched_ids": len(external_ids),
        "our_total": len(our_communes),
        "warnings": warnings,
    }

def find_territory_id(carrier_code, commune_external_id):
    """
    معرّف المنطقة اللي يتبعث لـZR = معرّف البلدية.

    ⚠️ الطلبية تخزّن المعرّف وقت الطلب، فحتى لو تبدّلت بيانات ZR بعدين،
    الإرسال يبقى يخدم بنفس المعرّف اللي اختارو الزبون.
    """
    return commune_external_id or None


# ═══════════════════════════════════════════════════════════
# نقاط الاستلام (Stop Desk)
# ═══════════════════════════════════════════════════════════

# المسارات المحتملة لقائمة المكاتب عند ZR.
# ⚠️ نجرّبوهم بالترتيب لأن الحساب/الإصدار يقرّر واحد منهم. أول واحد يرد
# ببيانات هو اللي نستعملو، ونسجّلو اسمو باش التشخيص يبيّن شنو خدم فعلاً.
_OFFICE_ENDPOINTS = [
    ("POST", "/agencies/search", {"page": 1, "pageSize": 500}),
    ("POST", "/offices/search", {"page": 1, "pageSize": 500}),
    ("POST", "/pickup-points/search", {"page": 1, "pageSize": 500}),
    ("GET", "/agencies", None),
    ("GET", "/offices", None),
]


def _fetch_offices_raw():
    """
    يجيب قائمة المكاتب الخام من ZR، ويرجّع (العناصر، المسار اللي خدم).

    ⚠️ ما نخمّنوش مسار واحد ونعتبروه صحيح: نجرّبو الاحتمالات ونرجّعو
    اللي رد فعلاً ببيانات. إذا كلهم فشلو، نرمي خطأ فيه التفاصيل باش
    الأدمن يشوف المشكل الحقيقي بدل قائمة فارغة بلا سبب.
    """
    errors = []

    for method, path, body in _OFFICE_ENDPOINTS:
        try:
            data = _request(method, path, **({"json": body} if body else {}))
        except ZRError as e:
            errors.append(f"{path}: {e}")
            continue

        if isinstance(data, list):
            items = data
        else:
            items = data.get("data") or data.get("items") or data.get("results") or []

        if items:
            return items, path

        errors.append(f"{path}: رد فارغ")

    raise ZRError(
        "ما لقيناش قائمة المكاتب عند ZR. جرّبنا: "
        + " | ".join(errors[:5])
    )


def _office_wilaya_code(item):
    """يستخرج رمز الولاية من مكتب — من الرمز البريدي أولاً ثم من حقول أخرى."""
    postal = str(item.get("postalCode") or item.get("cp") or "").strip()
    if len(postal) >= 2 and postal[:2].isdigit():
        return postal[:2]

    raw = (item.get("wilayaCode") or item.get("stateCode")
           or item.get("wilaya") or item.get("territoryCode"))
    if raw is not None and str(raw).strip().isdigit():
        return str(int(str(raw).strip())).zfill(2)

    return None


def _office_accepts_pickup(item):
    """
    واش المكتب يقبل استلام الزبون.

    ⚠️ فواجهة ZR الخانة اسمها Pickup (Oui/Non). بعض المكاتب مراكز فرز
    داخلية ما يستقبلوش الزبائن — ما نعرضوهمش، وإلا الزبون يمشي لمكتب
    مغلق فوجهو.
    """
    for key in ("pickup", "hasPickup", "isPickupPoint", "hasPickupPoint",
                "allowPickup", "isStopDesk"):
        if key in item:
            value = item[key]
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("oui", "yes", "true", "1")

    # ماكانش الحقل → نعتبروه مقبول (أحسن من إخفاء كل المكاتب)
    return True


def get_offices():
    """
    كل مكاتب ZR الحقيقية (اللي تبان فصفحة «Nos Bureaux»).

    هذا هو **المصدر الوحيد** لخيار «التوصيل للمكتب» — ماشي البلديات.
    """
    items, source = _fetch_offices_raw()
    results = []

    for it in items:
        oid = it.get("id") or it.get("code") or it.get("agencyId")
        if not oid:
            continue

        name = (it.get("nameArabic") or it.get("name")
                or it.get("label") or "").strip()

        results.append({
            "id": str(oid),
            "name": name or "مكتب",
            "address": (it.get("address") or it.get("addressArabic") or "").strip(),
            "commune": (it.get("commune") or it.get("city") or "").strip(),
            "wilaya_code": _office_wilaya_code(it),
            "accepts_pickup": _office_accepts_pickup(it),
            "_source": source,
        })

    return results


def get_pickup_points(wilaya_code=None):
    """
    مكاتب الاستلام لولاية معيّنة — للزبون فصفحة الطلب.

    نعرضو فقط المكاتب اللي تقبل استلام الزبون (Pickup = Oui).
    """
    offices = get_offices()

    if wilaya_code:
        target = str(wilaya_code).zfill(2)
        offices = [o for o in offices if o["wilaya_code"] == target]

    return [
        {
            "id": o["id"],
            "name": o["name"],
            "address": o["address"] or o["commune"],
            "wilaya_code": o["wilaya_code"],
        }
        for o in offices if o["accepts_pickup"]
    ]


def get_office_wilayas():
    """
    رموز الولايات اللي فيها مكتب استلام واحد على الأقل.
    نستعملوها باش نعرفو وين «التوصيل للمكتب» متاح فعلاً.
    """
    return {
        o["wilaya_code"]
        for o in get_offices()
        if o["accepts_pickup"] and o["wilaya_code"]
    }
