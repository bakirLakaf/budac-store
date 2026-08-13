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
    أسعار التوصيل الفعلية لكل المناطق.
    مفيدة باش نحدّثو جدول أسعارنا تلقائيًا بدل التعديل اليدوي.
    """
    data = _request("GET", "/delivery-pricing/rates")
    return data.get("data") or data if isinstance(data, list) else []


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
    يربط بلدياتنا الرسمية بمعرّفات ZR.

    ⚠️ البلديات عندنا أصلاً (القائمة الرسمية 1541). هنا ما نزيدو حتى بلدية —
    نربط فقط: "بلدية X عند ZR معرّفها Y، وواش يوصل ليها".

    المطابقة بالاسم بعد التوحيد + رمز الولاية من الرمز البريدي.
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

    # فهرس: (رمز الولاية، الاسم الموحّد) → البلدية عندنا
    index = {}
    for c in our_communes:
        index[(c.wilaya_code, _normalize_arabic(c.name_ar))] = c
        if c.name_fr:
            index.setdefault((c.wilaya_code, _normalize_arabic(c.name_fr)), c)

    items = get_territories()
    if not items:
        raise ZRError("ZR ما رجعش حتى منطقة")

    # الروابط الموجودة
    existing = {
        link.commune_id: link
        for link in CarrierCommune.query.filter_by(carrier_id=carrier.id).all()
    }

    linked, no_match, skipped_level = 0, [], 0
    matched_ids = set()

    for item in items:
        if (item.get("level") or "").lower() != "commune":
            skipped_level += 1
            continue

        external_id = item.get("id")
        if not external_id:
            continue

        postal = str(item.get("postalCode") or "").strip()
        wilaya_code = postal[:2] if len(postal) >= 2 and postal[:2].isdigit() else None
        if not wilaya_code:
            continue

        name_ar = (item.get("nameArabic") or "").strip()
        name_fr = (item.get("name") or "").strip()

        # نجرّبو بالعربية ثم بالفرنسية
        commune = (index.get((wilaya_code, _normalize_arabic(name_ar)))
                   or index.get((wilaya_code, _normalize_arabic(name_fr))))

        if commune is None:
            if name_ar or name_fr:
                no_match.append(f"{wilaya_code} ~ {name_ar or name_fr}")
            continue

        delivery = item.get("delivery") or {}

        link = existing.get(commune.id)
        if link is None:
            link = CarrierCommune(carrier_id=carrier.id, commune_id=commune.id)
            db.session.add(link)
            existing[commune.id] = link

        link.external_id = str(external_id)
        link.has_home_delivery = bool(delivery.get("hasHomeDelivery", True))
        link.has_pickup_point = bool(delivery.get("hasPickupPoint", False))
        link.can_send = bool(delivery.get("canSend"))

        matched_ids.add(commune.id)
        linked += 1

    # بلديات ما لقيناهاش عند ZR هاد المرة → نعطّلوها
    deactivated = 0
    for cid, link in existing.items():
        if cid not in matched_ids and link.can_send:
            link.can_send = False
            deactivated += 1

    db.session.commit()

    deliverable = CarrierCommune.query.filter_by(
        carrier_id=carrier.id, can_send=True
    ).count()

    wilayas_covered = (
        db.session.query(Commune.wilaya_code)
        .join(CarrierCommune, CarrierCommune.commune_id == Commune.id)
        .filter(CarrierCommune.carrier_id == carrier.id,
                CarrierCommune.can_send.is_(True))
        .distinct()
        .count()
    )

    return {
        "total_from_zr": len(items),
        "linked": linked,
        "deliverable": deliverable,
        "deactivated": deactivated,
        "wilayas_covered": wilayas_covered,
        "our_total": len(our_communes),
        "no_match": no_match[:20],
        "no_match_count": len(no_match),
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

def get_pickup_points(wilaya_code=None):
    """
    مكاتب الاستلام عند ZR.

    ⚠️ الزبون اللي يختار "التوصيل للمكتب" لازم يختار **مكتب محدد**،
    ماشي بلدية. لهذا نجيبو القائمة من الشركة مباشرة.

    أسماء الحقول مجرّبة على عدة احتمالات لأن التوثيق ما يوضحهاش.
    """
    try:
        data = _request("POST", "/pickup-points/search",
                        json={"page": 1, "pageSize": 500})
    except ZRError:
        # بعض الحسابات تستعمل مسار آخر
        data = _request("POST", "/agencies/search",
                        json={"page": 1, "pageSize": 500})

    items = data.get("data") or data.get("items") or []
    results = []

    for it in items:
        pid = it.get("id") or it.get("code")
        if not pid:
            continue

        name = (it.get("nameArabic") or it.get("name")
                or it.get("label") or "").strip()

        address = (it.get("address") or it.get("addressArabic") or "").strip()
        postal = str(it.get("postalCode") or "").strip()
        w_code = postal[:2] if len(postal) >= 2 and postal[:2].isdigit() else None

        # بعض الردود تعطي الولاية كحقل مستقل
        if not w_code:
            raw = it.get("wilayaCode") or it.get("stateCode")
            if raw and str(raw).isdigit():
                w_code = str(int(raw)).zfill(2)

        if wilaya_code and w_code != str(wilaya_code).zfill(2):
            continue

        results.append({
            "id": str(pid),
            "name": name or "مكتب",
            "address": address,
            "wilaya_code": w_code,
        })

    return results
