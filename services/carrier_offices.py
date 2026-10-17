"""
مكاتب الاستلام تاع شركات التوصيل — من ملفات محلية.

⚠️ علاش خدمة مستقلة؟
قبل، منطق المكاتب كان محشورًا فـ`services/zrexpress.py`، فالمسار
`/api/pickup-points` كان يخدم لـZR **برك**. النتيجة: الزبون اللي
يختار World Express + «التوصيل للمكتب» يلقى قائمة فارغة، والحقل
مطلوب، فالطلبية ما تكملش أبدًا — رغم أن السعر محسوب ومعروض.

درك المنطق مركزي: أي شركة نزيدولها ملف مكاتب تخدم على طول، بلا
ما نلمسو كود المسارات.

باش تزيد شركة:
  1. حط ملفها فـ`data/<code>_offices.json`
  2. زيد سطر فـOFFICE_FILES تحت
  خلاص — المسار والواجهة يخدمو بلا تبديل.

شكل الملف (نفس شكل ZR):
[
  {
    "name": "Hub Jijel مكتب جيجل 18",
    "wilaya_code": "18",
    "commune": "Jijel",
    "address": "شارع ...",
    "pickup": true
  }
]

⚠️ `pickup: false` معناها المكتب موجود لكن **ما يستلمش** الزبائن
(مركز فرز مثلاً). ما يبانش فالقائمة.
"""

import json
import os

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# رمز الشركة → اسم ملف مكاتبها
OFFICE_FILES = {
    "zrexpress": "zr_offices.json",
    "worldexpress": "world_offices.json",   # 95 مكتب فـ54 ولاية
}

# شركات مكاتبها تجي من الـAPI مباشرة (مخزّنة فالخدمة نفسها 6 ساعات) وماشي من ملف.
# Yalidine: GET /centers — ما نخزّنوش الفشل هنا باش يرجع يخدم كي ترجع الشبكة.
LIVE_LOADERS = {
    "yalidine": ("services.yalidine", "centers_as_offices"),
}

_cache = {}


def _load(carrier_code):
    """
    يقرا ملف المكاتب تاع شركة، ويخزّنو فالذاكرة.

    ⚠️ الملف الناقص ماشي خطأ: الشركة تقدر تكون بلا مكاتب بعد.
    نرجّعو قائمة فارغة، والواجهة توري رسالة مفهومة بدل ما تطيح.
    """
    if carrier_code in LIVE_LOADERS:
        module_name, func_name = LIVE_LOADERS[carrier_code]
        try:
            import importlib
            return getattr(importlib.import_module(module_name), func_name)()
        except Exception as e:  # noqa: BLE001 — الشبكة طاحت: قائمة فارغة، ماشي 500
            try:
                from flask import current_app
                current_app.logger.warning("🏢 مكاتب %s: فشل الجلب: %s", carrier_code, e)
            except Exception:
                pass
            return []

    if carrier_code in _cache:
        return _cache[carrier_code]

    filename = OFFICE_FILES.get(carrier_code)
    if not filename:
        _cache[carrier_code] = []
        return []

    path = os.path.join(_DATA_DIR, filename)

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = []
    except (json.JSONDecodeError, OSError) as e:
        # ⚠️ ما نبلعوهش بصمت — ملف مكسور لازم يبان فالسجلات،
        # وإلا نقعدو نتساءلو علاش المكاتب اختفات.
        try:
            from flask import current_app
            current_app.logger.warning(
                "🏢 ملف مكاتب %s مكسور: %s", carrier_code, e
            )
        except Exception:
            pass
        data = []

    _cache[carrier_code] = data
    return data


def has_offices(carrier_code):
    """واش هاد الشركة عندها قائمة مكاتب أصلاً؟"""
    return bool(_load(carrier_code))


def get_pickup_points(carrier_code, wilaya_code=None):
    """
    مكاتب الاستلام تاع شركة فولاية معيّنة — للزبون فصفحة الطلب.

    نرجّعو غير اللي `pickup: true`.
    """
    offices = [o for o in _load(carrier_code) if o.get("pickup")]

    if wilaya_code:
        target = str(wilaya_code).zfill(2)
        offices = [o for o in offices if str(o.get("wilaya_code", "")).zfill(2) == target]

    return [
        {
            # الاسم هو المعرّف — يتبعث لشركة التوصيل كيما هو
            "id": o.get("name", ""),
            "name": o.get("name", ""),
            "address": o.get("address") or o.get("commune", ""),
            # الهاتف يفيد الزبون: يعيّط قبل ما يتحرّك ويتأكد أن الطرد وصل
            "phone": o.get("phone", ""),
            "wilaya_code": str(o.get("wilaya_code", "")).zfill(2),
        }
        for o in offices
    ]


def get_office_wilayas(carrier_code):
    """رموز الولايات اللي فيها مكتب استلام واحد على الأقل."""
    return {
        str(o.get("wilaya_code", "")).zfill(2)
        for o in _load(carrier_code)
        if o.get("pickup")
    }


def get_office(carrier_code, name, wilaya_code=None):
    """
    يرجّع المكتب كامل (بالبلدية تاعو) من اسمو — ولا None إذا ما لقاهش.

    نحتاجوها كي نبعثو طلبية مكتب لـWorld Express: ECOTRACK يستنّى **بلدية**
    المكتب، والزبون اختار الاسم برك.
    """
    target = (name or "").strip().lower()
    if not target:
        return None
    wanted = str(wilaya_code).zfill(2) if wilaya_code else None
    for o in _load(carrier_code):
        if (o.get("name") or "").strip().lower() != target:
            continue
        if wanted and str(o.get("wilaya_code", "")).zfill(2) != wanted:
            continue
        return o
    return None


def clear_cache(carrier_code=None):
    """يفرّغ الذاكرة — بعد تحديث ملف المكاتب."""
    if carrier_code:
        _cache.pop(carrier_code, None)
    else:
        _cache.clear()


# ═══════════════════════════════════════════════════════════
# أسعار التوصيل من جدول رسمي محلي
# ═══════════════════════════════════════════════════════════

RATE_FILES = {
    "worldexpress": "world_rates.json",   # 57 ولاية، من جدول World الرسمي
    # ⚠️ ZR عندو API حيّ (زر «زامن أسعار ZR»)، وهو المصدر الأول.
    # هاد الملف نسخة احتياطية: كي المفاتيح تطيح ولا الـAPI ما يجاوبش،
    # نرجّعو للأسعار المعروفة بدل ما نبقاو بلا والو.
    "zrexpress": "zr_rates.json",         # 58 ولاية، نسخة احتياطية
}


def sync_rates_from_file(carrier_code):
    """
    يحدّث أسعار شركة من جدولها الرسمي المخزّن عندنا.

    ⚠️ علاش ملف وماشي API؟
    World Express (ECOTRACK) الإرسال التلقائي يخدم من v171 (WORLD_API_TOKEN). لكن
    جدول أسعارهم منشور، فنخزّنوه ونقارنو بيه بضغطة بدل ما نقلّبو
    58 خانة بالعين.

    القاعدة: الولاية اللي ماكانتش فالجدول = **الخدمة غير متوفرة**
    → نحطو None. صفر ما يعنيش مجاني هنا؛ يعني ما توصلش.

    يرجّع تقرير: واش تبدّل، واش بقى كيما هو.
    """
    import json as _json
    from models import db, Carrier, ShippingRate

    filename = RATE_FILES.get(carrier_code)
    if not filename:
        raise ValueError(f"ماكانش جدول أسعار محفوظ لـ«{carrier_code}»")

    carrier = Carrier.query.filter_by(code=carrier_code).first()
    if not carrier:
        raise ValueError("الشركة ماكانتش فقاعدة البيانات")

    with open(os.path.join(_DATA_DIR, filename), encoding="utf-8") as f:
        rows = _json.load(f)

    incoming = {
        str(r["wilaya_code"]).zfill(2): (r.get("home"), r.get("office"))
        for r in rows
    }

    changes, unchanged, not_listed = [], 0, []

    for rate in ShippingRate.query.filter_by(carrier_id=carrier.id).all():
        new = incoming.get(rate.wilaya_code)

        if new is None:
            # ⚠️ ماكانتش فجدولهم = ما توصلش. نصفّيوها بدل ما نخلّيو
            # سعرًا قديمًا يخلّي الزبون يطلب حاجة ما توصلش.
            if rate.home_price is not None or rate.office_price is not None:
                changes.append({
                    "wilaya": f"{rate.wilaya_code} ~ {rate.wilaya_name}",
                    "old_home": rate.home_price, "new_home": None,
                    "old_office": rate.office_price, "new_office": None,
                })
                rate.home_price = None
                rate.office_price = None
            else:
                not_listed.append(f"{rate.wilaya_code} ~ {rate.wilaya_name}")
            continue

        new_home, new_office = new

        if rate.home_price == new_home and rate.office_price == new_office:
            unchanged += 1
            continue

        changes.append({
            "wilaya": f"{rate.wilaya_code} ~ {rate.wilaya_name}",
            "old_home": rate.home_price, "new_home": new_home,
            "old_office": rate.office_price, "new_office": new_office,
        })
        rate.home_price = new_home
        rate.office_price = new_office

    db.session.commit()

    return {
        "from_file": len(incoming),
        "changed": len(changes),
        "unchanged": unchanged,
        "missing": not_listed,
        "details": changes[:60],
    }
