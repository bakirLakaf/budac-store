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
    print("مختبر الإرسال")
    print("=" * 60)

    # ⚠️ نفحصو بناء الأشكال بلا أي اتصال بـZR — نستبدلو requests.post
    with app.app_context():
        o = make(delivery_type="office",
                 pickup_point_id="Hub Setif 19 مكتب سطيف",
                 pickup_point_name="Hub Setif 19 مكتب سطيف — Cité Dallas")

        # ⚠️ المختبر يفحص المفاتيح قبل أي طلب — نحطّو وهميات
        os.environ["ZR_TENANT_ID"] = "test"
        os.environ["ZR_API_KEY"] = "test"

        calls = []

        class FakeResp:
            status_code = 400
            text = ""
            content = b""

        real_post = zr.requests.post
        zr.requests.post = lambda url, **kw: (
            calls.append(kw.get("json")) or FakeResp()
        )
        try:
            attempts = zr.send_variants(o, "tid-1")
        finally:
            zr.requests.post = real_post

        check("يجرّب 4 أشكال للمكتب", len(attempts) == 4, len(attempts))
        check("الشكل الأول بلا pickupPointId",
              "pickupPointId" not in calls[0], calls[0].keys())
        check("شكل بالبلدية معمّرة موجود",
              any(c.get("commune") == "Setif" for c in calls),
              [c.get("commune") for c in calls])
        check("كل الأشكال فيها territoryId",
              all(c.get("territoryId") == "tid-1" for c in calls))
        check("كل الأشكال deliveryType = pickup-point",
              all(c["deliveryType"] == "pickup-point" for c in calls))

        # 🔴 الأهم: يوقف عند أول نجاح باش ما ينشئش طرودًا مكررة
        calls.clear()
        state = {"n": 0}

        class OkResp:
            status_code = 200
            text = '{"trackingNumber":"ZR-1"}'
            content = b"x"

        def post_second_ok(url, **kw):
            calls.append(kw.get("json"))
            state["n"] += 1
            return OkResp() if state["n"] == 2 else FakeResp()

        zr.requests.post = post_second_ok
        try:
            attempts = zr.send_variants(o, "tid-1")
        finally:
            zr.requests.post = real_post

        check("🔴 يوقف عند أول نجاح (ماشي طرود مكررة)",
              len(calls) == 2 and attempts[-1]["ok"], len(calls))

    print("\n" + "=" * 60)
    print("مختبر الأبعاد")
    print("=" * 60)

    with app.app_context():
        o = make(delivery_type="office",
                 pickup_point_id="Hub Setif 19 مكتب سطيف",
                 pickup_point_name="Hub Setif 19 مكتب سطيف")

        calls = []
        real_post = zr.requests.post
        zr.requests.post = lambda url, **kw: (
            calls.append(kw.get("json")) or FakeResp()
        )
        try:
            dims = zr.lab_dimensions(o, "tid-1", test_amount=100)
        finally:
            zr.requests.post = real_post

        check("يجرّب 5 أبعاد", len(dims) == 5, len(dims))

        # 🔴 الأهم: الطرود تجريبية — ما نبعثوش مبلغ الزبون الحقيقي
        check("🔴 كل المبالغ تجريبية (ماشي 3360)",
              all(c["amount"] != 3360 for c in calls),
              [c["amount"] for c in calls])
        check("المراجع كلها TEST وفريدة",
              all(str(c["reference"]).startswith("TEST") for c in calls)
              and len({c["reference"] for c in calls}) == 5,
              [c["reference"] for c in calls])
        check("سعر المنتج يطابق المبلغ فكل محاولة",
              all(c["orderedProducts"][0]["price"] == c["amount"] for c in calls),
              [(c["amount"], c["orderedProducts"][0]["price"]) for c in calls])

        check("كاين بُعد «للدار»",
              any(c["deliveryType"] == "home" for c in calls))
        check("ماكانش pickupPointId فأبعاد الدار",
              all("pickupPointId" not in c for c in calls
                  if c["deliveryType"] == "home"))
        check("كاين بُعد بلا territoryId",
              any("territoryId" not in c for c in calls))
        check("كاين بُعد بلا حروف عربية",
              any(not any("\u0600" <= ch <= "\u06FF" for ch in c["customerName"])
                  for c in calls), [c["customerName"] for c in calls])

    src2 = open("routes/admin.py", encoding="utf-8").read()
    check("🔴 الطرد التجريبي ما يتحفظش كرقم تتبّع للطلبية",
          "ماشي طلبية الزبون" in src2)

    print("\n" + "=" * 60)
    print("معرّف المكتب (hub)")
    print("=" * 60)

    with app.app_context():
        # معرّف محفوظ يتستعمل بلا أي طلب لـZR
        saved_hub = make(delivery_type="office", hub_external_id="hub-uuid-1",
                         pickup_point_id="Hub Setif 19 مكتب سطيف")
        hid, why = zr.resolve_hub_id(saved_hub)
        check("معرّف الـhub المحفوظ يتستعمل مباشرة",
              hid == "hub-uuid-1" and why is None)

        home = make(delivery_type="home", commune="سطيف")
        hid, why = zr.resolve_hub_id(home)
        check("توصيل الدار: بلا hub", hid is None)

        # 🔴 الـpayload لازم يبعث UUID ماشي الاسم
        payload = zr.build_parcel_payload(saved_hub, "tid-1")
        check("🔴 pickupPointId = UUID ماشي الاسم",
              payload["pickupPointId"] == "hub-uuid-1", payload["pickupPointId"])

        # بلا UUID محفوظ، الاسم يبقى كتراجع (أحسن من والو)
        no_hub = make(delivery_type="office",
                      pickup_point_id="Hub Setif 19 مكتب سطيف")
        payload2 = zr.build_parcel_payload(no_hub, "tid-1")
        check("بلا UUID: الاسم كتراجع",
              payload2["pickupPointId"] == "Hub Setif 19 مكتب سطيف")

        # hub_id ممرّر مباشرة يغلب المحفوظ
        payload3 = zr.build_parcel_payload(no_hub, "tid-1", hub_id="fresh-uuid")
        check("hub_id الممرّر عندو الأولوية",
              payload3["pickupPointId"] == "fresh-uuid")

    src3 = open("services/zrexpress.py", encoding="utf-8").read()
    check("المسار الصحيح /hubs/search موجود", '"/hubs/search"' in src3)

    print("\n" + "=" * 60)
    print(f"نجح: {len(PASSED)}  ·  فشل: {len(FAILED)}")
    print("=" * 60)
    if FAILED:
        for f in FAILED:
            print("  ❌ " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
