# -*- coding: utf-8 -*-
"""
فحص معرّف الوجهة (territoryId) عند ZR.

🔴 الخلل اللي هاد الملف موجود علاه — طلبية BD-970966:
طلبية «توصيل للمكتب» (Hub Setif) اتبعثات لـZR بلا `territoryId`،
وZR رد **400 بلا أي رسالة**. السبب: مسار الإرسال كان فيه شرط
`if order.delivery_type != "office"`, يعني طلبيات المكتب ما كانت
تدوّر أبدًا على المعرّف. وبلديتها فارغة أصلاً (نفرّغوها عمدًا فالتحقق).

النتيجة: **كل** طلبيات المكتب كانت تفشل، بلا استثناء.

الحل: بلدية المكتب تجي من ملف المكاتب نفسو (Hub Setif → Setif).

التشغيل: python tests/test_zr_territory.py
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "test-key-for-testing-only")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import create_app
from models import db, Order
from services import zrexpress as zr

PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name
          + ("  → " + str(detail) if not ok and detail != "" else ""))


def make(**kw):
    base = dict(
        order_number="BD-970966", customer_name="Nacera Djessas",
        customer_phone="0775297494", wilaya_code="19", wilaya_name="سطيف",
        total=3360, subtotal=2860, shipping_price=500,
    )
    base.update(kw)
    return Order(**base)


def main():
    app = create_app()
    app.config["TESTING"] = True

    print("\n" + "=" * 60)
    print("بلدية المكتب — من ملف المكاتب")
    print("=" * 60)

    with app.app_context():
        db.create_all()

        # ───────── الطلبية الحقيقية اللي فشلات ─────────
        o = make(delivery_type="office",
                 pickup_point_id="Hub Setif 19 مكتب سطيف",
                 pickup_point_name="Hub Setif 19 مكتب سطيف — Cité Dallas 3eme Tranche")

        check("🔴 لقينا بلدية مكتب سطيف",
              zr.office_commune(o) == "Setif", zr.office_commune(o))

        # الزبون يشوف «الاسم — العنوان»، فالتطابق لازم يقبل البداية
        o2 = make(delivery_type="office", pickup_point_id="",
                  pickup_point_name="Hub Setif 19 مكتب سطيف — Cité Dallas 3eme Tranche")
        check("يطابق حتى بلا pickup_point_id",
              zr.office_commune(o2) == "Setif", zr.office_commune(o2))

        o3 = make(delivery_type="office",
                  pickup_point_id="Hub El Eulma 19 مكتب العلمة")
        check("مكتب آخر فنفس الولاية يعطي بلديتو",
              zr.office_commune(o3) == "El Eulma", zr.office_commune(o3))

        # ⚠️ مكتب مجهول: نرجّعو None برسالة، ماشي تخمين
        o4 = make(delivery_type="office", pickup_point_name="مكتب ما كاينش")
        check("مكتب مجهول يرجّع None", zr.office_commune(o4) is None)

        # توصيل للمنزل ما عندوش بلدية مكتب
        o5 = make(delivery_type="home", commune="سطيف")
        check("توصيل المنزل: ماكانش بلدية مكتب",
              zr.office_commune(o5) is None)

        print("\n" + "=" * 60)
        print("resolve_territory_id — مصدر واحد للنوعين")
        print("=" * 60)

        # معرّف محفوظ يتستعمل على طول، بلا أي طلب لـZR
        saved = make(delivery_type="office", commune_external_id="abc-123",
                     pickup_point_id="Hub Setif 19 مكتب سطيف")
        tid, why = zr.resolve_territory_id(saved)
        check("المعرّف المحفوظ يتستعمل مباشرة", tid == "abc-123" and why is None)

        # بلا شبكة، البحث يفشل — لكن **السبب** لازم يكون واضحًا
        unknown = make(delivery_type="office", pickup_point_name="مكتب ما كاينش")
        tid, why = zr.resolve_territory_id(unknown)
        check("مكتب مجهول: يرجّع سبب مفهوم",
              tid is None and why and "بلدية المكتب" in why, why)

        no_commune = make(delivery_type="home", commune="")
        tid, why = zr.resolve_territory_id(no_commune)
        check("منزل بلا بلدية: سبب واضح",
              tid is None and why == "الطلبية بلا بلدية", why)

        print("\n" + "=" * 60)
        print("الـpayload")
        print("=" * 60)

        payload = zr.build_parcel_payload(saved, "abc-123")
        check("territoryId يدخل فالـpayload",
              payload.get("territoryId") == "abc-123", payload.get("territoryId"))
        check("deliveryType = pickup-point",
              payload["deliveryType"] == "pickup-point")
        check("pickupPointId يتبعث",
              payload.get("pickupPointId") == "Hub Setif 19 مكتب سطيف")
        check("العنوان ماشي فارغ (ZR يرفضو)", bool(payload["deliveryAddress"]))

    print("\n" + "=" * 60)
    print("مسار الإرسال")
    print("=" * 60)

    src = open("routes/admin.py", encoding="utf-8").read()
    check("🔴 ماكانش استثناء للمكتب فالبحث",
          'order.delivery_type != "office" and order.commune' not in src)
    check("الإرسال يستعمل resolve_territory_id",
          "resolve_territory_id(order)" in src)
    check("التشخيص يستعمل بلدية المكتب",
          "zr.office_commune(order)" in src)

    print("\n" + "=" * 60)
    print(f"نجح: {len(PASSED)}  ·  فشل: {len(FAILED)}")
    print("=" * 60)
    if FAILED:
        for f in FAILED:
            print("  ❌ " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
