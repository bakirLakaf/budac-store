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

# ⚠️ اسم موحّد يتبعث لـZR بدل أسماء المنتجات الحقيقية.
# باش نبدّلو، غيّر هاد السطر فقط.
ZR_PRODUCT_LABEL = "أكسسوارات"
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
#
# 🔴 ملاحظتان أمنيتان على هاد المتغيّر العام:
#
# 1) **بيانات شخصية**: الـpayload اللي يتبعث لـZR فيه اسم الزبون
#    وهاتفو وعنوانو. الاحتفاظ بيهم فالذاكرة بلا حد يعني أنهم
#    يبقاو موجودين ساعات بعد ما تسالى العملية. نخبّيوهم درك.
#
# 2) **مشترك بين الخيوط**: gunicorn يخدم بـ4 خيوط، فأدمنان يشوفو
#    نفس السطر. عندنا أدمن واحد فالواقع، فالأثر تشخيصي برك
#    (تشوف تبادلاً ماشي تاعك)، ماشي تسريبًا لطرف خارجي.
#    زدنا الوقت باش يبان بوضوح واش هاد التبادل قديم.
LAST_EXCHANGE = {}

# الحقول اللي ما نخزّنوهاش نص صريح — بيانات الزبون
_PII_FIELDS = {
    "clientName", "clientPhone", "clientPhone2", "phone", "phone2",
    "name", "address", "addressLine", "customerName", "customerPhone",
    "recipientName", "recipientPhone", "note", "comment",
}


def _redact_payload(payload):
    """
    ينسخ الـpayload ويخبّي بيانات الزبون قبل ما يتخزّن للتشخيص.

    ⚠️ نخلّيو **طول** القيمة بدل ما نمسحوها كليًا: التشخيص عادةً
    يدور على «واش الحقل معمّر؟ واش طولو صحيح؟» ماشي على القيمة
    نفسها. هكذا نحلّو المشكل بلا ما نخسرو فائدة الصفحة.
    """
    if not isinstance(payload, dict):
        return payload

    clean = {}
    for key, value in payload.items():
        if key in _PII_FIELDS and value:
            clean[key] = f"‹مخفي · {len(str(value))} حرف›"
        elif isinstance(value, dict):
            clean[key] = _redact_payload(value)
        elif isinstance(value, list):
            clean[key] = [_redact_payload(v) for v in value]
        else:
            clean[key] = value
    return clean


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

    # ⚠️ الرد الفارغ ما يقولش والو. نسجّلو الهيدرز وحجم المحتوى —
    # فأوقات السبب يبان فيهم (نوع المحتوى، ضغط، رأس خطأ خاص).
    try:
        headers = {
            k: v for k, v in response.headers.items()
            if k.lower() in (
                "content-type", "content-length", "content-encoding",
                "x-error", "x-error-message", "www-authenticate", "date",
            )
        }
    except Exception:
        headers = {}

    from datetime import datetime as _dt

    LAST_EXCHANGE.update({
        "method": method,
        "path": path,
        "at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        "payload": _redact_payload(kwargs.get("json")),
        "status": response.status_code,
        "response": body_preview,
        "headers": headers,
        "content_length": len(response.content or b""),
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
        #    واش الحقل الغالط.
        detail = ""
        try:
            payload = response.json()
            field_errors = payload.get("errors")

            # ⚠️ ZR يرجّع errors كـ**قائمة** فيها {code, description}:
            #   [{"code":"NotEmptyValidator","description":"..."}]
            # وماشي كـdict كيما ASP.NET العادي. لازم ندعمو الشكلين.
            if isinstance(field_errors, list) and field_errors:
                parts = []
                for err in field_errors:
                    if isinstance(err, dict):
                        text = (err.get("description") or err.get("message")
                                or err.get("code") or "")
                    else:
                        text = str(err)
                    if text:
                        parts.append(text.strip())
                detail = " • ".join(parts)

            elif isinstance(field_errors, dict) and field_errors:
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

        # ⚠️ رد فارغ بلا تفاصيل: التحقق من الحقول عدا، والرفض من مكان آخر.
        # الأرجح الوجهة (territoryId) ناقصة ولا غير معروفة عند ZR.
        if not (detail or "").strip():
            detail = (
                "رد فارغ بلا تفاصيل. غالبًا الوجهة ماشي مضبوطة — "
                "تأكد أن الطلبية عندها معرّف بلدية (territoryId)، "
                "وزامن البلديات من صفحة التكاملات."
            )

        raise ZRError(f"ZR Express رجع خطأ ({response.status_code}): {detail}")

    if not response.content:
        return {}

    try:
        return response.json()
    except Exception:
        return {}


def _extract_items(data):
    """يستخرج قائمة العناصر من رد ZR مهما كان اسم الحقل."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("items", "data", "results", "records"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


# ⚠️ طرق الترقيم المحتملة. ZR **يتجاهل** `page` بصمت ويرجّع نفس الصفحة
# الأولى فكل مرة — كشفناها من التشخيص: 20 صفحة × 200 = 4000 «منطقة»،
# لكن المعرّفات الفريدة 200 فقط. لهذا نجرّبو عدة أسماء ونكشفو التكرار.
_PAGINATION_STYLES = [
    ("pageNumber", lambda n, size: {"pageNumber": n, "pageSize": size}),
    ("pageIndex", lambda n, size: {"pageIndex": n, "pageSize": size}),
    ("skip", lambda n, size: {"skip": (n - 1) * size, "take": size}),
    ("offset", lambda n, size: {"offset": (n - 1) * size, "limit": size}),
    ("page", lambda n, size: {"page": n, "pageSize": size}),
]

_territories_cache = None


def get_territories(max_pages=40, page_size=500, force=False):
    """
    كل مناطق ZR مع معرّفاتها.

    ⚠️ درسان تعلّمناهم من التشخيص الحقيقي:

    1) ZR يتجاهل `page` بصمت. كنا نجيبو نفس الـ200 عنصر 20 مرة ونظنو
       عندنا 4000 منطقة. النتيجة: 90% من البلديات ما كانتش عندنا
       (غرداية مثلاً)، والطلبيات ترفض بـ400 بلا رسالة.

    2) الحل: نجرّبو عدة أسماء للترقيم، ونكشفو التكرار بمقارنة
       المعرّفات. إذا صفحة ما جابت حتى معرّف جديد → الطريقة ما تخدمش.

    نرجّعو العناصر **الفريدة** فقط.
    """
    global _territories_cache

    if _territories_cache is not None and not force:
        return _territories_cache

    best = []

    for style_name, build in _PAGINATION_STYLES:
        seen_ids = set()
        items = []
        page = 1

        while page <= max_pages:
            try:
                data = _request("POST", "/territories/search",
                                json=build(page, page_size))
            except ZRError:
                break

            batch = _extract_items(data)
            if not batch:
                break

            # ⚠️ نعدّو الجداد فقط — إذا صفر، الترقيم ما يتطبّقش
            fresh = [
                it for it in batch
                if it.get("id") and it["id"] not in seen_ids
            ]
            for it in fresh:
                seen_ids.add(it["id"])
                items.append(it)

            if not fresh:
                break  # نفس الصفحة تتعاود — هاد الطريقة ما تخدمش

            if len(batch) < page_size:
                break  # صفحة ناقصة = الأخيرة

            page += 1

        if len(items) > len(best):
            best = items

        # ⚠️ إذا لقينا أكثر من صفحة كاملة، الطريقة تخدم — نوقفو هنا
        if len(items) > page_size:
            break

    _territories_cache = best
    return best


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
    فجدول الأسعار = ZR يوصل ليها. ماشي حقل canSend تاع البلديات.
    """
    data = _request("GET", "/delivery-pricing/rates")
    if isinstance(data, list):
        return data
    return data.get("data") or data.get("items") or []


def _rate_wilaya_code(item):
    """يستخرج رمز الولاية من سطر سعر."""
    # ⚠️ ندعمو camelCase و snake_case — ZR يبدّل التسمية بين المسارات،
    # وأي مفتاح ناقص معناه الولاية تتجاهل بصمت وسعرها ما يتحدّثش.
    for key in ("wilayaCode", "wilaya_code", "stateCode", "state_code",
                "code", "destinationCode", "destination_code", "number"):
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

def _check_parcel_data(order):
    """
    يفحص بيانات الطلبية قبل ما نبعثوها لـZR.

    ⚠️ ZR يرد بـ400 «One or more validation errors» بلا ما يقول واش ناقص
    فبعض الحالات. هاد الفحص يمسك المشاكل المعروفة ويعطي رسالة عربية
    واضحة تقول بالضبط واش لازم يتصلّح.
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


def build_parcel_payload(order, territory_id=None, hub_id=None,
                         city_territory_id=None, customer_id=None,
                         product_id=None, product_sku=None):
    """
    يبني الـpayload اللي يتبعث لـZR.

    ⚠️ البنية مبنية على **رسائل التحقق الحقيقية** اللي رجّعها ZR، ماشي
    على تخمين. الرد كان:

        "At least one product must be ordered."
        "OrderedProducts cannot be null or empty."
        "DeliveryType is required"
        "DeliveryType must be either 'home' or 'pickup-point'."
        "Description is required."

    يعني:
      • orderedProducts : قائمة منتجات (ماشي نص productName)
      • deliveryType    : "home" ولا "pickup-point" (ماشي isStopDesk منطقي)
      • description     : إجباري

    وفالجولة الثانية زاد:

        "ProductName is required. [Index: 0]"
        "StockType is required. [Index: 0]"
        "StockType must be either 'local', 'warehouse', or 'none'."
        "deliveryAddress cannot be null or empty."
        "Products must be either all Warehouse, or a mix of Local and None."

    يعني:
      • داخل كل منتج: productName (ماشي name) + stockType
      • العنوان اسمو deliveryAddress (ماشي address)
      • كل المنتجات لازم نفس العائلة: إما كلهم warehouse،
        ولا خليط من local وnone

    منفصلة عن create_parcel باش صفحة التشخيص تعرضها بلا ما تبعث.
    """
    is_office = order.delivery_type == "office"

    # ⚠️ القيم الحرفية اللي يقبلها ZR — أي قيمة أخرى ترفض
    delivery_type = "pickup-point" if is_office else "home"

    # 🔴 ZR يرفض الشكل المحلي: «must be a valid international phone
    # number». لازم +213...
    phone = to_international_phone(order.customer_phone)

    # ---------- الشارع ----------
    if is_office:
        street = order.pickup_point_name or order.wilaya_name or ""
    else:
        street = order.address_note or order.commune or order.wilaya_name or ""

    street = (street or "").strip() or (order.wilaya_name or "الجزائر")

    # ---------- المنتجات ----------
    # ⚠️ سطر واحد ثابت «أكسسوارات»: ما نكشفوش لشركة التوصيل واش نبيعو
    # ولا بأشمن سعر. المبلغ الحقيقي يمشي فـ`amount`.
    #
    # 🔴 `stockType` يقبل **"local" ولا "warehouse"** برك (من التوثيق).
    # كنا نبعثو "none" — قيمة ما كاينةش، وهي وحدها كافية للرفض.
    # 🔴 السعر اسمو `unitPrice` ماشي `price`.
    # 🔴 ZR يفرض: ProductId (GUID صالح) + ProductSku. يعني السطر لازم
    # يشير لمنتج **موجود فكتالوجهم** — ماشي أي نص.
    product = {
        "productName": ZR_PRODUCT_LABEL,
        "quantity": 1,
        "unitPrice": order.total,
        "stockType": "local",
    }
    if product_id:
        product["productId"] = product_id
    if product_sku:
        product["productSku"] = product_sku

    ordered_products = [product]

    # ══════════════════════════════════════════════════════════
    # 🔴 الشكل الصحيح — من التوثيق الرسمي (POST /parcels)
    #
    # خمس جولات تخمين راحو لأن الـpayload القديم كان **بشكل تاني
    # كليًا**: حقول مسطّحة (customerName, customerPhone, wilaya,
    # commune, reference, price...) بينما ZR يستنّى **كائنات
    # متداخلة**: customer{} · deliveryAddress{} · hubId · externalId.
    #
    # ASP.NET يتجاهل الحقول اللي ما يعرفهاش بصمت، ومن بعد يرفض لأن
    # الحقول الحقيقية ناقصة — ويرد **400 بحجم 0**، بلا ما يقول واش
    # ناقص. هذا يفسّر كل شي شفناه.
    # ══════════════════════════════════════════════════════════
    payload = {
        "customer": {
            "name": (order.customer_name or "").strip(),
            "phone": {"number1": phone},
        },
        "deliveryAddress": {
            # cityTerritoryId = الولاية · districtTerritoryId = البلدية
            "street": street,
        },
        "orderedProducts": ordered_products,
        "deliveryType": delivery_type,
        "description": ZR_PRODUCT_LABEL,
        "amount": order.total,
        # ⚠️ مرجعنا اسمو عندهم `externalId` ماشي `reference`
        "externalId": order.order_number,
    }

    # الولاية
    city_id = city_territory_id or order.wilaya_external_id
    if not city_id:
        try:
            city_id = lookup_wilaya_territory_id(order.wilaya_code)
        except Exception:
            city_id = None
    if city_id:
        payload["deliveryAddress"]["cityTerritoryId"] = city_id

    # البلدية
    if territory_id:
        payload["deliveryAddress"]["districtTerritoryId"] = territory_id

    # 🔴 ZR يفرض CustomerId — الطرد لازم يشير لزبون موجود عندهم
    cust = customer_id or order.customer_external_id
    if cust:
        payload["customer"]["customerId"] = cust

    if is_office:
        # 🔴 المكتب اسمو `hubId` فالجذر — ماشي `pickupPointId`
        hub = hub_id or order.hub_external_id
        if hub:
            payload["hubId"] = hub

    return payload


def create_parcel(order, territory_id=None, hub_id=None):
    """
    ينشئ طرد عند ZR Express من طلبية عندنا.

    يرجع dict فيه tracking_number وparcel_id.
    يرمي ZRError إذا فشل — والمستدعي يقرر واش يدير.
    """
    # نفحصو أولاً — أحسن رسالة عربية واضحة من 400 غامض
    problems = _check_parcel_data(order)
    if problems:
        raise ZRError("الطلبية ما تقبلهاش ZR: " + " | ".join(problems))

    # ⚠️ التجهيز قبل الإرسال: الزبون والمنتج لازم يكونو موجودين عند ZR
    customer_id, cust_why = ensure_customer(order)
    if not customer_id:
        raise ZRError(f"ما قدرناش نجهّزو الزبون عند ZR: {cust_why}")

    (product_id, product_sku), prod_why = ensure_zr_product()
    if not product_id:
        raise ZRError(f"ما قدرناش نجهّزو المنتج فكتالوج ZR: {prod_why}")

    # نحفظو معرّف الزبون فالطلبية باش ما نعاودوش البحث
    if not order.customer_external_id:
        try:
            from models import db as _db
            order.customer_external_id = customer_id
            _db.session.commit()
        except Exception:
            pass

    payload = build_parcel_payload(order, territory_id, hub_id,
                                   customer_id=customer_id,
                                   product_id=product_id,
                                   product_sku=product_sku)

    data = _request("POST", "/parcels", json=payload)

    # 🔴 الرد تاع الإنشاء فيه `{"id": "..."}` **برك** — ماكانش رقم
    # تتبّع (من التوثيق: 201 Created). لازم نداء ثاني باش نجيبوه.
    parcel_id = data.get("id") if isinstance(data, dict) else None

    tracking = (
        data.get("trackingNumber")
        or data.get("tracking_number")
        or (data.get("data") or {}).get("trackingNumber")
    )

    if not tracking and parcel_id:
        try:
            detail = _request("GET", f"/parcels/{parcel_id}")
            tracking = (detail or {}).get("trackingNumber")
        except Exception as e:
            # ⚠️ الطرد **اتنشأ فعلاً** — ما نرميوش خطأ ونخلّيو الأدمن
            # يظن أنه فشل ويعاود يبعث ويخلق طردًا ثانيًا.
            try:
                from flask import current_app
                current_app.logger.warning(
                    "📦 الطرد %s اتنشأ لكن ما جبناش رقم التتبّع: %s",
                    parcel_id, e,
                )
            except Exception:
                pass

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

    ⚠️ بلاغ حقيقي (v148): ZR بدّلت شكل الرد — `state` ولّى كائن كامل
    `{id, name, description, isBlocking, isLocked, visibleFor,
    editableBy, color}` بدل نص بسيط. كنا نمرّرو هاد الكائن كامل
    لعمود `carrier_state` (نص قصير) فكانت تطيح:
        psycopg2.ProgrammingError: can't adapt type 'dict'
    نستخرجو النص المقروء من جوّا الكائن، مع دعم الشكل القديم (نص
    مباشر) احتياطًا لو ZR رجّعت للشكل القديم فمكان آخر من الـAPI.
    """
    data = _request("GET", f"/parcels/{tracking_number}")

    state_obj = data.get("state")

    if isinstance(state_obj, dict):
        # الشكل الجديد: كائن فيه description (فرنسي مقروء) وname (تقني)
        state_label = (
            state_obj.get("description")
            or state_obj.get("name")
            or "غير معروف"
        )
        state_id = state_obj.get("id")
    else:
        # الشكل القديم: نص مباشر، أو stateName منفصل
        state_label = data.get("stateName") or state_obj or "غير معروف"
        state_id = data.get("stateId")

    # نتأكدو أنو نص دايمًا، بأي حال — العمود فقاعدة البيانات نص
    # قصير (120 حرف)، وما نبعثوش كائن مهما كان شكل رد ZR مستقبلاً.
    state_label = str(state_label)[:120]

    return {
        "state": state_label,
        "state_id": state_id,
        "updated_at": data.get("updatedAt") or data.get("lastUpdate"),
        "raw": data,
    }


def get_label_files(tracking_numbers, fmt="a6"):
    """
    الملصق (البوردرو) الرسمي تاع ZR Express بصيغة PDF.

    المسار: `POST /parcels/labels/individual/pdf` بجسم
    `{"trackingNumbers": [...], "format": "a6" | "a4"}` — a6 = ملصق فورقة،
    a4 = 4 ملصقات فورقة. يرجّع رابط PDF **مؤقت** (SAS) لكل رقم تتبّع.

    المصدر: مشروع CodFlow (Apache-2.0)، مجرَّب على API الإنتاج تاع ZR.
    ⚠️ ما جرّبناهش بمفاتيحنا: إذا ZR بدّلت المسار، `LAST_EXCHANGE` (صفحة
    التكاملات) يبيّن الرد الحقيقي.

    يرجّع (urls, failed): urls = {tracking_number: file_url}.
    """
    numbers = [str(n).strip() for n in tracking_numbers if n and str(n).strip()]
    if not numbers:
        return {}, []

    data = _request(
        "POST", "/parcels/labels/individual/pdf",
        json={"trackingNumbers": numbers, "format": fmt},
    )

    urls = {}
    for item in (data.get("parcelLabelFiles") or []):
        number = item.get("trackingNumber")
        file_url = item.get("fileUrl")
        if number and file_url:
            urls[str(number)] = file_url

    failed = [str(n) for n in (data.get("failedTrackingNumbers") or [])]
    # أي رقم ما رجعش لا فالملفات لا فالفاشلين نعتبروه فاشل
    failed += [n for n in numbers if n not in urls and n not in failed]
    return urls, failed


def _plain_state(text):
    """نص بحروف صغيرة وبلا تشكيل لاتيني (Livré → livre) باش المقارنة تكون سهلة."""
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().strip()


def map_state_to_order_status(state_label, delivery_type="home"):
    """
    يترجم حالة ZR (مثلاً «Encaissé») لحالة طلبية فالموقع.

    يرجع None إذا الحالة ماهيش واضحة — وقتها ما نبدّلوش شي (الأمان أولًا:
    نفضّلو ما نبدّلوش على ما نبدّلو غلط، خصوصًا أن «راجعة» ترجّع المخزون).

    ⚠️ الكلمات تحت مبنية على أسماء حالات ZR المعروفة (فرنسي). إذا ZR
    عندها حالة جديدة ما تتعرفش، زيدها هنا.
    """
    text = _plain_state(state_label)
    if not text:
        return None

    # 1) راجعة — «retour en cours» مازال ما رجعش، ما نبدّلوش
    if "retour" in text or "returned" in text:
        if any(k in text for k in ("en cours", "en attente", "in progress")):
            return None
        return "returned"

    # 1b) «Récupéré par fournisseur» = ZR رجّعت الطرد لصاحب المتجر (تأكيد منو)
    #     ⚠️ العبارة كاملة برك: «Récupéré» وحدها (ramassé) = فالطريق، ماشي راجعة
    if "recupere" in text and "par" in text and "fournisseur" in text:
        return "returned"

    # 2) حالات الفشل / الانتظار — ما نبدّلوش شي
    if any(k in text for k in ("non livre", "echec", "failed", "annule",
                               "en attente", "pending", "cree", "brouillon",
                               "draft", "pret", "ready", "injoignable")):
        return None

    # 3) تسلّمها الزبون (وكذلك «Encaissé» و«Recouvert» = ZR قبضات الكاش)
    if any(k in text for k in ("livre", "delivered", "encaisse", "recouvert")):
        return "delivered"

    # 4) فمكتب الاستلام (للطلبيات اللي نوعها «مكتب» برك)
    if delivery_type == "office" and any(
        k in text for k in ("bureau", "stop desk", "stopdesk", "point relais",
                            "pickup", "agence")
    ):
        return "at_office"

    # 5) فالطريق
    if any(k in text for k in ("expedie", "transit", "ramasse", "collecte",
                               "prise en charge", "pris en charge",
                               "en livraison", "en cours", "sorti", "hub",
                               "tri", "shipped", "dispatch", "en route",
                               "bureau", "stop desk", "stopdesk")):
        return "shipped"

    return None


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


# نخزّنو فهرس المناطق فالذاكرة — 4000 منطقة، ما نجيبوهمش فكل طلبية
_territory_index = None


def _iter_commune_territories():
    """
    يمرّ على مناطق ZR ويرجّع البلديات الصالحة برك: (معرّف، رمز الولاية، العنصر).

    ⚠️ هاد الترشيح كان مكرّر حرفيًا فبلاصتين (بناء الفهرس، ومزامنة
    المعرّفات الخارجية). التكرار خطر هنا بالذات: لو صلّحت شرط
    التحقق فوحدة ونسيت الثانية، البلديات يتفلترو بطريقتين مختلفتين
    والمعرّفات تولّي غالطة — بلا ما يبان خطأ.
    """
    for item in get_territories():
        if (item.get("level") or "").lower() != "commune":
            continue

        tid = item.get("id")
        postal = str(item.get("postalCode") or "").strip()

        # الرمز البريدي لازم يبدا برقمين = رمز الولاية
        if not tid or len(postal) < 2 or not postal[:2].isdigit():
            continue

        yield str(tid), postal[:2], item


def _build_territory_index(force=False):
    """
    يبني فهرس {(رمز الولاية, اسم مطبّع): معرّف ZR} من مناطق ZR.

    ⚠️ الفهرس فالذاكرة فقط: يتبنى مرة وحدة ويتمسح مع إعادة التشغيل.
    4000 منطقة على عدة صفحات = 30 ثانية، فما نعاودوهاش فكل إرسال.
    """
    global _territory_index

    if _territory_index is not None and not force:
        return _territory_index

    index = {}
    for tid, wilaya, item in _iter_commune_territories():
        for name in (item.get("nameArabic"), item.get("name")):
            if name:
                # أول معرّف يكفي — ما نكتبوش فوقو بمنطقة فرعية أخرى
                index.setdefault((wilaya, _normalize_arabic(name)), tid)

    _territory_index = index
    return index


def lookup_wilaya_territory_id(wilaya_code):
    """
    معرّف **الولاية** عند ZR (`cityTerritoryId`).

    ⚠️ ZR عندو نوعين مناطق: `wilaya` (54) و`commune` (1531). الطرد
    يحتاج الجوج: الولاية فـ`cityTerritoryId`، والبلدية فـ
    `districtTerritoryId`.

    مناطق الولاية عندها `code` = رمز الولاية الرسمي (25 = قسنطينة)،
    فالمطابقة مباشرة. البلديات عندها ترقيم داخلي مختلف.
    """
    if not wilaya_code:
        return None

    try:
        wanted = int(str(wilaya_code).lstrip("0") or "0")
    except ValueError:
        return None

    for item in get_territories():
        if (item.get("level") or "").lower() != "wilaya":
            continue
        if item.get("code") == wanted:
            return item.get("id")

    return None


def lookup_territory_id(wilaya_code, commune_name):
    """
    يدوّر على معرّف المنطقة عند ZR من رمز الولاية واسم البلدية.

    ⚠️ علاش هاد الدالة؟
    ZR يحدّد الوجهة بـterritoryId، ماشي بأسماء الولاية والبلدية. الطلبيات
    القديمة (ولا اللي الزبون كتب فيها البلدية بيدو) ما عندهاش المعرّف
    محفوظ. بدل ما نرفضو الإرسال، نبحثو عليه مباشرة.

    يرجع المعرّف ولا None — والمستدعي يقرر.
    """
    if not wilaya_code or not commune_name:
        return None

    # 1) البحث المباشر أولاً — أسرع وما يعتمدش على الترقيم المعطوب
    try:
        found, _ = search_territory(commune_name, wilaya_code)
        if found:
            return found
    except Exception:
        pass

    # 2) احتياط: الفهرس الكامل (يخدم فقط إذا الترقيم يخدم عند ZR)
    try:
        index = _build_territory_index()
    except ZRError:
        return None

    key = (str(wilaya_code).zfill(2), _normalize_arabic(commune_name))
    return index.get(key)


def office_commune(order):
    """
    بلدية مكتب الاستلام تاع طلبية «توصيل للمكتب».

    🔴 علاش هاد الدالة؟
    طلبية المكتب ما عندهاش بلدية أصلاً (`commune` تتفرّغ عمدًا — شوف
    التحقق فـroutes/cart.py). النتيجة كانت: `lookup_territory_id`
    تترفض على طول، الطرد يتبعث بلا `territoryId`، وZR يرد **400 بلا
    أي رسالة**. كل طلبيات المكتب كانت تفشل.

    الحل: نجيبو البلدية من **المكتب نفسو**. مكتب سطيف موجود فبلدية
    Setif، وZR عندو منطقة اسمها setif — فالربط ممكن.

    نطابقو على `pickup_point_id` أولاً (هو اسم المكتب كيما نبعثوه)،
    ومن بعد على `pickup_point_name` اللي ممكن يكون فيه العنوان معاه.
    """
    if order.delivery_type != "office":
        return None

    wanted_id = (order.pickup_point_id or "").strip()
    wanted_name = (order.pickup_point_name or "").strip()

    for o in get_offices():
        name = (o.get("name") or "").strip()
        if not name:
            continue
        # ⚠️ `pickup_point_name` عند الزبون يكون «الاسم — العنوان»،
        # فنقبلو البداية كذلك ماشي التطابق التام برك.
        if name == wanted_id or name == wanted_name or wanted_name.startswith(name):
            return o.get("commune") or None

    return None


def resolve_territory_id(order):
    """
    معرّف الوجهة عند ZR لأي طلبية — منزل ولا مكتب.

    ⚠️ مصدر واحد للحقيقة: قبل، كل مستدعي كان يبني منطقو بيدو،
    فمسار الإرسال ومسار التشخيص كانو يعطيو نتائج مختلفة.

    يرجّع (المعرّف، السبب إذا فشل).
    """
    # 1) محفوظ من قبل
    if order.commune_external_id:
        return order.commune_external_id, None

    # 2) الاسم اللي نبحثو بيه يختلف حسب نوع التوصيل
    if order.delivery_type == "office":
        name = office_commune(order)
        if not name:
            return None, (
                f"ما لقيناش بلدية المكتب «{order.pickup_point_name or '—'}» "
                "فملف المكاتب. تأكد أن اسم المكتب مطابق."
            )
    else:
        name = order.commune
        if not name:
            return None, "الطلبية بلا بلدية"

    try:
        found = lookup_territory_id(order.wilaya_code, name)
    except Exception as e:
        return None, f"فشل البحث: {e}"

    if not found:
        return None, f"ما لقيناش «{name}» فمناطق ZR تاع الولاية {order.wilaya_code}"

    return found, None


# ═══════════════════════════════════════════════════════════
# المكاتب — من ملف محلي، بلا API
# ═══════════════════════════════════════════════════════════

_OFFICES_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "zr_offices.json")
_offices_cache = None


def get_offices():
    """
    كل مكاتب ZR (79 مكتب) — من ملف محلي `data/zr_offices.json`.

    ⚠️ علاش ملف محلي وماشي API؟
    ZR ما عندوش مسار API للمكاتب (جرّبنا 5 مسارات، كلهم رجعو 404).
    القائمة ثابتة وتتبدّل نادرًا، فنخزّنوها عندنا. باش تحدّثها:
    نسخ القائمة من صفحة «Nos Bureaux» عند ZR وحدّث الملف.

    ملاحظة مهمة: رمز الولاية ماخوذ من **رقم اسم المكتب**، ماشي من الرمز
    البريدي — بعض المكاتب الجديدة رمزها البريدي تاع الولاية القديمة
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

        for tid, w, item in _iter_commune_territories():
            commune = (
                index.get((w, _normalize_arabic(item.get("nameArabic") or "")))
                or index.get((w, _normalize_arabic(item.get("name") or "")))
            )
            if commune is not None:
                # أول معرّف يكفي — ما نكتبوش فوقو بمنطقة فرعية أخرى
                external_ids.setdefault(commune.id, tid)
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


def diagnose_territory_lookup(wilaya_code, commune_name):
    """
    يشرح **علاش** البحث عن معرّف المنطقة نجح ولا فشل.

    ⚠️ ضروري لأن lookup_territory_id يرجّع None بلا سبب. بلا هاد التشخيص
    ما نعرفوش واش المشكل: ZR ما ردّش، ولا الولاية ماكانتش، ولا الاسم
    مكتوب بطريقة مختلفة.

    يرجّع dict فيه كل التفاصيل — صفحة التشخيص تعرضهم.
    """
    result = {
        "wilaya_code": wilaya_code,
        "commune_name": commune_name,
        "normalized": _normalize_arabic(commune_name or ""),
        "error": None,
        "total_territories": 0,
        "commune_level": 0,
        "index_size": 0,
        "unique_ids": 0,
        "wilaya_found": False,
        "wilaya_communes": [],
        "match": None,
        "search_match": None,
        "search_attempts": [],
    }

    # ⚠️ نجرّبو البحث المباشر أولاً ونسجّلو النتيجة — هو الطريقة
    # الوحيدة اللي تخدم إذا الترقيم معطوب عند ZR
    try:
        found, attempts = search_territory(commune_name, wilaya_code)
        result["search_match"] = found
        result["search_attempts"] = attempts
        if found:
            result["match"] = found
    except Exception as e:
        result["search_attempts"] = [{"error": str(e)[:150]}]

    try:
        items = get_territories(force=True)
    except Exception as e:
        result["error"] = f"ما قدرناش نجيبو المناطق من ZR: {e}"
        return result

    # ⚠️ ZR ممكن يرد بحاجة ماشي قائمة — ما نثقوش ونكرشو فالتشخيص نفسو
    if not isinstance(items, list):
        result["error"] = (
            f"ZR رجّع نوع غير متوقع ({type(items).__name__}) بدل قائمة مناطق"
        )
        return result

    if not items:
        result["error"] = "ZR رجّع قائمة مناطق فارغة"
        return result

    result["total_territories"] = len(items)
    result["unique_ids"] = len({it.get("id") for it in items if it.get("id")})
    result["commune_level"] = sum(
        1 for it in items if (it.get("level") or "").lower() == "commune"
    )

    if not result["commune_level"]:
        levels = sorted({(it.get("level") or "?") for it in items[:200]})
        result["error"] = (
            "ماكانش ولا منطقة من نوع «commune». "
            f"الأنواع الموجودة: {', '.join(levels)}"
        )
        return result

    try:
        index = _build_territory_index(force=True)
    except Exception as e:
        result["error"] = f"فشل بناء الفهرس: {e}"
        return result

    result["index_size"] = len(index)

    target = str(wilaya_code or "").zfill(2)

    # كل بلديات هاد الولاية فالفهرس — باش نشوفو الأسماء كيما هي عند ZR
    names = sorted({k[1] for k in index.keys() if k[0] == target})
    result["wilaya_found"] = bool(names)
    result["wilaya_communes"] = names[:60]

    result["match"] = result.get("search_match") or index.get(
        (target, result["normalized"])
    )

    if not result["match"] and names:
        # اقتراحات: أسماء تبدا بنفس الحروف
        prefix = result["normalized"][:3]
        result["suggestions"] = [n for n in names if n.startswith(prefix)][:10]

    return result


# ⚠️ صيغ البحث المحتملة فـ/territories/search.
# نستعملو البحث بدل جلب 4000 منطقة على صفحات — الترقيم عند ZR
# ما يخدمش (يتجاهل `page` ويرجّع نفس الصفحة الأولى دايمًا).
# ⚠️ الترتيب مهم: `keyword` هو اللي يخدم فعلاً عند ZR (مؤكّد بالتشخيص).
# الباقي يتجاهلو ZR ويرجّع 200 نتيجة عامة — نخليوهم كاحتياط فقط.
_SEARCH_KEYS = ["keyword", "search", "searchTerm", "name", "query", "term"]


def search_territory(commune_name, wilaya_code=None):
    """
    يدوّر على بلدية عند ZR **بالبحث المباشر** — بلا ترقيم.

    ⚠️ علاش هاد الطريقة؟
    ZR يتجاهل معامل الترقيم: كل صفحة ترجّع نفس الـ200 عنصر. يعني ما
    نقدروش نجيبو الـ3880 بلدية كاملين مهما درنا. لكن المسار اسمو
    «search» — فنبعثو اسم البلدية ونخليو ZR هو يدوّر.

    نجرّبو عدة أسماء للحقل لأن التوثيق ما يوضحوش.

    يرجّع (المعرّف، تفاصيل المحاولات) — التفاصيل للتشخيص.
    """
    if not commune_name:
        return None, [{"note": "ماكانش اسم بلدية"}]

    target_wilaya = str(wilaya_code).zfill(2) if wilaya_code else None
    wanted = _normalize_arabic(commune_name)
    attempts = []

    for key in _SEARCH_KEYS:
        body = {key: commune_name, "pageSize": 200}

        try:
            data = _request("POST", "/territories/search", json=body)
        except ZRError as e:
            attempts.append({"key": key, "error": str(e)[:120]})
            continue

        items = _extract_items(data)
        attempts.append({"key": key, "count": len(items)})

        if not items:
            continue

        # ⚠️ لو ZR تجاهل البحث، يرجّع نفس الـ200 العامة. نتأكدو أن
        # النتائج فيها فعلاً الاسم المطلوب قبل ما نثقو فيها.
        for item in items:
            if (item.get("level") or "").lower() != "commune":
                continue

            postal = str(item.get("postalCode") or "").strip()
            item_wilaya = postal[:2] if len(postal) >= 2 and postal[:2].isdigit() else None

            if target_wilaya and item_wilaya != target_wilaya:
                continue

            for name in (item.get("nameArabic"), item.get("name")):
                if name and _normalize_arabic(name) == wanted:
                    attempts[-1]["matched"] = True
                    return str(item.get("id")), attempts

    return None, attempts


def sync_shipping_rates():
    """
    يحدّث أسعار التوصيل تاع ZR من جدول أسعارهم الرسمي.

    ⚠️ علاش هاد الدالة؟
    الأسعار كانت تتعمّر يدويًا وتقادمت. ZR يبدّلها من وقت لوقت،
    والسعر الغالط معناه خسارة فكل طلبية ولا زبون يهرب.

    القواعد:
      • سعر 0 ولا فارغ = **الخدمة غير متوفرة** (ماشي مجاني).
        نحطو None باش الولاية ما تبانش للزبون فهاد النوع.
      • ما نلمسوش الشركات الأخرى — ZR فقط.

    يرجّع تقرير مفصّل: واش تبدّل وواش بقى.
    """
    from models import db, Carrier, ShippingRate

    carrier = Carrier.query.filter_by(code="zrexpress").first()
    if not carrier:
        raise ZRError("شركة ZR Express ماكانتش فقاعدة البيانات")

    rates_from_zr = get_delivery_rates()
    if not rates_from_zr:
        raise ZRError(
            "ZR ما ردّش بأسعار. تأكد أن المفاتيح صحيحة، "
            "ولا عمّر الأسعار يدويًا من صفحة «أسعار التوصيل»."
        )

    # نبنيو خريطة: رمز الولاية → (سعر المنزل، سعر المكتب)
    incoming = {}
    for item in rates_from_zr:
        code = _rate_wilaya_code(item)
        if not code:
            continue

        home = _rate_price(item, "homePrice", "home", "domicile", "deliveryPrice")
        office = _rate_price(item, "officePrice", "office", "stopDesk",
                             "stopdeskPrice", "desk")

        # إذا الولاية تكررت، ناخذو أعلى سعر متوفر (أأمن من الأقل)
        old = incoming.get(code)
        if old:
            home = max(filter(None, [home, old[0]]), default=None)
            office = max(filter(None, [office, old[1]]), default=None)

        incoming[code] = (home, office)

    our_rates = ShippingRate.query.filter_by(carrier_id=carrier.id).all()

    changes = []
    unchanged = 0
    missing = []

    for rate in our_rates:
        new = incoming.get(rate.wilaya_code)

        if new is None:
            missing.append(f"{rate.wilaya_code} ~ {rate.wilaya_name}")
            continue

        new_home, new_office = new

        if rate.home_price == new_home and rate.office_price == new_office:
            unchanged += 1
            continue

        changes.append({
            "wilaya": f"{rate.wilaya_code} ~ {rate.wilaya_name}",
            "old_home": rate.home_price,
            "new_home": new_home,
            "old_office": rate.office_price,
            "new_office": new_office,
        })

        rate.home_price = new_home
        rate.office_price = new_office

    db.session.commit()

    return {
        "from_zr": len(incoming),
        "changed": len(changes),
        "unchanged": unchanged,
        "missing": missing,
        "details": changes[:60],
    }


# ═══════════════════════════════════════════════════════════
# مختبر الإرسال — نعزلو سبب الـ400 بدل ما نخمّنو
# ═══════════════════════════════════════════════════════════

def search_hubs(page_size=500):
    """
    قائمة مراكز/مكاتب ZR — من `POST /hubs/search`.

    🔴 هاد المسار هو اللي كان ناقصنا.
    جرّبنا 11 اسمًا (`/pickup-points`، `/offices`، `/desks`...) وكلهم
    404. التوثيق الرسمي ورّى أن ZR يسمّيهم **hubs**، وأسماء مكاتبهم
    تبدا بـ«Hub» فعلاً («Hub Setif 19 مكتب سطيف») — كان لازم ننتبهو.

    الأرجح أن `pickupPointId` يستنّى **UUID تاع الـhub** ماشي اسمو،
    وهذا يفسّر الـ400 الفارغ.
    """
    # 🔴 `_request(method, path, **kwargs)` — الجسم لازم `json=`.
    # بلاه: TypeError → صفحة 500 كاملة بدل رسالة خطأ. (غلطة صدرت مني
    # وطاحت فالإنتاج — علاش زدنا فحصًا يمنعها تحت.)
    data = _request("POST", "/hubs/search",
                    json={"pageNumber": 1, "pageSize": page_size})

    items = data.get("items") if isinstance(data, dict) else data
    return items or []


def find_hub_id(office_name, wilaya_code=None):
    """
    يدوّر على UUID تاع مكتب من اسمو.

    ⚠️ المطابقة متسامحة: أسماء ZR فيها خليط عربي/لاتيني وأرقام
    («Hub Setif 19 مكتب سطيف»). نطابقو بالاسم الكامل أولاً، ومن بعد
    بالجزء اللاتيني، ومن بعد بالولاية إذا بقى مكتب واحد فيها.
    """
    hubs = search_hubs()
    wanted = (office_name or "").strip().lower()

    def hub_name(h):
        return (h.get("name") or h.get("label") or "").strip()

    # 1) تطابق كامل
    for h in hubs:
        if hub_name(h).lower() == wanted:
            return h.get("id"), hub_name(h)

    # 2) واحد يحتوي الآخر
    for h in hubs:
        n = hub_name(h).lower()
        if n and (n in wanted or wanted in n):
            return h.get("id"), hub_name(h)

    # 3) الولاية فيها مكتب واحد برك
    if wilaya_code:
        code = str(wilaya_code).zfill(2)
        same = [h for h in hubs
                if str(h.get("wilayaCode") or h.get("territoryCode") or "").zfill(2) == code]
        if len(same) == 1:
            return same[0].get("id"), hub_name(same[0])

    return None, None


def resolve_city_id(order):
    """معرّف الولاية عند ZR — محفوظ ولا نجيبوه ونحفظوه."""
    if order.wilaya_external_id:
        return order.wilaya_external_id, None

    try:
        found = lookup_wilaya_territory_id(order.wilaya_code)
    except Exception as e:
        return None, f"فشل البحث عن الولاية: {e}"

    if not found:
        return None, f"ما لقيناش الولاية {order.wilaya_code} فمناطق ZR"

    return found, None


def resolve_hub_id(order):
    """
    UUID تاع مكتب الطلبية — محفوظ ولا نجيبوه من ZR ونحفظوه.

    يرجّع (المعرّف، السبب إذا فشل).
    """
    if order.delivery_type != "office":
        return None, None

    if order.hub_external_id:
        return order.hub_external_id, None

    name = order.pickup_point_id or order.pickup_point_name or ""
    if not name:
        return None, "الطلبية بلا مكتب"

    try:
        hub_id, matched = find_hub_id(name, order.wilaya_code)
    except Exception as e:
        return None, f"فشل جلب المكاتب من ZR: {e}"

    if not hub_id:
        return None, f"ما لقيناش مكتب «{name}» فقائمة hubs تاع ZR"

    return hub_id, None


def fetch_openapi_spec():
    """
    يجيب مخطط الـAPI الرسمي — باش نعرفو **بالضبط** واش يستنّى
    `POST /parcels`، بدل ما نخمّنو الحقول.

    ⚠️ التوثيق على docs.zrexpress.app يمنع الجلب الآلي، لكن السيرفر
    نفسو عادةً ينشر المخطط. نجرّبو المسارات المعروفة.
    """
    import requests as _rq

    roots = ["https://api.zrexpress.app"]
    paths = [
        "/swagger/v1/swagger.json",
        "/swagger/v1.0/swagger.json",
        "/openapi.json",
        "/swagger.json",
        "/api/v1.0/openapi.json",
    ]

    results = []
    for root in roots:
        for path in paths:
            url = f"{root}{path}"
            try:
                r = _rq.get(url, timeout=20)
                results.append({
                    "url": url, "status": r.status_code,
                    "size": len(r.content or b""),
                    "is_spec": "openapi" in (r.text or "")[:200].lower()
                               or "swagger" in (r.text or "")[:200].lower(),
                })
                if r.status_code == 200:
                    try:
                        spec = r.json()
                    except ValueError:
                        continue

                    schemas = (spec.get("components") or {}).get("schemas") or {}
                    create = (schemas.get("CreateParcelRequest")
                              or schemas.get("SingleParcelCreationRequest"))
                    return {"found": url, "schema": create,
                            "probe": results}
            except Exception as e:
                results.append({"url": url, "status": "—", "size": 0,
                                "is_spec": False, "error": str(e)})

    return {"found": None, "schema": None, "probe": results}


def probe_pickup_endpoints():
    """
    يجرّب مسارات GET محتملة لقائمة مكاتب ZR ويرجّع كود كل واحد.

    ⚠️ علاش؟ `pickupPointId` اللي نبعثوه درك هو **اسم المكتب** («Hub
    Setif 19 مكتب سطيف»)، وZR يستعمل UUID فكل شي آخر (territoryId
    مثلاً UUID). الأرجح أنه يستنّى UUID هنا كذلك — والاسم يترفض بـ400
    بلا رسالة.

    القراءة فقط: كلها GET، ما تنشئ والو.
    """
    paths = [
        "/pickup-points", "/pickuppoints", "/pickup_points",
        "/agencies", "/offices", "/desks", "/stop-desks", "/stopdesks",
        "/territories/pickup-points",
        "/delivery-pricing/pickup-points",
        "/parcels/pickup-points",
    ]

    headers = _headers()

    results = []
    for path in paths:
        try:
            r = requests.get(f"{BASE_URL}{path}", headers=headers, timeout=15)
            body = (r.text or "")[:300]
            results.append({
                "path": path,
                "status": r.status_code,
                "size": len(r.content or b""),
                "preview": body,
            })
        except Exception as e:
            results.append({"path": path, "status": "—", "size": 0,
                            "preview": f"فشل الاتصال: {e}"})

    return results


def inspect_territory_levels():
    """
    يجمّع مناطق ZR حسب `level` — نشوفو واش كاين نوع «pickup-point».

    ⚠️ التشخيص السابق ورّى: 1585 منطقة، 1531 منها `commune`.
    الـ54 الباقية نوعهم مجهول — إذا كانو مكاتب، معرّفهم هو اللي
    لازم يمشي فـ`pickupPointId`.
    """
    items = get_territories()

    by_level = {}
    for it in items:
        lvl = (it.get("level") or "?").lower()
        by_level.setdefault(lvl, []).append(it)

    return {
        "total": len(items),
        "levels": {k: len(v) for k, v in sorted(by_level.items())},
        # عيّنة من كل نوع ماشي commune — هي اللي تهمّنا
        "samples": {
            k: v[:4] for k, v in by_level.items() if k != "commune"
        },
    }


def _try_parcel_variants(variants, headers):
    """
    يجرّب أشكال الـpayload وحدة بوحدة ويوقف عند أول نجاح.

    ⚠️ **الوقوف عند أول نجاح إجباري**: كل محاولة ناجحة تنشئ طرد
    حقيقي عند ZR. لو كمّلنا الحلقة، الزبون يلقى 4 طرود لطلبية وحدة.

    يرجّع قائمة المحاولات، كل وحدة بحالتها وردّها.
    """
    attempts = []

    for label, payload in variants:
        try:
            response = requests.post(f"{BASE_URL}/parcels", headers=headers,
                                     json=payload, timeout=TIMEOUT)
            status, body = response.status_code, (response.text or "")[:600]
        except Exception as e:
            attempts.append({"label": label, "status": "—", "body": str(e),
                             "payload": payload, "ok": False})
            continue

        ok = 200 <= status < 300
        attempts.append({"label": label, "status": status, "body": body,
                         "payload": payload, "ok": ok})
        if ok:
            break

    return attempts


def lab_dimensions(order, territory_id=None, test_amount=100):
    """
    مختبر الأبعاد — يجرّب **طرودًا تجريبية** يعزل كل بُعد لوحدو.

    🔴 علاش هاد الجولة الثانية؟
    الجولة الأولى جرّبت 4 أشكال تاع المكتب، وكلهم رجعو 400 فارغ —
    **حتى اللي بلا `pickupPointId`**. يعني المشكل ماشي فحقل المكتب.
    والاستكشاف ورّى أن مناطق ZR فيها `commune` و`wilaya` برك، ماكانش
    نوع «مكتب» أصلاً.

    فالسؤال الحقيقي ولّى: **واش الإرسال خدم عمرو؟** ما جرّبنا عمرنا
    توصيل للدار. إذا هو تاني يطيح، المشكل فالحساب ولا فحقل ناقص
    نبعثوهش، ماشي فمنطق المكتب.

    ⚠️ **طرود تجريبية**: مبلغ صغير (100 دج افتراضيًا) ومرجع فريد
    فيه TEST. إذا نجح واحد، امسحو من لوحة ZR — ماشي طلبية حقيقية.

    يوقف عند أول نجاح.
    """
    import time as _time

    headers = _headers()
    base = build_parcel_payload(order, territory_id)
    stamp = int(_time.time())

    import copy as _copy

    def make(label, **over):
        p = _copy.deepcopy(base)
        # ⚠️ مرجع فريد فكل محاولة: إذا ZR يرفض المرجع المكرر،
        # هاد الشي وحدو يقدر يكون السبب.
        p["externalId"] = f"TEST-{stamp}-{len(variants) + 1}"
        p["amount"] = test_amount
        p["orderedProducts"] = [{
            "productName": ZR_PRODUCT_LABEL, "quantity": 1,
            "unitPrice": test_amount, "stockType": "local",
        }]
        p.update(over)
        return (label, p)

    variants = []

    # 1) 🔴 البُعد الأهم: توصيل للدار — ما جرّبناهش عمرنا
    v = make("توصيل للدار (نفس الوجهة)", deliveryType="home")
    v[1].pop("hubId", None)
    variants.append(v)

    # 2) الحد الأدنى: غير الحقول اللي التوثيق يقول أنها إجبارية
    minimal = {
        "customer": _copy.deepcopy(base["customer"]),
        "deliveryAddress": _copy.deepcopy(base["deliveryAddress"]),
        "orderedProducts": [{
            "productName": ZR_PRODUCT_LABEL, "quantity": 1,
            "unitPrice": test_amount, "stockType": "local",
        }],
        "deliveryType": "home",
        "description": ZR_PRODUCT_LABEL,
        "amount": test_amount,
    }
    variants.append(("الحد الأدنى تاع الحقول", minimal))

    # 3) بلا districtTerritoryId (البلدية)
    v = make("للدار بلا البلدية", deliveryType="home")
    v[1]["deliveryAddress"].pop("districtTerritoryId", None)
    v[1].pop("hubId", None)
    variants.append(v)

    # 4) بلا cityTerritoryId (الولاية)
    v = make("للدار بلا الولاية", deliveryType="home")
    v[1]["deliveryAddress"].pop("cityTerritoryId", None)
    v[1].pop("hubId", None)
    variants.append(v)

    # 5) stockType = warehouse
    v = make("للدار بـstockType=warehouse", deliveryType="home")
    v[1]["orderedProducts"][0]["stockType"] = "warehouse"
    v[1].pop("hubId", None)
    variants.append(v)

    return _try_parcel_variants(variants, headers)


def send_variants(order, territory_id=None):
    """
    يجرّب عدة أشكال للـpayload ويوقف عند أول نجاح.

    🔴 علاش هاد الدالة؟
    ZR يرد **400 بحجم محتوى 0** — بلا أي رسالة. ثلاث جولات تخمين
    ما حلّوش المشكل. بدل ما نبدّلو حقلاً ونعاودو نستنّاو، نجرّبو
    الأشكال المحتملة فمرة وحدة ونشوفو أشمن واحد يعدّي.

    ⚠️ **يوقف عند أول نجاح** — باش ما ننشئوش طرودًا مكررة.
    الأشكال مرتّبة من الأقرب للأبعد.

    يرجّع قائمة المحاولات، كل وحدة بحالتها وردّها.
    """
    # ⚠️ نفحصو المفاتيح **مرة وحدة قبل** الحلقة. بلا هذا، كل شكل
    # يفشل فبناء الرؤوس، والمختبر يبان كأن ZR رفض 4 أشكال — وهو ما
    # وصلوش حتى طلب. الخطأ يخرج واضحًا بدل ما يتخبّى فالنتائج.
    headers = _headers()

    base = build_parcel_payload(order, territory_id)
    is_office = order.delivery_type == "office"

    variants = []

    if is_office:
        # 1) بلا hubId — يمكن الوجهة تكفي
        v1 = dict(base)
        v1.pop("hubId", None)
        variants.append(("بلا hubId (الوجهة برك)", v1))

        # 2) بلا districtTerritoryId — يمكن الولاية تكفي للمكتب
        import copy
        v2 = copy.deepcopy(base)
        v2["deliveryAddress"].pop("districtTerritoryId", None)
        variants.append(("بلا districtTerritoryId", v2))

        # 3) stockType = warehouse بدل local
        v3 = copy.deepcopy(base)
        for prod in v3["orderedProducts"]:
            prod["stockType"] = "warehouse"
        variants.append(("stockType = warehouse", v3))

        # 4) بلا externalId — يمكن المرجع المكرر يترفض
        v4 = copy.deepcopy(base)
        v4.pop("externalId", None)
        variants.append(("بلا externalId", v4))
    else:
        import copy
        v1 = copy.deepcopy(base)
        v1["deliveryAddress"].pop("districtTerritoryId", None)
        variants.append(("بلا districtTerritoryId", v1))

        v2 = copy.deepcopy(base)
        for prod in v2["orderedProducts"]:
            prod["stockType"] = "warehouse"
        variants.append(("stockType = warehouse", v2))

    return _try_parcel_variants(variants, headers)


# ═══════════════════════════════════════════════════════════
# تجهيز ZR: الزبون والمنتج فكتالوجهم
# ═══════════════════════════════════════════════════════════
#
# 🔴 ZR بدا يرد برسائل تحقق واضحة (بعد ما صلّحنا شكل الـpayload)،
# وقال بالضبط واش ناقص:
#
#   • CustomerId is required
#   • ProductId is required + must be a valid GUID
#   • ProductSku is required
#   • Phone_Number1 must be a valid international phone number
#
# يعني ZR ماشي «صندوق بريد» ياخذ أي بيانات: الطرد لازم يشير لـ**زبون
# موجود** فحسابهم و**منتج موجود** فكتالوجهم. لهذا نجهّزوهم مرة وحدة.

ZR_PRODUCT_SKU = "BUDAC-ACC-001"

# ⚠️ سعر افتراضي فكتالوج ZR — لازم **أكبر من 0** وإلا يرفضو الإنشاء:
#     400 — "Base price must be greater than 0"
# ما عندو حتى أثر على الفوترة: كل طرد يحمل مبلغو الحقيقي فـ`amount`.
ZR_PRODUCT_BASE_PRICE = 100


def to_international_phone(raw):
    """
    يحوّل الرقم الجزائري للشكل الدولي: 0775297494 → +213775297494

    ⚠️ ZR يرفض الشكل المحلي صراحةً:
    «Phone_Number1 must be a valid international phone number».
    """
    digits = "".join(c for c in (raw or "") if c.isdigit())

    if digits.startswith("00213"):
        digits = digits[5:]
    elif digits.startswith("213"):
        digits = digits[3:]
    elif digits.startswith("0"):
        digits = digits[1:]

    return f"+213{digits}" if digits else ""


def find_customer(phone):
    """يدوّر على زبون عند ZR برقم هاتفو."""
    intl = to_international_phone(phone)
    local = "".join(c for c in (phone or "") if c.isdigit())

    for term in (intl, local):
        if not term:
            continue
        try:
            data = _request("POST", "/customers/search",
                            json={"pageNumber": 1, "pageSize": 20,
                                  "search": {"value": term}})
        except Exception:
            continue

        items = (data or {}).get("items") or []
        if items:
            return items[0].get("id")

    return None


def create_customer(name, phone, order=None):
    """
    ينشئ زبونًا عند ZR ويرجّع معرّفو.

    ⚠️ الشكل من التوثيق الرسمي (`POST /customers/individual`):
      • `phone.number1` بالشكل الدولي
      • `addresses[]` فيها `cityTerritoryId` (الولاية) و
        `districtTerritoryId` (البلدية) — نفس معرّفات الطرد
      • `deliveryPreference` يقبل 'home' ولا 'pickup-point' برك

    ⚠️ نبعثو غير الحقول اللي عندنا: `dateOfBirth` و`timeSlot`
    و`instruction` اختيارية، وبعث قيم مخترعة فيهم يخلق رفضًا بلا فايدة.
    """
    body = {
        "name": (name or "").strip() or "زبون",
        "phone": {"number1": to_international_phone(phone)},
    }

    if order is not None:
        body["deliveryPreference"] = (
            "pickup-point" if order.delivery_type == "office" else "home"
        )

        address = {
            "street": (
                order.pickup_point_name if order.delivery_type == "office"
                else (order.address_note or order.commune or "")
            ) or order.wilaya_name or "",
            "city": order.wilaya_name or "",
            "country": "DZ",
            "isPrimary": True,
        }

        if order.wilaya_external_id:
            address["cityTerritoryId"] = order.wilaya_external_id
        if order.commune_external_id:
            address["districtTerritoryId"] = order.commune_external_id
        if order.commune:
            address["district"] = order.commune

        body["addresses"] = [address]

    data = _request("POST", "/customers/individual", json=body)

    return (data or {}).get("id")


def ensure_customer(order):
    """
    معرّف الزبون عند ZR — نلقاوه ولا ننشئوه.

    يرجّع (المعرّف، السبب إذا فشل).
    """
    if order.customer_external_id:
        return order.customer_external_id, None

    try:
        found = find_customer(order.customer_phone)
        if found:
            return found, None
    except Exception as e:
        return None, f"فشل البحث عن الزبون: {e}"

    try:
        created = create_customer(order.customer_name, order.customer_phone,
                                  order)
    except Exception as e:
        return None, f"فشل إنشاء الزبون: {e}"

    if not created:
        return None, "ZR ما رجّعش معرّف الزبون"

    return created, None


def find_zr_product():
    """يدوّر على منتجنا العام فكتالوج ZR بالـSKU."""
    data = _request("POST", "/products/search",
                    json={"pageNumber": 1, "pageSize": 50,
                          "search": {"value": ZR_PRODUCT_SKU}})

    for item in (data or {}).get("items") or []:
        if (item.get("sku") or "").upper() == ZR_PRODUCT_SKU.upper():
            return item.get("id"), item.get("sku")

    return None, None


def create_zr_product():
    """
    ينشئ منتجًا عامًا واحدًا «أكسسوارات» فكتالوج ZR.

    ⚠️ الشكل من التوثيق (`POST /products`):
      • السعر اسمو **`basePrice`** ماشي `price`
      • `localStock` رقم (نحطوه 0 — ما نتبعوش المخزون عندهم)
      • ماكانش حقل `description` أصلاً — كنت مخترعو

    ⚠️ منتج **واحد** لكل المتجر، ماشي منتج لكل سلعة:
      • ما نكشفوش لشركة التوصيل واش نبيعو بالضبط
      • ما نحتاجوش نزامنو 26 منتجًا وموديلاتهم معاهم
      • المبلغ الحقيقي يمشي فـ`amount` تاع الطرد

    ⚠️ `basePrice` لازم **أكبر من 0**. كنا نبعثو 0 وZR ولّات ترفض:
        400 — "Base price must be greater than 0"
    القيمة هنا ما عندها حتى أثر على الفوترة: كل طرد يحمل مبلغو
    الحقيقي فـ`amount` وفـ`unitPrice`. هذي غير قيمة افتراضية
    فكتالوجهم باش يقبلو الإنشاء.
    """
    data = _request("POST", "/products", json={
        "name": ZR_PRODUCT_LABEL,
        "sku": ZR_PRODUCT_SKU,
        "localStock": 0,
        "basePrice": ZR_PRODUCT_BASE_PRICE,
    })

    return (data or {}).get("id"), ZR_PRODUCT_SKU


def ensure_zr_product():
    """
    معرّف المنتج العام — محفوظ فإعدادات المتجر، ولا نلقاوه/ننشئوه.

    يرجّع ((المعرّف، الرمز)، السبب إذا فشل).
    """
    from models import db, StoreSetting

    saved_id = StoreSetting.get("zr_product_id")
    saved_sku = StoreSetting.get("zr_product_sku")
    if saved_id and saved_sku:
        return (saved_id, saved_sku), None

    try:
        pid, sku = find_zr_product()
    except Exception as e:
        return (None, None), f"فشل البحث فالكتالوج: {e}"

    if not pid:
        try:
            pid, sku = create_zr_product()
        except Exception as e:
            return (None, None), f"فشل إنشاء المنتج: {e}"

    if not pid:
        return (None, None), "ZR ما رجّعش معرّف المنتج"

    StoreSetting.set("zr_product_id", pid)
    StoreSetting.set("zr_product_sku", sku or ZR_PRODUCT_SKU)
    db.session.commit()

    return (pid, sku or ZR_PRODUCT_SKU), None
