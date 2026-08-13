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


def get_territories():
    """
    قائمة الولايات/المناطق عند ZR مع معرّفاتها.

    نحتاجوها باش نربطو ولاياتنا بمعرّفات ZR — بلاها ما نقدروش نبعثو طرد.
    """
    data = _request("POST", "/territories/search", json={"page": 1, "pageSize": 200})
    return data.get("data") or data.get("items") or []


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


def sync_territories():
    """
    يجيب مناطق ZR ويربطها بولاياتنا فجدول shipping_rates.

    يرجع dict فيه: matched (تربطو)، unmatched (ما تربطوش)، total.

    الاستراتيجية — نجرّبو بالترتيب:
      1) مطابقة برمز الولاية (الأدق إذا ZR يعطيه)
      2) مطابقة بالاسم بعد التوحيد
      3) مطابقة جزئية (الاسم داخل الاسم)
    """
    from models import db, Carrier, ShippingRate

    carrier = Carrier.query.filter_by(code="zr").first()
    if not carrier:
        raise ZRError("شركة ZR Express ماكانتش فقاعدة البيانات")

    territories = get_territories()
    if not territories:
        raise ZRError("ZR ما رجعش حتى منطقة")

    # نبنيو فهرسين للبحث السريع
    by_code, by_name = {}, {}
    for item in territories:
        tid, name, code = _extract_territory_fields(item)
        if not tid:
            continue
        if code:
            by_code[str(int(code)) if str(code).isdigit() else str(code)] = tid
        if name:
            by_name[_normalize_arabic(name)] = tid

    rates = ShippingRate.query.filter_by(carrier_id=carrier.id).all()
    matched, unmatched = 0, []

    for rate in rates:
        tid = None

        # 1) بالرمز
        code_key = str(int(rate.wilaya_code)) if str(rate.wilaya_code).isdigit() else str(rate.wilaya_code)
        tid = by_code.get(code_key)

        # 2) بالاسم الموحّد
        if not tid:
            tid = by_name.get(_normalize_arabic(rate.wilaya_name))

        # 3) مطابقة جزئية
        if not tid:
            ours = _normalize_arabic(rate.wilaya_name)
            for zr_name, zr_id in by_name.items():
                if ours and (ours in zr_name or zr_name in ours):
                    tid = zr_id
                    break

        if tid:
            rate.carrier_territory_id = str(tid)
            matched += 1
        else:
            unmatched.append(f"{rate.wilaya_code} ~ {rate.wilaya_name}")

    db.session.commit()

    return {
        "total": len(rates),
        "matched": matched,
        "unmatched": unmatched,
        "zr_count": len(territories),
    }


def find_territory_id(carrier_code, wilaya_code):
    """
    يجيب معرّف المنطقة المحفوظ لولاية معينة.
    يرجع None إذا ما تربطش — والمستدعي يعرض خطأ واضح.
    """
    from models import Carrier, ShippingRate

    carrier = Carrier.query.filter_by(code=carrier_code).first()
    if not carrier:
        return None

    rate = ShippingRate.query.filter_by(
        carrier_id=carrier.id, wilaya_code=str(wilaya_code).zfill(2)
    ).first()

    if not rate:
        # نجرّبو بلا أصفار بادئة (احتياط لاختلاف التخزين)
        rate = ShippingRate.query.filter_by(
            carrier_id=carrier.id, wilaya_code=str(wilaya_code)
        ).first()

    return rate.carrier_territory_id if rate else None
