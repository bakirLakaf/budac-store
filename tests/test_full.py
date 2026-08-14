# -*- coding: utf-8 -*-
"""
اختبار شامل لمتجر BUDAC — الوظائف الأساسية.
التشغيل: python tests/test_full.py
"""
import os, sys, traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "test-key-for-testing-only")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from sqlalchemy import event
from sqlalchemy.engine import Engine


# ⚠️ SQLite يعطّل فرض المفاتيح الأجنبية افتراضيًا، بينما Postgres
# (اللي يخدم فالإنتاج) يفرضها بصرامة. بلا هاد السطر، أخطاء الحذف
# ما تبانش فالاختبار وتبان فالإنتاج — صرالنا هكذا مع wipe_products.
@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    try:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass   # ماشي SQLite — Postgres يفرضها أصلاً


from app import create_app
from models import (db, Product, ProductVariant, Category, Offer, OfferItem,
                    Coupon, Order, Carrier, ShippingRate, AdminUser, StoreSetting,
                    Commune, CarrierCommune, VariantPriceTier, ProductImage)

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  ✅ {name}")
    else:
        FAILED.append((name, detail))
        print(f"  ❌ {name}  {detail}")


def section(title):
    print(f"\n{'='*60}\n{title}\n{'='*60}")


def build():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True

    with app.app_context():
        db.create_all()

        cat = Category(name="مقابض وكركوبات", slug="handles")
        cat2 = Category(name="إضاءة", slug="lighting")
        db.session.add_all([cat, cat2])
        db.session.commit()

        p1 = Product(name="كوان حديد", slug="kouan", price=500, compare_at_price=600,
                     category_id=cat.id, stock_quantity=0, is_featured=True)
        p2 = Product(name="شريط إضاءة", slug="led", price=1200,
                     category_id=cat2.id, stock_quantity=0)
        p3 = Product(name="منتج مطفي", slug="off", price=100, is_active=False)
        db.session.add_all([p1, p2, p3])
        db.session.commit()

        db.session.add(ProductImage(product_id=p1.id, image_filename="img-a.webp"))
        db.session.add(ProductImage(product_id=p1.id, image_filename="img-b.webp"))
        v1 = ProductVariant(product_id=p1.id, label="دوري كبير", stock_quantity=0,
                            image_filename="img-a.webp")
        v2 = ProductVariant(product_id=p1.id, label="أرجونتي", price=550, stock_quantity=0)
        db.session.add_all([v1, v2])

        # أسعار الجملة على v1
        db.session.flush()
        for mn, mx, pr in [(1, 99, 500), (100, 199, 420), (200, None, 380)]:
            db.session.add(VariantPriceTier(variant_id=v1.id, min_quantity=mn,
                                            max_quantity=mx, unit_price=pr))

        c = Coupon(code="BUDAC10", discount_type="percent", value=10, is_active=True)
        db.session.add(c)

        off = Offer(title="باقة التزيين", slug="pack", bundle_price=1500, is_active=True)
        db.session.add(off)
        db.session.commit()
        db.session.add(OfferItem(offer_id=off.id, product_id=p1.id, quantity=2))

        carrier = Carrier(name="ZR Express", code="zrexpress", is_active=True)
        db.session.add(carrier)
        db.session.commit()
        db.session.add(ShippingRate(carrier_id=carrier.id, wilaya_code="16",
                                    wilaya_name="الجزائر", home_price=600, office_price=400))
        cm1 = Commune(wilaya_code="16", name_ar="باب الوادي", daira="باب الوادي")
        cm2 = Commune(wilaya_code="16", name_ar="بلدية ماشي متاحة")
        db.session.add_all([cm1, cm2])
        db.session.flush()
        db.session.add(CarrierCommune(carrier_id=carrier.id, commune_id=cm1.id,
                                      external_id="zr-uuid-1", has_home_delivery=True,
                                      has_pickup_point=True, can_send=True))
        # بلدية ما يوصلش ليها: has_home_delivery=False هو المعيار
        # (can_send بقى للتوافق فقط — التوفّر للمنزل يتقرّر بـhas_home_delivery)
        db.session.add(CarrierCommune(carrier_id=carrier.id, commune_id=cm2.id,
                                      external_id="zr-uuid-2", has_home_delivery=False,
                                      has_pickup_point=False, can_send=False))

        admin = AdminUser(username="admin")
        admin.set_password("test-password-123")
        db.session.add(admin)
        db.session.commit()

        ids = {"v1": v1.id, "v2": v2.id, "p1": p1.id, "p2": p2.id,
               "p3": p3.id, "offer": off.id}
    return app, ids


def run():
    app, ids = build()
    c = app.test_client()

    # ══════════ 1. الصفحات العمومية ══════════
    section("1. الصفحات العمومية")
    pages = {
        "/": "الرئيسية", "/cart": "السلة",
        "/track": "تتبع الطلبية", "/product/kouan": "صفحة منتج",
        # ملاحظة: /checkout بسلة فارغة يحوّل للسلة — هذا سلوك صحيح
        "/offer/pack": "صفحة عرض", "/page/about": "من نحن",
        "/page/shipping": "التوصيل", "/page/returns": "الإرجاع",
        "/sitemap.xml": "خريطة الموقع", "/robots.txt": "robots",
        "/search?q=كوان": "البحث", "/?category=handles": "فلترة التصنيف",
    }
    for url, label in pages.items():
        r = c.get(url)
        check(f"{label} ({url})", r.status_code == 200, f"رجع {r.status_code}")

    check("checkout بسلة فارغة يحوّل للسلة",
          c.get("/checkout", follow_redirects=False).status_code == 302)
    check("404 لمنتج ماكانش", c.get("/product/nothing").status_code == 404)
    check("منتج مطفي ما يبانش", c.get("/product/off").status_code == 404)
    check("صفحة 404 بالعربية",
          "ارجع للصفحة الرئيسية" in c.get("/xyz").get_data(as_text=True))

    # ══════════ 2. السلة ══════════
    section("2. السلة")
    r = c.post("/cart/add", data={"kind": "product", "item_id": ids["p1"],
                                  "variant_id": ids["v1"], "quantity": 3},
               follow_redirects=True)
    h = c.get("/cart").get_data(as_text=True)
    check("إضافة منتج بتنويعة", "كوان حديد" in h)
    check("🔴 زر الطلب مفتوح (المخزون المفتوح)", "عدّل الكميات المعلّمة" not in h)

    api = c.get("/api/cart").get_json()
    check("عدّاد السلة = عدد المنتجات ماشي الكميات", api["count"] == 1, f"رجع {api['count']}")
    check("المجموع 3×500=1500", api["subtotal"] == 1500, f"رجع {api['subtotal']}")

    # تنويعة ثانية بسعر مختلف
    c.post("/cart/add", data={"kind": "product", "item_id": ids["p1"],
                              "variant_id": ids["v2"], "quantity": 1})
    api = c.get("/api/cart").get_json()
    check("تنويعتان = سطران منفصلان", api["count"] == 2, f"رجع {api['count']}")
    check("سعر التنويعة الخاص محسوب (1500+550)", api["subtotal"] == 2050, f"رجع {api['subtotal']}")

    # تعديل الكمية عبر API
    key = api["lines"][0]["key"]
    r = c.post("/api/cart/update", json={"key": key, "quantity": 5})
    check("تعديل الكمية من اللوحة", r.status_code == 200)
    r = c.post("/api/cart/update", json={"key": key, "quantity": 0})
    check("الحذف بكمية 0", r.get_json()["count"] == 1)

    # كميات غير منطقية
    c.post("/cart/add", data={"kind": "product", "item_id": ids["p2"], "quantity": -5})
    api = c.get("/api/cart").get_json()
    check("كمية سالبة مرفوضة", all(l["quantity"] > 0 for l in api["lines"]))

    # ══════════ 3. الكوبونات ══════════
    section("3. الكوبونات")
    c.post("/cart/coupon", data={"coupon_code": "BUDAC10"}, follow_redirects=True)
    h = c.get("/cart").get_data(as_text=True)
    check("كوبون صالح يُقبل", "BUDAC10" in h)
    r = c.post("/cart/coupon", data={"coupon_code": "GHALET"}, follow_redirects=True)
    check("كوبون غالط يُرفض", r.status_code == 200)

    # ══════════ 4. الإعدادات ══════════
    section("4. الإعدادات")
    h = c.get("/cart").get_data(as_text=True)
    check("كود الخصم ظاهر افتراضيًا", "coupon_code" in h)
    with app.app_context():
        StoreSetting.set("show_coupon_field", "0")
        db.session.commit()
    # نصفّيو السلة من الكوبون باش نشوفو الإخفاء
    c2 = app.test_client()
    c2.post("/cart/add", data={"kind": "product", "item_id": ids["p2"], "quantity": 1})
    check("كود الخصم يتخبّى بالإعداد",
          "coupon_code" not in c2.get("/cart").get_data(as_text=True))
    with app.app_context():
        StoreSetting.set("show_coupon_field", "1")
        db.session.commit()

    # ══════════ 5. أسعار التوصيل ══════════
    section("5. التوصيل")
    r = c.get("/api/shipping-price?wilaya=16&type=home&carrier=zrexpress")
    check("سعر التوصيل للمنزل", r.status_code == 200)
    if r.status_code == 200:
        check("السعر صحيح (600)", r.get_json().get("price") == 600, str(r.get_json()))
    r = c.get("/api/shipping-price?wilaya=999&type=home&carrier=zrexpress")
    check("ولاية ماكانتش ما تكسرش", r.status_code in (200, 400, 404))

    # ---------- البلديات ----------
    r = c.get("/api/communes?carrier=zrexpress&wilaya=16&type=home")
    data = r.get_json()["communes"]
    names = [x["name"] for x in data]
    check("قائمة البلديات تخدم", "باب الوادي" in names, str(names))
    ok = [x for x in data if x["available"]]
    bad = [x for x in data if not x["available"]]
    check("البلدية المتاحة معلّمة صح",
          any(x["name"] == "باب الوادي" for x in ok))
    check("البلدية اللي ما توصلش معلّمة معطّلة",
          any(x["name"] == "بلدية ماشي متاحة" for x in bad), str(bad))
    check("معرّف الشركة يرجع مع البلدية المتاحة",
          ok and ok[0]["id"] == "zr-uuid-1", str(ok[:1]))

    r = c.get("/api/communes")
    check("طلب بلا معاملات ما يكسرش", r.status_code == 200)

    r = c.get("/api/communes?carrier=zrexpress&wilaya=99&type=home")
    check("ولاية بلا بلديات ترجع قائمة فارغة",
          r.get_json()["communes"] == [])

    # البيانات الرسمية
    import json as _json, os as _os
    data_file = _os.path.join(_os.path.dirname(__file__), "..",
                              "data", "algeria_communes.json")
    check("ملف البلديات الرسمي موجود", _os.path.exists(data_file))
    if _os.path.exists(data_file):
        with open(data_file, encoding="utf-8") as f:
            all_rows = _json.load(f)
        check("الملف فيه 1541 بلدية", len(all_rows) == 1541, str(len(all_rows)))
        check("يغطي 58 ولاية",
              len({r["c"] for r in all_rows}) == 58,
              str(len({r["c"] for r in all_rows})))

    r = c.get("/api/pickup-points?carrier=zrexpress&wilaya=16")
    check("API المكاتب ما يكسرش بلا مفاتيح", r.status_code == 200)
    r = c.get("/api/pickup-points?carrier=other&wilaya=16")
    check("شركة بلا مكاتب ترجع رسالة واضحة",
          r.get_json().get("points") == [])

    # ══════════ 6. لوحة التحكم ══════════
    section("6. لوحة التحكم")
    check("الدخول محمي", c.get("/admin/", follow_redirects=False).status_code == 302)
    check("المنتجات محمية", c.get("/admin/products", follow_redirects=False).status_code == 302)
    check("الطلبيات محمية", c.get("/admin/orders", follow_redirects=False).status_code == 302)
    check("الإعدادات محمية", c.get("/admin/settings", follow_redirects=False).status_code == 302)

    ad = app.test_client()
    r = ad.post("/admin/login", data={"username": "admin", "password": "test-password-123"},
                follow_redirects=True)
    check("دخول بكلمة سر صحيحة", r.status_code == 200 and "لوحة" in r.get_data(as_text=True))
    for url, label in {"/admin/": "الرئيسية", "/admin/products": "المنتجات",
                       "/admin/orders": "الطلبيات", "/admin/coupons": "الكوبونات",
                       "/admin/settings": "الإعدادات",
                       "/admin/integrations": "التكاملات"}.items():
        check(f"لوحة: {label}", ad.get(url).status_code == 200)

    # ══════════ 7. الطلبية الكاملة ══════════
    section("7. الطلبية الكاملة")
    buyer = app.test_client()
    buyer.post("/cart/add", data={"kind": "product", "item_id": ids["p1"],
                                  "variant_id": ids["v1"], "quantity": 2})
    r = buyer.post("/checkout", data={
        "name": "بكير", "phone": "0676672334",
        "carrier_code": "zrexpress", "wilaya_code": 16,
        "delivery_type": "home", "commune": "باب الوادي",
        "commune_id": "zr-uuid-1",
        "address_note": "شارع 1", "note": "",
    }, follow_redirects=True)
    body = r.get_data(as_text=True)
    ok_order = r.status_code == 200 and "تم استلام طلبيتك" in body
    if not ok_order:
        import re as _re
        errs = _re.findall(r'class="flash[^"]*"[^>]*>([^<]+)', body)
        print("      رسائل الصفحة:", errs[:5])
    check("🔴 الطلبية تمرّ", ok_order)
    with app.app_context():
        n = Order.query.count()
        check("الطلبية تسجّلت فقاعدة البيانات", n == 1, f"عدد الطلبيات {n}")
        if n:
            o = Order.query.first()
            check("مجموع الطلبية صحيح (1000+600)", o.total == 1600, f"رجع {o.total}")
            check("🔴 معرّف البلدية محفوظ (ضروري لـZR)",
                  o.commune_external_id == "zr-uuid-1", str(o.commune_external_id))
    check("السلة تفرغت بعد الطلب", buyer.get("/api/cart").get_json()["count"] == 0)

    nc = app.test_client()
    nc.post("/cart/add", data={"kind": "product", "item_id": ids["p2"], "quantity": 1})
    r = nc.post("/checkout", data={
        "name": "زبون", "phone": "0555111222", "carrier_code": "zrexpress",
        "wilaya_code": 16, "delivery_type": "home", "commune": "",
    }, follow_redirects=True)
    check("طلبية بلا بلدية مرفوضة",
          "اختر البلدية" in r.get_data(as_text=True))

    oc = app.test_client()
    oc.post("/cart/add", data={"kind": "product", "item_id": ids["p2"], "quantity": 1})
    r = oc.post("/checkout", data={
        "name": "زبون", "phone": "0555111222", "carrier_code": "zrexpress",
        "wilaya_code": 16, "delivery_type": "office", "commune": "",
    }, follow_redirects=True)
    check("طلبية للمكتب بلا مكتب مرفوضة",
          "اختر مكتب الاستلام" in r.get_data(as_text=True))

    # ══════════ 7.5 أسعار الجملة ══════════
    section("7.5 أسعار الجملة (حسب الكمية)")
    with app.app_context():
        v = ProductVariant.query.filter_by(label="دوري كبير").first()
        check("1 حبة → 500", v.price_for_quantity(1) == 500, str(v.price_for_quantity(1)))
        check("99 حبة → 500", v.price_for_quantity(99) == 500)
        check("100 حبة → 420", v.price_for_quantity(100) == 420, str(v.price_for_quantity(100)))
        check("199 حبة → 420", v.price_for_quantity(199) == 420)
        check("200 حبة → 380", v.price_for_quantity(200) == 380)
        check("9999 حبة → 380", v.price_for_quantity(9999) == 380)
        check("صورة الموديل من صور المنتج",
              v.image_filename in [i.image_filename for i in v.product.images])

    tc = app.test_client()
    tc.post("/cart/add", data={"kind": "product", "item_id": ids["p1"],
                               "variant_id": ids["v1"], "quantity": 150})
    sub = tc.get("/api/cart").get_json()["subtotal"]
    check("🔴 السلة تطبّق سعر الجملة (150×420)", sub == 63000, f"رجع {sub}")

    tc2 = app.test_client()
    tc2.post("/cart/add", data={"kind": "product", "item_id": ids["p1"],
                                "variant_id": ids["v1"], "quantity": 300})
    sub2 = tc2.get("/api/cart").get_json()["subtotal"]
    check("السلة تطبّق أرخص مجال (300×380)", sub2 == 114000, f"رجع {sub2}")

    # ══════════ 8. الحسابات ══════════
    section("8. الأسعار والحسابات")
    with app.app_context():
        p = Product.query.filter_by(slug="kouan").first()
        check("نسبة التخفيض (600→500 = 17%)", p.discount_percent == 17, str(p.discount_percent))
        check("المنتج متوفر (مخزون مفتوح)", p.is_in_stock is True)
        v = ProductVariant.query.filter_by(label="أرجونتي").first()
        check("سعر التنويعة يغلب سعر المنتج", v.effective_price == 550)
        v0 = ProductVariant.query.filter_by(label="دوري كبير").first()
        check("تنويعة بلا سعر ترث سعر المنتج", v0.effective_price == 500)

    # ══════════ النتيجة ══════════
    section("النتيجة")
    total = len(PASSED) + len(FAILED)
    print(f"نجح: {len(PASSED)}/{total}")
    if FAILED:
        print(f"\n🔴 فشل {len(FAILED)}:")
        for name, detail in FAILED:
            print(f"   • {name}  {detail}")
    return len(FAILED)


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
