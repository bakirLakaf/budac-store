# -*- coding: utf-8 -*-
"""
اختبار عرض حقيقي: يشغّل التطبيق، يفتح كل الصفحات، ويفحص HTML المُخرَج.

⚠️ الفرق عن `test_full.py`: هذا ما يختبرش المنطق — يختبر أن القوالب
تتعرض بلا خطأ، وأن JSON-LD صالح فعلاً، وأن الترتيب الجديد ديال
العروض/الأسعار/الكمية موجود، وأن ماكانش سكربت برّا كتل Jinja.

التشغيل: python tests/test_render.py
"""
import os, sys, re, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "test-key-for-testing-only")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import create_app
from models import (db, Product, ProductVariant, Category, ProductImage,
                    VariantPriceTier, VariantPack, AdminUser, Carrier,
                    ShippingRate, Commune)

PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name + ("  " + detail if not ok else ""))


def section(t):
    print(f"\n{'='*60}\n{t}\n{'='*60}")


def build():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True

    with app.app_context():
        db.create_all()

        cat = Category(name="مقابض", slug="handles")
        db.session.add(cat)
        db.session.commit()

        # منتج بسعر 180 وسعر قبل التخفيض 200 (تخفيض سليم)
        p = Product(name="مقابض بواني", slug="bouani", price=180,
                    compare_at_price=200, category_id=cat.id, stock_quantity=0,
                    description="<p>وصف</p>")
        db.session.add(p)
        db.session.commit()

        db.session.add(ProductImage(product_id=p.id, image_filename="a.webp"))
        db.session.commit()

        # الموديل الافتراضي سعرو 120 — مختلف عن سعر المنتج (180)
        v_def = ProductVariant(product_id=p.id, label="شفاف/دوري", price=120,
                               stock_quantity=0, is_default=True,
                               image_filename="a.webp")
        v_two = ProductVariant(product_id=p.id, label="حطب/دوري", price=200,
                               stock_quantity=0)
        db.session.add_all([v_def, v_two])
        db.session.flush()

        db.session.add(VariantPriceTier(variant_id=v_def.id, min_quantity=100,
                                        max_quantity=None, unit_price=165))
        db.session.add(VariantPack(variant_id=v_def.id, quantity=12,
                                   label="رولو (12 متر)", price=700))

        admin = AdminUser(username="admin")
        admin.set_password("pass1234")
        db.session.add(admin)

        c = Carrier(name="ZR", code="zrexpress", is_active=True)
        db.session.add(c)
        db.session.commit()
        db.session.add(ShippingRate(carrier_id=c.id, wilaya_code="16",
                                    wilaya_name="الجزائر", home_price=500,
                                    office_price=300))
        db.session.commit()
        slug = p.slug

    return app, slug


def main():
    app, slug = build()
    client = app.test_client()

    # ───────── 1) كل الصفحات العامة تتعرض ─────────
    section("1. عرض الصفحات العامة")
    for path, label in [
        ("/", "الرئيسية"),
        (f"/product/{slug}", "صفحة المنتج"),
        ("/cart", "السلة"),
        ("/checkout", "الطلب"),
        ("/shipping-prices", "أسعار التوصيل"),
        ("/sitemap.xml", "خريطة الموقع"),
        ("/robots.txt", "robots"),
    ]:
        try:
            r = client.get(path)
            check(f"{label} ({r.status_code})", r.status_code in (200, 302))
        except Exception as e:
            check(label, False, repr(e)[:120])

    # ───────── 2) JSON-LD صالح ─────────
    section("2. البيانات المنظمة (JSON-LD)")
    html = client.get(f"/product/{slug}").get_data(as_text=True)
    blocks = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.S)
    check("فيه كتلة JSON-LD واحدة على الأقل", len(blocks) >= 1)
    for i, b in enumerate(blocks):
        try:
            data = json.loads(b)
            check(f"JSON-LD #{i+1} صالح ({data.get('@type','?')})", True)
        except Exception as e:
            check(f"JSON-LD #{i+1} صالح", False, str(e)[:100])

    # ───────── 3) ترتيب العروض / الأسعار / الكمية ─────────
    section("3. ترتيب صندوق العروض فوق محدّد الكمية")
    i_packs = html.find('id="packsBox"')
    i_tiers = html.find('id="tiersBox"')
    i_qty = html.find('class="qty-picker"')
    i_total = html.find('id="liveTotal"')
    i_btn = html.find('add-to-cart-form')

    check("صندوق العروض موجود", i_packs > 0)
    check("صندوق أسعار الكمية موجود", i_tiers > 0)
    check("العروض قبل محدّد الكمية", 0 < i_packs < i_qty, f"{i_packs} vs {i_qty}")
    check("أسعار الكمية قبل محدّد الكمية", 0 < i_tiers < i_qty)
    check("العروض داخل فورم الشراء", 0 < i_btn < i_packs)
    check("محدّد الكمية قبل الإجمالي الحيّ", 0 < i_qty < i_total)

    # ───────── 4) سعر الموديل الافتراضي ─────────
    section("4. السعر الأول = سعر الموديل الافتراضي")
    check("دالة applyVariantPrice موجودة", "function applyVariantPrice" in html)
    check("تتنادى عند التحميل", "applyVariantPrice(initialChip)" in html)
    check("الموديل الافتراضي فيه is-active",
          re.search(r'variant-chip[^"]*is-active', html) is not None)

    m = re.search(r'class="variant-chip[^"]*is-active[^"]*"(.*?)>', html, re.S)
    check("الموديل الافتراضي عندو data-price-display",
          bool(m and "data-price-display" in m.group(1)))
    check("سعر الموديل (120) موجود فالصفحة", "120" in html)

    # ───────── 5) الأمان الأساسي فالصفحة ─────────
    section("5. رموز CSRF والحقول المخفية")
    check("csrf_token فالفورم", 'name="csrf_token"' in html)
    check("variant_id مخفي", 'id="selectedVariantId"' in html)
    check("pack_id مخفي", 'id="selectedPackId"' in html)

    # ───────── 6) لوحة التحكم تتعرض ─────────
    section("6. عرض صفحات لوحة التحكم")
    client.post("/admin/login", data={"username": "admin", "password": "pass1234"})
    with app.app_context():
        pid = Product.query.first().id
    for path, label in [
        ("/admin/", "الرئيسية"),
        ("/admin/products", "المنتجات"),
        (f"/admin/products/{pid}/edit", "تعديل منتج"),
        ("/admin/orders", "الطلبيات"),
        ("/admin/coupons", "الكوبونات"),
        ("/admin/settings", "الإعدادات"),
        ("/admin/shipping", "أسعار التوصيل"),
        ("/admin/reviews", "التقييمات"),
    ]:
        try:
            r = client.get(path)
            check(f"لوحة: {label} ({r.status_code})", r.status_code in (200, 302))
        except Exception as e:
            check(f"لوحة: {label}", False, repr(e)[:120])

    # ───────── 7) getCsrf معرّفة مرة وحدة وعالميًا ─────────
    section("7. getCsrf عالمية (إصلاح حفظ سعر الموديل)")
    admin_html = client.get(f"/admin/products/{pid}/edit").get_data(as_text=True)
    check("window.getCsrf معرّفة", "window.getCsrf" in admin_html)
    check("ماكانش تعريف محلي مكرر",
          admin_html.count("function getCsrf") <= 1,
          f"عدد التعريفات: {admin_html.count('function getCsrf')}")
    check("زر حفظ سعر الموديل موجود", "vprice-save" in admin_html)

    # ───────── 7.5) شريط الطلب الثابت ─────────
    section("7.5 شريط الطلب الثابت (موبايل)")
    check("يستعمل liveSum الموجود فعلاً", "getElementById('liveSum')" in html)
    check("ما يستعملش livePrice المفقود", "'livePrice'" not in html)
    check("ما يستعملش .price-current المفقود",
          "querySelector('.price-current')" not in html)

    # ───────── 7.6) كل getElementById لها عنصر موجود ─────────
    section("7.6 كل getElementById لها عنصر فالصفحة")
    import glob as _g
    broken = []
    for tpl in _g.glob("templates/**/*.html", recursive=True):
        src = open(tpl, encoding="utf-8").read()
        ids = set(re.findall(r'\bid="([\w-]+)"', src))
        used = set(re.findall(r'getElementById\(\s*[\'"]([\w-]+)[\'"]', src))
        for miss in sorted(used - ids):
            broken.append(f"{tpl} → {miss}")
    check("ماكانش معرّف مفقود", not broken, str(broken[:5]))

    # ───────── 7.7) متغيرات CSS كلها معرّفة ─────────
    section("7.7 متغيرات CSS")
    css_all = "".join(open(f, encoding="utf-8").read()
                      for f in _g.glob("static/css/*.css"))
    defined = set(re.findall(r"(--[\w-]+)\s*:", css_all))
    for f in _g.glob("static/css/*.css"):
        used_v = set(re.findall(r"var\(\s*(--[\w-]+)",
                                open(f, encoding="utf-8").read()))
        miss_v = sorted(used_v - defined)
        check(f"{os.path.basename(f)}: ماكانش متغير مجهول", not miss_v, str(miss_v))

    # ───────── 8) ماكانش محتوى برّا كتل Jinja ─────────
    section("8. سلامة القوالب")
    import glob
    outside = []
    for p in glob.glob("templates/**/*.html", recursive=True):
        s = open(p, encoding="utf-8").read()
        if "{% extends" not in s:
            continue
        # واش فيه <script> ولا <div> بعد آخر endblock؟
        last = s.rfind("{% endblock %}")
        if last == -1:
            continue
        tail = s[last + len("{% endblock %}"):]
        if re.search(r'<(script|div|section|style)\b', tail):
            outside.append(p)
    check("ماكانش محتوى بعد آخر endblock", not outside, str(outside))

    # ───────── النتيجة ─────────
    section("النتيجة")
    print(f"نجح: {len(PASSED)}/{len(PASSED)+len(FAILED)}")
    if FAILED:
        print("\n🔴 فشل:")
        for f in FAILED:
            print("  •", f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
