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
    يجيب بلديات ZR ويخزّنهم عندنا.

    ⚠️ بنية بيانات ZR (مفحوصة من ردّو الحقيقي):
        level        = "commune"
        id           = UUID — هو territoryId اللي يتبعث عند إنشاء الطرد
        nameArabic   = اسم البلدية بالعربية
        postalCode   = "01035" → أول رقمين "01" = رمز الولاية
        delivery.canSend = واش ZR يقدر يبعث لهاد البلدية

    نتجاهلو البلديات اللي canSend = false — ما نعرضوهاش للزبون بدل ما
    نقبلو طلبية ما نقدروش نبعثوها.
    """
    from models import db, Carrier, Commune

    carrier = Carrier.query.filter_by(code="zrexpress").first()
    if not carrier:
        raise ZRError("شركة ZR Express ماكانتش فقاعدة البيانات")

    items = get_territories()
    if not items:
        raise ZRError("ZR ما رجعش حتى منطقة")

    # البلديات الموجودة عندنا (باش نحدّثو بدل ما نكرّرو)
    existing = {
        c.external_id: c
        for c in Commune.query.filter_by(carrier_id=carrier.id).all()
    }

    added, updated, skipped_level, skipped_nosend, skipped_nocode = 0, 0, 0, 0, 0
    seen = set()

    for item in items:
        # 1) البلديات فقط
        if (item.get("level") or "").lower() != "commune":
            skipped_level += 1
            continue

        external_id = item.get("id")
        if not external_id:
            continue

        delivery = item.get("delivery") or {}
        can_send = bool(delivery.get("canSend"))

        # 2) نتجاوزو البلديات اللي ZR ما يبعثش ليها
        if not can_send:
            skipped_nosend += 1
            continue

        # 3) رمز الولاية من الرمز البريدي: "01035" → "01"
        postal = str(item.get("postalCode") or "").strip()
        wilaya_code = postal[:2] if len(postal) >= 2 and postal[:2].isdigit() else None

        if not wilaya_code:
            skipped_nocode += 1
            continue

        name_ar = (item.get("nameArabic") or "").strip()
        name_fr = (item.get("name") or "").strip()

        if not name_ar and not name_fr:
            continue

        seen.add(external_id)
        row = existing.get(external_id)

        if row is None:
            row = Commune(carrier_id=carrier.id, external_id=external_id)
            db.session.add(row)
            added += 1
        else:
            updated += 1

        row.name_ar = name_ar or name_fr
        row.name_fr = name_fr or None
        row.postal_code = postal or None
        row.wilaya_code = wilaya_code
        row.has_home_delivery = bool(delivery.get("hasHomeDelivery", True))
        row.has_pickup_point = bool(delivery.get("hasPickupPoint", False))
        row.can_send = True

    # البلديات اللي ما بقاتش عند ZR → نعطّلوها بدل ما نمسحوها
    # (باش الطلبيات القديمة تبقى مفهومة)
    removed = 0
    for ext_id, row in existing.items():
        if ext_id not in seen and row.can_send:
            row.can_send = False
            removed += 1

    db.session.commit()

    # كم ولاية عندها بلديات
    wilayas_covered = db.session.query(Commune.wilaya_code).filter_by(
        carrier_id=carrier.id, can_send=True
    ).distinct().count()

    return {
        "total_from_zr": len(items),
        "added": added,
        "updated": updated,
        "deactivated": removed,
        "wilayas_covered": wilayas_covered,
        "skipped_level": skipped_level,
        "skipped_nosend": skipped_nosend,
        "skipped_nocode": skipped_nocode,
    }


def find_territory_id(carrier_code, commune_external_id):
    """
    معرّف المنطقة اللي يتبعث لـZR = معرّف البلدية.

    ⚠️ الطلبية تخزّن المعرّف وقت الطلب، فحتى لو تبدّلت بيانات ZR بعدين،
    الإرسال يبقى يخدم بنفس المعرّف اللي اختارو الزبون.
    """
    return commune_external_id or None
