# -*- coding: utf-8 -*-
"""
فحص مكاتب الاستلام لكل الشركات.

🔴 الخلل اللي هاد الملف موجود علاه:
`/api/pickup-points` كان فيه `if carrier != "zrexpress": return []`.
النتيجة: World Express عندو 57 سعر مكتب معمّر، والزبون اللي يختار
«التوصيل للمكتب» يلقى قائمة فارغة وحقلاً مطلوبًا — الطلبية توقف
بلا سبب مفهوم. السعر يتحسب ويتعرض، والشراء مستحيل.

التشغيل: python tests/test_offices.py
"""
import os, sys, json, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "test-key-for-testing-only")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import create_app
from models import db, Carrier, ShippingRate
from services import carrier_offices as co

PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name
          + ("  → " + str(detail) if not ok and detail != "" else ""))


def build():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        for code, name in [("zrexpress", "ZR Express"),
                           ("worldexpress", "World Express")]:
            db.session.add(Carrier(code=code, name=name, is_active=True))
        db.session.commit()
        we = Carrier.query.filter_by(code="worldexpress").first()
        db.session.add(ShippingRate(
            carrier_id=we.id, wilaya_code="18", wilaya_name="جيجل",
            home_price=800, office_price=450,
        ))
        db.session.commit()
    return app


def main():
    app = build()
    client = app.test_client()

    print("\n" + "=" * 60)
    print("مكاتب الاستلام — أي شركة، ماشي ZR برك")
    print("=" * 60)

    # ───────── ZR باقي يخدم كيما كان ─────────
    zr = client.get("/api/pickup-points?carrier=zrexpress&wilaya=18").get_json()
    check("ZR: يرجّع مكاتب جيجل", len(zr.get("points", [])) > 0, zr)
    check("ZR: كل مكتب فيه اسم ومعرّف",
          all(p.get("id") and p.get("name") for p in zr.get("points", [])))

    # ───────── 🔴 الفحص الأساسي: شركة أخرى بملف مكاتب ─────────
    fake = [
        {"name": "Hub Jijel WE 18", "wilaya_code": "18",
         "commune": "Jijel", "address": "شارع الاستقلال", "pickup": True},
        {"name": "Centre Tri Jijel", "wilaya_code": "18",
         "commune": "Jijel", "address": "", "pickup": False},
        {"name": "Hub Alger WE 16", "wilaya_code": "16",
         "commune": "Alger", "address": "", "pickup": True},
    ]
    path = os.path.join(os.path.dirname(co.__file__), "..", "data",
                        "world_offices.json")
    backup = open(path, encoding="utf-8").read()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(fake, f, ensure_ascii=False)
        co.clear_cache("worldexpress")

        we = client.get("/api/pickup-points?carrier=worldexpress&wilaya=18").get_json()
        names = [p["name"] for p in we.get("points", [])]

        check("🔴 World: يرجّع مكاتبو (ماشي فارغة)", len(names) == 1, names)
        check("🔴 مركز الفرز ما يبانش (pickup=false)",
              "Centre Tri Jijel" not in names, names)
        check("ما يخلطش ولايات (16 ما تبانش فـ18)",
              "Hub Alger WE 16" not in names, names)
        check("العنوان يوصل للزبون",
              we["points"][0]["address"] == "شارع الاستقلال")
        check("الهاتف يرجع فارغًا إذا ماكانش، بلا انهيار",
              we["points"][0].get("phone") == "")

        wilayas = co.get_office_wilayas("worldexpress")
        check("ولايات فيها مكاتب: 18 و16 برك",
              wilayas == {"18", "16"}, wilayas)

        # ولاية بلا مكتب: رسالة واضحة، ماشي no_list
        empty = client.get("/api/pickup-points?carrier=worldexpress&wilaya=31").get_json()
        check("ولاية بلا مكتب: قائمة فارغة برسالة",
              empty["points"] == [] and empty.get("message"), empty)
        check("ولاية بلا مكتب ماشي «بلا قائمة»",
              not empty.get("no_list"), empty)

    finally:
        with open(path, "w", encoding="utf-8") as f:
            f.write(backup)
        co.clear_cache("worldexpress")

    # ───────── الملف الحقيقي تاع World Express ─────────
    print("\n" + "=" * 60)
    print("ملف World Express الحقيقي")
    print("=" * 60)

    import json as _json
    real = _json.load(open("data/world_offices.json", encoding="utf-8"))
    check("الملف فيه مكاتب", len(real) >= 90, len(real))

    # ⚠️ رمز الولاية لازم نص بخانتين — "1" ولا 1 يكسّر المطابقة
    bad_code = [o["name"] for o in real
                if not isinstance(o.get("wilaya_code"), str)
                or len(o["wilaya_code"]) != 2]
    check("كل الرموز نص بخانتين", not bad_code, bad_code[:3])

    bad_wil = [o["name"] for o in real
               if not o["wilaya_code"].isdigit()
               or not (1 <= int(o["wilaya_code"]) <= 58)]
    check("كل الرموز بين 01 و58", not bad_wil, bad_wil[:3])

    no_name = [o for o in real if not o.get("name")]
    check("كل مكتب عندو اسم", not no_name, len(no_name))

    dupes = [n for n in {o["name"] for o in real}
             if [o["name"] for o in real].count(n) > 1]
    check("ماكانش اسم مكرر", not dupes, dupes[:3])

    check("كل المكاتب pickup=true",
          all(o.get("pickup") for o in real))

    wilayas = co.get_office_wilayas("worldexpress")
    check("مغطّي 50 ولاية فأكثر", len(wilayas) >= 50, len(wilayas))

    jijel = client.get("/api/pickup-points?carrier=worldexpress&wilaya=18").get_json()
    check("جيجل فيها مكتب", len(jijel.get("points", [])) >= 1, jijel)
    check("الهاتف يوصل للزبون",
          bool(jijel["points"][0].get("phone")), jijel["points"][0])

    alger = client.get("/api/pickup-points?carrier=worldexpress&wilaya=16").get_json()
    check("الجزائر فيها عدة مكاتب", len(alger.get("points", [])) >= 5,
          len(alger.get("points", [])))

    # ───────── شركة بلا ملف مكاتب ─────────
    print("\n" + "=" * 60)
    print("شركة بلا قائمة مكاتب")
    print("=" * 60)

    none = client.get("/api/pickup-points?carrier=yalidine&wilaya=18").get_json()
    check("ترجّع no_list باش الواجهة تخبّي الخيار",
          none.get("no_list") is True, none)
    check("الرسالة توجّه للتوصيل للمنزل",
          "للمنزل" in (none.get("message") or ""), none)

    # ⚠️ الشركة اللي ماكانش عندها ملف أصلاً ما تنهارش
    check("شركة بلا ملف = بلا مكاتب، بلا انهيار",
          co.has_offices("yalidine") is False)

    # ───────── الواجهة تتعامل معاها ─────────
    # ───────── مزامنة أسعار World من الجدول ─────────
    print("\n" + "=" * 60)
    print("مزامنة أسعار World")
    print("=" * 60)

    rates = _json.load(open("data/world_rates.json", encoding="utf-8"))
    check("الجدول فيه 57 ولاية", len(rates) == 57, len(rates))
    check("كل الرموز نص بخانتين",
          all(isinstance(r["wilaya_code"], str) and len(r["wilaya_code"]) == 2
              for r in rates))
    check("كل الأسعار موجبة",
          all((r["home"] is None or r["home"] > 0)
              and (r["office"] is None or r["office"] > 0) for r in rates))

    with app.app_context():
        from services.carrier_offices import sync_rates_from_file
        from models import Carrier as _C, ShippingRate as _SR

        we = _C.query.filter_by(code="worldexpress").first()
        r18 = _SR.query.filter_by(carrier_id=we.id, wilaya_code="18").first()

        # ⚠️ نخربقو السعر عمدًا ونتأكد أن المزامنة ترجّعو
        r18.home_price = 99999
        db.session.commit()

        res = sync_rates_from_file("worldexpress")
        db.session.refresh(r18)

        check("🔴 المزامنة تصلّح السعر المخربق",
              r18.home_price == 850, r18.home_price)
        check("توري عدد اللي تبدّل", res["changed"] == 1, res["changed"])
        check("التقرير يوري القديم والجديد",
              res["details"][0]["old_home"] == 99999
              and res["details"][0]["new_home"] == 850, res["details"][:1])

        # مرة ثانية: ماكانش تبديل
        res2 = sync_rates_from_file("worldexpress")
        check("مزامنة ثانية: صفر تبديل", res2["changed"] == 0, res2["changed"])

        # ⚠️ ولاية ماكانتش فجدولهم لازم تولّي «غير متوفر»، ماشي تبقى
        # بسعر قديم يخلّي الزبون يطلب حاجة ما توصلش
        r18.wilaya_code = "50"
        db.session.commit()
        sync_rates_from_file("worldexpress")
        db.session.refresh(r18)
        check("ولاية برّا الجدول تولّي غير متوفرة",
              r18.home_price is None and r18.office_price is None,
              (r18.home_price, r18.office_price))

    print("\n" + "=" * 60)
    print("صفحة الطلب")
    print("=" * 60)

    html = open("templates/shop/checkout.html", encoding="utf-8").read()
    check("الواجهة تخبّي خيار المكتب عند no_list",
          "syncOfficeOption" in html and "no_list" in html)
    check("🔴 تحيّد required كي القائمة فارغة",
          "pickupSelect.required = false" in html)
    check("تعاود تفحص كي يتبدّل الشركة",
          "carrier.addEventListener('change', syncOfficeOption)" in html)

    print("\n" + "=" * 60)
    print(f"نجح: {len(PASSED)}  ·  فشل: {len(FAILED)}")
    print("=" * 60)
    if FAILED:
        for f in FAILED:
            print("  ❌ " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
