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

# ⚠️ مهم: الاختبار يرفع صور حقيقية عبر التطبيق. بلا هاد السطر، الصور
# تنكتب فـ`static/img/products/` — مجلد صور المتجر الحقيقي — وتبقى
# فيه بعد ما يخلص الاختبار وتدخل معاك للمستودع.
# نوجّهوها لمجلد مؤقت يتمسح لوحدو فالآخر.
import tempfile, atexit, shutil

_TMP_UPLOADS = tempfile.mkdtemp(prefix="budac-test-uploads-")
os.environ["UPLOAD_ROOT"] = _TMP_UPLOADS
atexit.register(shutil.rmtree, _TMP_UPLOADS, True)

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

        # 3 صور بترتيب: b (رئيسية) ← a ← c
        db.session.add_all([
            ProductImage(product_id=p.id, image_filename="b.webp", sort_order=0),
            ProductImage(product_id=p.id, image_filename="a.webp", sort_order=1),
            ProductImage(product_id=p.id, image_filename="c.webp", sort_order=2),
        ])
        p.image_filename = "b.webp"   # الصورة الرئيسية ★
        db.session.commit()

        # الموديل الافتراضي سعرو 120 — مختلف عن سعر المنتج (180)
        # ⚠️ نزيدوهم **بترتيب مقلوب** عمدًا، وعلامة is_default على موديل
        # ماشي هو صاحب الصورة الرئيسية — باش نتأكد أن الصور هي اللي تغلب.
        v_c = ProductVariant(product_id=p.id, label="نوار/دوري", price=300,
                             stock_quantity=0, image_filename="c.webp")
        v_a = ProductVariant(product_id=p.id, label="شفاف/دوري", price=120,
                             stock_quantity=0, is_default=True,
                             image_filename="a.webp")
        v_b = ProductVariant(product_id=p.id, label="دوري/دوري", price=250,
                             stock_quantity=0, image_filename="b.webp")
        v_none = ProductVariant(product_id=p.id, label="بلا صورة", price=999,
                                stock_quantity=0)
        db.session.add_all([v_c, v_a, v_b, v_none])
        db.session.flush()
        v_def = v_b   # المتوقّع: صاحب الصورة الرئيسية

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

    # ───────── 4.5) السعر المعروض = سعر الموديل الافتراضي ─────────
    section("4.5 سعر المنتج مشتق من الموديل الافتراضي")
    from models import Product as _P
    with app.app_context():
        prod = _P.query.filter_by(slug=slug).first()
        check("display_price = سعر موديل الصورة الرئيسية (250)",
              prod.display_price == 250, f"جا {prod.display_price}")
        check("price_display يستعمل السعر المشتق",
              "250" in prod.price_display, prod.price_display)
        check("العمود price مازال 180 (احتياطي)", prod.price == 180)
        # 200 < 250 → ماكانش تخفيض
        check("has_discount يقارن بالسعر المشتق", prod.has_discount is False)

    check("basePrice للإجمالي الحيّ = 250", "var basePrice = 250;" in html)
    check("JSON-LD يستعمل السعر المشتق", '"price": "250"' in html)

    section("4.7 ترتيب الموديلات يتبع ترتيب الصور")
    with app.app_context():
        prod = _P.query.filter_by(slug=slug).first()
        labels = [v.label for v in prod.ordered_variants]
        check("الترتيب: b ← a ← c ← بلا صورة",
              labels == ["دوري/دوري", "شفاف/دوري", "نوار/دوري", "بلا صورة"],
              str(labels))
        check("الافتراضي = موديل الصورة الرئيسية ★",
              prod.default_variant.label == "دوري/دوري",
              prod.default_variant.label)
        check("علامة is_default القديمة ما تغلبش الصورة",
              prod.default_variant.label != "شفاف/دوري")

    # الترتيب فالصفحة نفسها
    order_html = re.findall(r'class="variant-chip[^"]*"[^>]*>\s*([^<]+?)\s*<',
                            html)
    check("ترتيب الأزرار فالصفحة صحيح",
          [x.strip() for x in order_html][:4]
          == ["دوري/دوري", "شفاف/دوري", "نوار/دوري", "بلا صورة"],
          str(order_html[:4]))

    active = re.search(
        r'class="variant-chip[^"]*is-active[^"]*"[^>]*>\s*([^<]+?)\s*<', html)
    check("الزر المختار = موديل الصورة الرئيسية",
          bool(active) and active.group(1).strip() == "دوري/دوري",
          active.group(1) if active else "ماكانش")

    section("4.6 الصنف الصحيح للموديل المختار")
    check("ما يستعملش is-selected المفقود",
          ".is-selected" not in html)
    check("يستعمل is-active", "[data-tiers].is-active" in html)

    section("4.8 ترتيب الصور ثابت (sort_order + id)")
    with app.app_context():
        prod = _P.query.filter_by(slug=slug).first()
        # كل الصور بـsort_order مختلف — نتأكد أن الترتيب يحترمو
        names = [i.image_filename for i in prod.images]
        check("ترتيب الصور: b ← a ← c",
              names == ["b.webp", "a.webp", "c.webp"], str(names))

        # حالة البيانات القديمة: كل sort_order = 0
        for img in prod.images:
            img.sort_order = 0
        db.session.commit()
        db.session.expire(prod)
        again = [i.image_filename for i in prod.images]
        check("بـsort_order كلها صفر، الترتيب يبقى ثابت (حسب id)",
              again == ["b.webp", "a.webp", "c.webp"], str(again))

        # نرجّعو الوضع
        for pos, img in enumerate(prod.images):
            img.sort_order = pos
        db.session.commit()

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
    # ⚠️ القالب الوارث يقدر ينادي عنصرًا معرّفًا فالقالب الأساسي
    # (مثلاً `adminZoomer` فـbase_admin.html). فنجمعو معرّفات القالب
    # مع اللي يمدّد منو، وإلا الفحص يعطي إنذارات كاذبة.
    all_ids = {}
    for tpl in _g.glob("templates/**/*.html", recursive=True):
        src = open(tpl, encoding="utf-8").read()
        all_ids[os.path.basename(tpl)] = set(re.findall(r'\bid="([\w-]+)"', src))

    broken = []
    for tpl in _g.glob("templates/**/*.html", recursive=True):
        src = open(tpl, encoding="utf-8").read()
        ids = set(re.findall(r'\bid="([\w-]+)"', src))

        # نزيدو معرّفات القوالب الأب (سلسلة extends)
        parent = re.search(r'\{%\s*extends\s*["\']([^"\']+)["\']', src)
        seen = set()
        while parent:
            name = os.path.basename(parent.group(1))
            if name in seen or name not in all_ids:
                break
            seen.add(name)
            ids |= all_ids[name]

            hits = [f for f in _g.glob("templates/**/*.html", recursive=True)
                    if os.path.basename(f) == name]
            if not hits:
                break
            parent = re.search(r'\{%\s*extends\s*["\']([^"\']+)["\']',
                               open(hits[0], encoding="utf-8").read())

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

    # ───────── 7.8) حذف موديل ما يمحيش الصورة ─────────
    section("7.8 حذف الموديل يقطع الربط برك")
    from models import ProductVariant as _V
    with app.app_context():
        before = len(_P.query.filter_by(slug=slug).first().images)
        vid = _V.query.filter_by(label="نوار/دوري").first().id

    r = client.post(f"/admin/variants/{vid}/delete")
    check(f"الحذف نجح ({r.status_code})", r.status_code in (200, 302))

    with app.app_context():
        prod2 = _P.query.filter_by(slug=slug).first()
        check("الموديل اتمسح",
              db.session.get(_V, vid) is None)
        check("عدد الصور ما تنقصش", len(prod2.images) == before,
              f"{len(prod2.images)} بدل {before}")
        check("صورة الموديل المحذوف باقية",
              "c.webp" in [i.image_filename for i in prod2.images])
        check("الصورة الرئيسية ما تمسّتش", prod2.image_filename == "b.webp")

    # ───────── 7.9) تعديل اسم الموديل ─────────
    section("7.9 تعديل اسم الموديل فمكانو")
    with app.app_context():
        vid2 = _V.query.filter_by(label="شفاف/دوري").first().id

    r = client.post(f"/admin/variants/{vid2}/label",
                    data={"label": "شفاف/برونز"},
                    headers={"X-Requested-With": "fetch"})
    check(f"التعديل نجح ({r.status_code})", r.status_code == 200)
    with app.app_context():
        check("الاسم اتبدّل", db.session.get(_V, vid2).label == "شفاف/برونز",
              db.session.get(_V, vid2).label)

    r = client.post(f"/admin/variants/{vid2}/label", data={"label": "   "},
                    headers={"X-Requested-With": "fetch"})
    check("الاسم الفارغ مرفوض", r.status_code == 400)

    r = client.post(f"/admin/variants/{vid2}/label",
                    data={"label": "دوري/دوري"},
                    headers={"X-Requested-With": "fetch"})
    check("الاسم المكرر مرفوض", r.status_code == 400)

    with app.app_context():
        check("بعد الرفض، الاسم ما تبدّلش",
              db.session.get(_V, vid2).label == "شفاف/برونز")

    admin_html2 = client.get(f"/admin/products/{pid}/edit").get_data(as_text=True)
    check("حقل الاسم موجود فاللوحة", "vlabel-input" in admin_html2)
    check("زر حفظ الاسم موجود", "vlabel-save" in admin_html2)

    # ───────── 7.10) رفع صور حقيقية ─────────
    section("7.10 رفع الصور (ملفات حقيقية)")
    import io
    from PIL import Image as _Img

    def fake_image(px=900, name="t.jpg"):
        """يولّد JPEG حقيقي فالذاكرة."""
        buf = io.BytesIO()
        _Img.new("RGB", (px, px), (200, 120, 60)).save(buf, "JPEG", quality=92)
        buf.seek(0)
        return (buf, name)

    with app.app_context():
        before_n = len(_P.query.filter_by(slug=slug).first().images)

    up = f"/admin/products/{pid}/images/upload"

    r = client.post(up, data={"image": fake_image(800, "one.jpg")},
                    content_type="multipart/form-data",
                    headers={"X-Requested-With": "fetch"})
    check(f"رفع صورة وحدة ({r.status_code})", r.status_code == 200, r.get_data(as_text=True)[:120])
    if r.status_code == 200:
        j = r.get_json()
        check("الرد فيه رابط الصورة", bool(j.get("url")))
        check("الرد فيه id", isinstance(j.get("id"), int))

    # ⚠️ الحالة اللي كانت تكسر: عدة صور — دابا كل وحدة طلب مستقل
    ok_n = 0
    for i in range(4):
        rr = client.post(up, data={"image": fake_image(1400, f"m{i}.jpg")},
                         content_type="multipart/form-data",
                         headers={"X-Requested-With": "fetch"})
        if rr.status_code == 200:
            ok_n += 1
    check("4 صور كبار وحدة بوحدة", ok_n == 4, f"نجحو {ok_n}")

    with app.app_context():
        prod3 = _P.query.filter_by(slug=slug).first()
        check("كل الصور اتزادو فقاعدة البيانات",
              len(prod3.images) == before_n + 5,
              f"{len(prod3.images)} بدل {before_n + 5}")
        orders = [i.sort_order for i in prod3.images]
        check("sort_order ما فيهش تكرار", len(set(orders)) == len(orders), str(orders))
        check("الصورة الرئيسية ما تبدّلتش", prod3.image_filename == "b.webp")

    # ملف ماشي صورة → لازم يترفض برسالة، ماشي 500
    r = client.post(up, data={"image": (io.BytesIO(b"not an image"), "x.txt")},
                    content_type="multipart/form-data",
                    headers={"X-Requested-With": "fetch"})
    check("الملف الغالط مرفوض بـ400", r.status_code == 400, str(r.status_code))
    check("الرفض فيه رسالة عربية",
          bool(r.get_json() and r.get_json().get("message")))

    # بلا ملف
    r = client.post(up, data={}, content_type="multipart/form-data",
                    headers={"X-Requested-With": "fetch"})
    check("بلا ملف → 400", r.status_code == 400)

    # 🔴 خلل عدّى للإنتاج: `secure_filename` تحذف الحروف العربية حتى
    # النقطة، فالامتداد يضيع وأي صورة باسم عربي تترفض.
    section("7.10.d أسماء ملفات عربية")
    for arabic in ["لاد كادر متعدد الالوان.png",
                   "صورة المنتج.jpg",
                   "أرجل حديد ٢٠٢٦.webp",
                   "منتج - نسخة (2).jpeg"]:
        rr = client.post(up, data={"image": fake_image(600, arabic)},
                         content_type="multipart/form-data",
                         headers={"X-Requested-With": "fetch"})
        check(f"«{arabic}» مقبولة", rr.status_code == 200,
              (rr.get_json() or {}).get("message", str(rr.status_code)))

    # الامتداد الممنوع يبقى ممنوع حتى باسم عربي
    rr = client.post(up, data={"image": (io.BytesIO(b"MZ"), "برنامج خبيث.exe")},
                     content_type="multipart/form-data",
                     headers={"X-Requested-With": "fetch"})
    check("«.exe» باسم عربي مرفوضة", rr.status_code == 400)

    rr = client.post(up, data={"image": (io.BytesIO(b"x"), "سكربت.php")},
                     content_type="multipart/form-data",
                     headers={"X-Requested-With": "fetch"})
    check("«.php» باسم عربي مرفوضة", rr.status_code == 400)

    # الاسم المحفوظ ما يحتويش حروفًا عربية ولا مسارات
    with app.app_context():
        saved = [i.image_filename for i in
                 _P.query.filter_by(slug=slug).first().images]
    check("الأسماء المحفوظة ASCII بلا مسارات",
          all(n.isascii() and "/" not in n and ".." not in n for n in saved),
          str([n for n in saved if not n.isascii()][:2]))

    # ⚠️ هذا هو الاختبار اللي كان ناقص وخلّى العطل يوصل للإنتاج:
    # نتأكدو أن طلب أكبر من الحد يترفض بـ413 ماشي بانقطاع، وأن كل
    # صورة وحدها تبقى تحت الحد.
    section("7.10.b حد حجم الطلب")
    limit = app.config["MAX_CONTENT_LENGTH"]
    check("MAX_CONTENT_LENGTH معرّف", bool(limit))

    big = io.BytesIO(os.urandom(limit + 200_000))   # أكبر من الحد
    r = client.post(f"/admin/products/{pid}/edit",
                    data={"name": "x", "images": (big, "big.jpg")},
                    content_type="multipart/form-data")
    check("الطلب اللي يفوت الحد يرجّع 413 ماشي انقطاع",
          r.status_code == 413, str(r.status_code))

    # صور الهاتف تكون 3-5 ميغا. وحدة بوحدة كل طلب يبقى تحت الحد،
    # لكن 5 مع بعض يفوتوه بكثير — علاش قسّمنا الرفع.
    phone_photo = 4_500_000
    check("صورة هاتف وحدة تبقى تحت الحد", phone_photo < limit,
          f"{phone_photo} vs {limit}")
    check("5 صور هاتف مع بعض يفوتو الحد", phone_photo * 5 > limit)

    section("7.10.c رقم الإصدار يبان فاللوحة")
    from VERSION import VERSION as _V_
    dash = client.get("/admin/").get_data(as_text=True)
    check(f"رقم الإصدار ({_V_}) يبان فاللوحة", _V_ in dash)

    section("7.11 واجهة الرفع")
    up_html = client.get(f"/admin/products/{pid}/edit").get_data(as_text=True)
    check("شريط التقدّم موجود", 'id="uploadProgress"' in up_html)
    check("منتقي الملفات موجود", 'id="imagePicker"' in up_html)
    check("الفورم عندو id", 'id="productForm"' in up_html)
    check("يستعمل XMLHttpRequest (تقدّم حقيقي)",
          "new XMLHttpRequest()" in up_html)
    check("يقفل الحفظ أثناء الرفع", "b.disabled = on" in up_html)
    check("ينبّه قبل مغادرة الصفحة", "beforeunload" in up_html)

    # 🔴 الخلل اللي ضيّع 3 جولات: كود الرفع كان محشورًا داخل IIFE ديال
    # «أسعار الكمية»، وذاك يخرج بـ`return` مبكّر إذا ماكانش موديلات —
    # فالرافع ما يشتغلش أبدًا. نتأكدو أنه فكتلة <script> وحدو.
    section("7.12 كود الرفع فكتلة مستقلة")
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", up_html, re.S)
    owner = [b for b in blocks if "imagePicker" in b]
    check("كود الرفع فكتلة وحدة", len(owner) == 1, f"{len(owner)} كتلة")
    if owner:
        b = owner[0]
        check("الكتلة ما فيهاش كود أسعار الكمية",
              "makeRow" not in b and "unit_price" not in b)
        check("الكتلة ما فيهاش كود الموديلات",
              "vlabel" not in b and "vprice" not in b)
        # عدد الأقواس متوازن داخل الكتلة = ماشي مقطوعة
        check("الكتلة مقفولة مليح", b.count("{") == b.count("}"),
              f"{b.count('{')} vs {b.count('}')}")

    # منتج بلا موديلات: الرافع لازم يخدم كذلك
    with app.app_context():
        bare = _P(name="بلا موديلات", slug="bare", price=50,
                  category_id=_P.query.first().category_id, stock_quantity=0)
        db.session.add(bare)
        db.session.commit()
        bare_id = bare.id

    bare_html = client.get(f"/admin/products/{bare_id}/edit").get_data(as_text=True)
    check("منتج بلا موديلات: الرافع موجود", 'id="imagePicker"' in bare_html)
    check("منتج بلا موديلات: شريط التقدّم موجود",
          'id="uploadProgress"' in bare_html)

    r = client.post(f"/admin/products/{bare_id}/images/upload",
                    data={"image": fake_image(700, "bare.jpg")},
                    content_type="multipart/form-data",
                    headers={"X-Requested-With": "fetch"})
    check("منتج بلا موديلات: الرفع يخدم", r.status_code == 200, str(r.status_code))

    # ───────── 7.13) تعديل التصنيف ─────────
    section("7.13 تعديل التصنيف من اللوحة")
    from models import Category as _C
    with app.app_context():
        cid = _C.query.first().id
        old_slug = db.session.get(_C, cid).slug

    cats_html = client.get("/admin/categories").get_data(as_text=True)
    check("حقل الاسم موجود فالجدول", "cat-name-input" in cats_html)
    check("زر الحفظ موجود", f'id="cat-{cid}"' in cats_html)

    r = client.post(f"/admin/categories/{cid}/edit",
                    data={"name": "أغطية وقطع تزيين", "is_active": "1"})
    check(f"التعديل نجح ({r.status_code})", r.status_code in (200, 302))
    with app.app_context():
        c2 = db.session.get(_C, cid)
        check("الاسم اتبدّل", c2.name == "أغطية وقطع تزيين", c2.name)
        check("⚠️ الـslug ما تبدّلش (السيو محفوظ)", c2.slug == old_slug,
              f"{c2.slug} بدل {old_slug}")

    r = client.post(f"/admin/categories/{cid}/edit", data={"name": "  "})
    with app.app_context():
        check("الاسم الفارغ مرفوض",
              db.session.get(_C, cid).name == "أغطية وقطع تزيين")

    # اسم مكرر
    with app.app_context():
        db.session.add(_C(name="تصنيف آخر", slug="other-cat"))
        db.session.commit()
        other_id = _C.query.filter_by(slug="other-cat").first().id

    client.post(f"/admin/categories/{other_id}/edit",
                data={"name": "أغطية وقطع تزيين"})
    with app.app_context():
        check("الاسم المكرر مرفوض",
              db.session.get(_C, other_id).name == "تصنيف آخر")

    # ───────── 7.14) صور توضيحية ─────────
    section("7.14 صور أخرى (توضيحية)")
    from models import ProductImage as _PI

    with app.app_context():
        main_before = len(_P.query.filter_by(slug=slug).first().main_images)

    # نرفعو صورة توضيحية
    r = client.post(up, data={"image": fake_image(800, "howto.jpg"),
                              "kind": "extra"},
                    content_type="multipart/form-data",
                    headers={"X-Requested-With": "fetch"})
    check(f"رفع صورة توضيحية ({r.status_code})", r.status_code == 200)
    check("الرد يقول أنها توضيحية",
          bool(r.get_json() and r.get_json().get("is_extra")))
    extra_id = r.get_json().get("id") if r.status_code == 200 else None

    with app.app_context():
        prod4 = _P.query.filter_by(slug=slug).first()
        check("صور المنتج ما زادتش", len(prod4.main_images) == main_before,
              f"{len(prod4.main_images)} بدل {main_before}")
        check("الصورة التوضيحية اتزادت", len(prod4.extra_images) == 1)
        check("⚠️ ما تدخلش فترتيب الموديلات",
              "howto" not in " ".join(prod4._image_rank.keys()))
        check("ما تبانش فمعرض المتجر",
              all("howto" not in g for g in prod4.gallery_images))

    # التوضيحية ما تقدرش تكون رئيسية
    with app.app_context():
        old_main = _P.query.filter_by(slug=slug).first().image_filename
    r = client.post(f"/admin/products/{pid}/images/{extra_id}/make-main")
    with app.app_context():
        check("التوضيحية مرفوضة كصورة رئيسية",
              _P.query.filter_by(slug=slug).first().image_filename == old_main)

    # الموديل ما يقدرش يترتبط بصورة توضيحية
    with app.app_context():
        vid3 = _V.query.filter_by(product_id=pid).first().id
        extra_name = _P.query.filter_by(slug=slug).first().extra_images[0].image_filename
    r = client.post(f"/admin/variants/{vid3}/image",
                    data={"image_filename": extra_name},
                    headers={"X-Requested-With": "fetch"})
    check("الموديل ما يترتبطش بصورة توضيحية", r.status_code == 400,
          str(r.status_code))

    # الوصف
    r = client.post(f"/admin/products/{pid}/images/{extra_id}/caption",
                    data={"caption": "كيفاش تركّبها على الصينية"},
                    headers={"X-Requested-With": "fetch"})
    check(f"حفظ الوصف ({r.status_code})", r.status_code == 200)
    with app.app_context():
        check("الوصف انحفظ",
              db.session.get(_PI, extra_id).caption == "كيفاش تركّبها على الصينية")

    r = client.post(f"/admin/products/{pid}/images/{extra_id}/caption",
                    data={"caption": "x" * 250},
                    headers={"X-Requested-With": "fetch"})
    check("الوصف الطويل مرفوض", r.status_code == 400)

    # صفحة المنتج
    shop_html = client.get(f"/product/{slug}").get_data(as_text=True)
    check("قسم الصور التوضيحية يبان", "extra-shots" in shop_html)
    check("الوصف يبان تحت الصورة",
          "كيفاش تركّبها على الصينية" in shop_html)

    # ⚠️ القسم لازم يجي **بعد** الشراء، ماشي قبل
    i_cart = shop_html.find("add-to-cart-form")
    i_extra = shop_html.find("extra-shots")
    check("القسم تحت زر الشراء", 0 < i_cart < i_extra,
          f"{i_cart} vs {i_extra}")

    # اللوحة
    adm = client.get(f"/admin/products/{pid}/edit").get_data(as_text=True)
    check("رافع الصور التوضيحية موجود", 'id="extraPicker"' in adm)
    check("حقل الوصف موجود", "extra-caption" in adm)

    # منتج بلا صور توضيحية: القسم ما يبانش
    bare_shop = client.get(f"/product/bare").get_data(as_text=True) \
        if client.get("/product/bare").status_code == 200 else ""
    if bare_shop:
        check("منتج بلا توضيحية: القسم مخفي", "extra-shots" not in bare_shop)

    # ───────── 7.15) أرقام القائمة الجانبية ─────────
    section("7.15 أرقام القائمة الجانبية")
    from models import Order as _O, Category as _Cat

    with app.app_context():
        n_products = _P.query.count()
        n_cats = _Cat.query.count()

    nav = client.get("/admin/products").get_data(as_text=True)
    check("رقم المنتجات يبان",
          f'<span class="nav-count">{n_products}</span>' in nav,
          f"متوقّع {n_products}")
    check("رقم التصنيفات يبان",
          f'<span class="nav-count">{n_cats}</span>' in nav,
          f"متوقّع {n_cats}")

    # الطلبيات: بلا طلبيات → ماكانش رقم
    with app.app_context():
        check("ماكانش طلبيات بعد", _O.query.count() == 0)
    check("بلا طلبيات: ماكانش رقم للطلبيات",
          nav.count("nav-count") == 2, f"لقينا {nav.count('nav-count')}")

    # نزيدو طلبية جديدة → لازم شارة ذهبية (تنبيه) ماشي رقم هادي
    with app.app_context():
        db.session.add(_O(
            order_number="BD-TEST01", customer_name="اختبار",
            customer_phone="0550000000", wilaya_code="16",
            wilaya_name="الجزائر", commune="باب الوادي",
            delivery_type="home", shipping_price=500,
            subtotal=2000, total=2500, status=_O.STATUS_PENDING,
        ))
        db.session.commit()

    nav2 = client.get("/admin/orders").get_data(as_text=True)
    check("الطلبية الجديدة تبان كشارة تنبيه (ذهبية)",
          '<span class="nav-badge" title="طلبيات جديدة ما تواصلتش معاهم">1</span>' in nav2)

    # نأكّدو الطلبية → التنبيه يختفي ويولّي جرد
    with app.app_context():
        o = _O.query.filter_by(order_number="BD-TEST01").first()
        o.status = _O.STATUS_CONFIRMED
        db.session.commit()

    nav3 = client.get("/admin/orders").get_data(as_text=True)
    check("بعد التأكيد: التنبيه يختفي",
          'title="طلبيات جديدة ما تواصلتش معاهم"' not in nav3)
    check("ويولّي رقم جرد هادي",
          nav3.count('<span class="nav-count">') == 3,
          f"لقينا {nav3.count(chr(60) + 'span class=' + chr(34) + 'nav-count' + chr(34) + chr(62))}")

    # ⚠️ الرقم ما يبانش لزائر ماشي أدمن
    anon = app.test_client()
    pub = anon.get("/").get_data(as_text=True)
    check("صفحات المتجر ما فيهاش أرقام اللوحة", "nav-count" not in pub)

    # ───────── 7.16) معاينة الصور فكل اللوحة ─────────
    section("7.16 تكبير الصور فاللوحة")

    # ⚠️ نعيدو استعمال الطلبية اللي نشأت فوق بدل ما نزيدو وحدة —
    # `order_number` فريد، فإدخال تاني بنفس الرقم يطيح.
    from models import Order as _O2, OrderItem as _OI
    with app.app_context():
        prod5 = _P.query.filter_by(slug=slug).first()
        order = _O2.query.first()
        oid = order.id

        # نزيدو سطرًا فيه صورة باش المعاينة يكون عندها واش تكبّر
        if not any(i.image_filename for i in order.items):
            db.session.add(_OI(
                order_id=oid, product_id=prod5.id,
                item_name=prod5.name, unit_price=250, quantity=2,
                image_filename=prod5.image_filename,
            ))
            db.session.commit()

    pages = [
        (f"/admin/orders/{oid}", "تفاصيل الطلبية"),
        ("/admin/products", "المنتجات"),
        ("/admin/categories", "التصنيفات"),
        ("/admin/offers", "العروض"),
    ]
    for path, label in pages:
        r = client.get(path)
        if r.status_code != 200:
            check(f"{label} تتعرض", False, str(r.status_code))
            continue
        page = r.get_data(as_text=True)
        check(f"{label}: المعاينة موجودة", 'id="adminZoomer"' in page)

    order_page = client.get(f"/admin/orders/{oid}").get_data(as_text=True)
    check("صورة الطلبية عندها data-full", 'data-full=' in order_page)
    # ⚠️ data-full لازم يشير للصورة الأصلية ماشي للمصغّرة
    m = re.search(r'data-full="([^"]+)"', order_page)
    check("data-full يشير للأصل ماشي المصغّرة",
          bool(m) and "-thumb" not in m.group(1),
          m.group(1) if m else "ماكانش")

    # ما يتكررش فأكثر من مكان
    check("عنصر معاينة واحد فالصفحة",
          order_page.count('id="adminZoomer"') == 1)

    # ───────── 7.17) أزرار الواتساب ─────────
    section("7.17 التواصل مع الزبون عبر الواتساب")
    from models import Order as _O3

    with app.app_context():
        o = _O3.query.first()
        o.customer_phone = "0551486151"
        o.customer_name = "علي"
        o.tracking_number = "ZR99887"
        o.carrier_name = "ZR Express"
        db.session.commit()
        oid2 = o.id

        # صيغ الهاتف
        for raw, want in [
            ("0551486151", "213551486151"),
            ("213551486151", "213551486151"),
            ("00213551486151", "213551486151"),
            ("+213 551 48 61 51", "213551486151"),
            ("0551-48-61-51", "213551486151"),
            ("", ""),
            (None, ""),
        ]:
            o.customer_phone = raw
            check(f"«{raw}» → {o.whatsapp_phone}", o.whatsapp_phone == want,
                  f"المتوقّع {want}")

        o.customer_phone = "0551486151"
        db.session.commit()

        # كل قالب رسالة يبني نصًا صالحًا
        for kind in ["confirm", "shipped", "arrived", "modify",
                     "missing", "noreply", "thanks", "unknown-kind"]:
            msg = o.whatsapp_message(kind, "Budac")
            check(f"رسالة «{kind}» مبنية", isinstance(msg, str) and len(msg) > 20,
                  repr(msg)[:60])

        # ⚠️ الرسالة لازم تكون فيها معلومات الطلبية الحقيقية
        confirm = o.whatsapp_message("confirm", "Budac")
        check("التأكيد فيه رقم الطلبية", o.order_number in confirm)
        check("التأكيد فيه المجموع", o.total_display in confirm)
        check("التأكيد فيه اسم الزبون", "علي" in confirm)

        shipped = o.whatsapp_message("shipped", "Budac")
        check("الشحن فيه رقم التتبّع", "ZR99887" in shipped)

        # طلبية بلا تتبّع — الرسالة تتبنى بلا خطأ
        o.tracking_number = None
        db.session.commit()
        s2 = o.whatsapp_message("shipped", "Budac")
        check("بلا تتبّع: الرسالة مازالت صالحة",
              "ZR99887" not in s2 and len(s2) > 20)

    # الصفحة
    page = client.get(f"/admin/orders/{oid2}").get_data(as_text=True)
    check("لوحة الواتساب موجودة", "wa-panel" in page)
    check("زر تأكيد الطلبية", "wa-primary" in page)
    check("الروابط تمشي لـwa.me", "https://wa.me/213551486151" in page)
    check("زر نسخ الرقم", "data-copy=" in page)

    # ⚠️ النص لازم يكون مرمّزًا للرابط، ماشي خامًا
    m = re.search(r'href="https://wa\.me/\d+\?text=([^"]+)"', page)
    check("نص الرسالة مرمّز فالرابط",
          bool(m) and " " not in m.group(1) and "%" in m.group(1),
          (m.group(1)[:50] if m else "ماكانش"))

    # زبون بلا هاتف
    with app.app_context():
        o = db.session.get(_O3, oid2)
        saved_phone = o.customer_phone
        o.customer_phone = ""
        db.session.commit()
    no_phone = client.get(f"/admin/orders/{oid2}").get_data(as_text=True)
    check("بلا هاتف: الصفحة ما تطيحش وتوري تنبيهًا",
          "wa-panel" not in no_phone and "ماكانش رقم هاتف" in no_phone)
    with app.app_context():
        o = db.session.get(_O3, oid2)
        o.customer_phone = saved_phone
        db.session.commit()

    # قائمة الطلبيات
    lst = client.get("/admin/orders").get_data(as_text=True)
    check("زر واتساب سريع فالقائمة", "wa-quick" in lst)

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

    # ───────── 9) الاختبار ما لوّثش مجلد الصور ─────────
    section("9. نظافة: الاختبار ما يكتبش فصور المتجر")
    real_dir = os.path.join(os.path.dirname(__file__), "..",
                            "static", "img", "products")
    if os.path.isdir(real_dir):
        names = os.listdir(real_dir)
        # صور الاختبار اسم منتجها هو الـslug المستعمل فوق
        leaked = [n for n in names if n.startswith(slug + "-")]
        check("ماكانش صور اختبار فمجلد المتجر", not leaked,
              f"{len(leaked)} ملف: {leaked[:3]}")
        check("UPLOAD_ROOT موجّه لمجلد مؤقت",
              app.config.get("UPLOAD_ROOT", "") == _TMP_UPLOADS)

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
