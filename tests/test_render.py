# -*- coding: utf-8 -*-
"""
اختبار عرض حقيقي: يشغّل التطبيق، يفتح كل الصفحات، ويفحص HTML المُخرَج.

⚠️ الفرق عن `test_full.py`: هذا ما يختبرش المنطق — يختبر أن القوالب
تتعرض بلا خطأ، وأن JSON-LD صالح فعلاً، وأن الترتيب الجديد تاع
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


def _has_emoji_form(ch):
    """
    واش هاد الرمز عندو نسخة إيموجي ملوّنة؟

    🔴 هاد الدالة هي خلاصة 3 جولات تاع تصليح غالط. الواتساب يبدّل
    أي رمز عندو صورة إيموجي بتلك الصورة، وكي ما تتحمّلش يبان
    «\u25C6» عند الزبون. الرموز النصّية (★ ♥ ✓ ⚑ ✆) ما عندهاش صورة،
    فتتكتب بالخط العادي وما عمرها تطيح.

    ما نعتمدوش على مكتبة خارجية — القائمة صريحة وقابلة للقراءة.
    """
    cp = ord(ch)

    # كل ما فوق U+1F000 تقريبًا إيموجي
    if cp >= 0x1F000:
        return True

    # رموز عندها Emoji_Presentation افتراضيًا (تبان ملوّنة بلا VS16)
    if 0x231A <= cp <= 0x231B: return True   # ⌚⌛
    if 0x23E9 <= cp <= 0x23F3: return True   # ⏩..⏳ (فيها ⏰)
    if 0x25FD <= cp <= 0x25FE: return True
    if 0x2614 <= cp <= 0x2615: return True
    if 0x2648 <= cp <= 0x2653: return True
    if 0x267F == cp or 0x2693 == cp: return True
    if 0x26A1 == cp: return True
    if 0x26AA <= cp <= 0x26AB: return True
    if 0x26BD <= cp <= 0x26BE: return True
    if 0x26C4 <= cp <= 0x26C5: return True
    if 0x26CE == cp or 0x26D4 == cp or 0x26EA == cp: return True
    if 0x26F2 <= cp <= 0x26F3: return True
    if 0x26F5 == cp or 0x26FA == cp or 0x26FD == cp: return True
    if 0x2705 == cp: return True             # ✅
    if 0x270A <= cp <= 0x270B: return True   # ✊✋
    if 0x2728 == cp: return True             # ✨
    if 0x274C == cp or 0x274E == cp: return True
    if 0x2753 <= cp <= 0x2755: return True
    if 0x2757 == cp: return True             # ❗
    if 0x2795 <= cp <= 0x2797: return True
    if 0x27B0 == cp or 0x27BF == cp: return True
    if 0x2B1B <= cp <= 0x2B1C: return True
    if 0x2B50 == cp or 0x2B55 == cp: return True   # ⭐ ⭕

    # رموز عندها نسخة إيموجي معروفة (حتى بلا VS16 التطبيقات تلوّنها)
    KNOWN_COLORED = {
        0x2611,  # ☑
        0x2714,  # ✔
        0x2716,  # ✖
        0x2764,  # ❤
        0x26A0,  # ⚠
        0x21A9, 0x21AA,  # ↩ ↪
        0x2934, 0x2935,
        0x2B05, 0x2B06, 0x2B07,
        0x2600, 0x2601, 0x2602, 0x2603,
        0x260E,  # ☎
        0x2620, 0x2622, 0x2623, 0x262E, 0x262F,
        0x2665, 0x2666, 0x2663, 0x2660,  # ♥♦♣♠ — الواتساب يلوّنهم أحيانًا
        0x270F, 0x2712, 0x2733, 0x2734, 0x2744, 0x2747,
        0x203C, 0x2049, 0x2122, 0x2139,
        0x3030, 0x303D,
    }
    if cp in KNOWN_COLORED:
        return True

    # منتقي العرض الإيموجي — يفرض النسخة الملوّنة
    if cp == 0xFE0F:
        return True

    return False


def check(name, ok, detail=""):
    """
    ⚠️ `str(detail)` ضروري: كثير من الفحوص تمرّر رقمًا كتفصيل
    (مثلاً `o.subtotal`). بلاه، الاختبار **الفاشل** يطيح بـTypeError
    قبل ما يطبع سبب الفشل — فتشوف انهيارًا بدل رسالة واضحة.
    """
    (PASSED if ok else FAILED).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name
          + ("  → " + str(detail) if not ok and detail != "" else ""))


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
        _p0 = Product.query.first()
        pid = _p0.id
        prod_name_for_uv = _p0.name
    for path, label in [
        ("/admin/", "الرئيسية"),
        ("/admin/products", "المنتجات"),
        (f"/admin/products/{pid}/edit", "تعديل منتج"),
        ("/admin/orders", "الطلبيات"),
        ("/admin/coupons", "الكوبونات"),
        ("/admin/settings", "الإعدادات"),
        ("/admin/shipping", "أسعار التوصيل"),
        ("/admin/reviews", "التقييمات"),
        ("/admin/products/unlinked-variants", "موديلات بلا صورة"),
    ]:
        try:
            r = client.get(path)
            check(f"لوحة: {label} ({r.status_code})", r.status_code in (200, 302))
        except Exception as e:
            check(f"لوحة: {label}", False, repr(e)[:120])

    # ───────── 6b) صفحة «موديلات بلا صورة» تشتغل فعلاً ─────────
    # ⚠️ الصفحة تتعرض بلا خطأ ماشي كافٍ — لازم تلقى الموديل الناقص
    # فعلاً وما تعرضش اللي عندو صورة.
    section("6b. موديلات بلا صورة")
    with app.app_context():
        prod = Product.query.first()
        naked = ProductVariant(product_id=prod.id, label="موديل بلا صورة",
                               price=700, stock_quantity=3)
        dressed = ProductVariant(product_id=prod.id, label="موديل عندو صورة",
                                 price=800, stock_quantity=3,
                                 image_filename="x.webp")
        db.session.add_all([naked, dressed])
        db.session.commit()

    uv = client.get("/admin/products/unlinked-variants").get_data(as_text=True)
    check("يوري الموديل الناقص", "موديل بلا صورة" in uv)
    check("🔴 ما يوريش الموديل اللي عندو صورة",
          "موديل عندو صورة" not in uv)
    check("يوري اسم المنتج فوقو", prod_name_for_uv in uv)

    dash = client.get("/admin/").get_data(as_text=True)
    check("النظرة العامة توري التنبيه", "موديل بلا صورة" in dash)
    check("التنبيه يودّي للصفحة", "unlinked-variants" in dash)
    check("رابط دائم فقائمة المنتجات",
          "unlinked-variants" in client.get("/admin/products").get_data(as_text=True))

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

    # ⚠️ الحالة اللي كانت تكسر: عدة صور — درك كل وحدة طلب مستقل
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

    # 🔴 الخلل اللي ضيّع 3 جولات: كود الرفع كان محشورًا داخل IIFE تاع
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
                     "missing", "noreply", "thanks", "review", "returned",
                     "unknown-kind"]:
            msg = o.whatsapp_message(kind, "Budac")
            check(f"رسالة «{kind}» مبنية", isinstance(msg, str) and len(msg) > 20,
                  repr(msg)[:60])

        # ═══ الإيموجي ═══
    # 🔴 الإيموجي المكتوب مباشرة فالكود يتخرّب (محرّر بترميز غالط،
    # رفع، نسخ/لصق) ويوصل للزبون «\ufffd». نكتبوه بهروب ASCII —
    # هاد الفحوص تتأكد أنو باقٍ صحيحًا.
    with app.app_context():
        o = _O3.query.first()
        src_models = open("models.py", encoding="utf-8").read()
        i0 = src_models.index("def whatsapp_message")
        i1 = src_models.index("# نوع مجهول", i0)
        body = src_models[i0:i1]
        # ⚠️ التعليقات فيها إيموجي للتوضيح (🔴 ⚠️) — المهم أن **نصوص
        # الرسائل** ماكانش فيها إيموجي مباشر. نشيلو التعليقات أول.
        # ⚠️ التعليقات ونصوص التوثيق فيها إيموجي للتوضيح (🔴 ⚠️).
        # المهم أن **نصوص الرسائل** ماكانش فيها إيموجي مباشر، فنشيلو
        # التعليقات والـdocstrings قبل الفحص.
        body = re.sub(r"#[^\n]*", "", body)
        body = re.sub(r'\"\"\".*?\"\"\"', "", body, flags=re.S)
        raw_emoji = re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", body)
        check("ماكانش إيموجي مباشر فبناء الرسائل",
              not raw_emoji, set(raw_emoji))

        # ⚠️ القائمة نفسها: كل إيموجي فيها لازم يكون 3 بايت بالضبط.
        # هاد الفحص هو اللي يمنع رجوع الخلل كي نزيدو إيموجي جديدًا.
        bad_map = {k: f"{v} U+{ord(v[0]):04X}" for k, v in _O3.EMOJI.items()
                   if any(_has_emoji_form(c) for c in v)}
        check("قائمة EMOJI كلها رموز نصّية", not bad_map, bad_map)

        for kind in ["confirm", "shipped", "arrived", "modify",
                     "missing", "noreply", "thanks", "review", "returned"]:
            msg = o.whatsapp_message(kind, "BUDAC")
            check(f"«{kind}»: ماكانش محرف تالف",
                  "\ufffd" not in msg, msg[:60])
            # 🔴 الفحص الصحيح: ماكانش رمز عندو **نسخة إيموجي**.
            #
            # فحص البايت اللي كان هنا قبل كان غالطًا: ⚑ و☑ بجوج
            # 3 بايت، الأولى واصلة والثانية طايحة. المعيار الحقيقي هو
            # واش الواتساب يبدّل الرمز بصورة، ماشي حجمو.
            #
            # القائمة تحت مبنية على رموز اليونيكود اللي عندها
            # Emoji_Presentation ولا نسخة ملوّنة معروفة.
            banned = [c for c in msg if _has_emoji_form(c)]
            check(f"«{kind}»: ماكانش رمز إيموجي", not banned,
                  [f"{c} U+{ord(c):04X}" for c in banned])
            # ⚠️ ترميز URL ثم فكّو لازم يرجّع نفس النص بالضبط
            import urllib.parse as _up
            enc = _up.quote(msg)
            check(f"«{kind}»: الترميز ذهابًا وإيابًا سليم",
                  _up.unquote(enc) == msg)

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
    check("زر «أكّد + روابط المنتجات»", "أكّد + روابط المنتجات" in page)

    # ⚠️ روابط المنتجات: الواتساب يوري معاينة بالصورة لأول رابط
    with app.app_context():
        o = _O3.query.first()
        plain = o.whatsapp_message("confirm", "BUDAC")
        linked = o.whatsapp_message("confirm", "BUDAC", "https://budacdz.com")
        check("بلا base_url: ماكانش روابط", "https://" not in plain)
        check("مع base_url: فيها روابط منتجات",
              "https://budacdz.com/product/" in linked, linked[:80])

    # ═══════ قناع رقم الهاتف ═══════
    checkout_html = open("templates/shop/checkout.html", encoding="utf-8").read()
    check("خانة الهاتف محدودة الطول (10 أرقام + 4 مسافات)",
          'maxlength="14"' in checkout_html)
    check("لوحة الأرقام فالموبايل", 'inputmode="numeric"' in checkout_html)
    check("القناع 0X XX XX XX XX موجود", "formatPhone" in checkout_html)
    check("القناع ينظّف رمز الدولة",
          "00213" in checkout_html and "'213'" in checkout_html)
    check("مثال يبان تحت الخانة", 'id="phoneHint"' in checkout_html)

    # 🔴 الحد الصارم: 10 أرقام، لا أكثر — بثلاث طبقات
    check("حد أقصى معرّف بوضوح",
          "PHONE_MAX_DIGITS = 10" in checkout_html)
    check("القص يستعمل الحد ماشي رقمًا مكتوبًا",
          "slice(0, PHONE_MAX_DIGITS)" in checkout_html)
    check("المدخل يتمنع قبل ما يدخل (beforeinput)",
          "'beforeinput'" in checkout_html)
    check("اللصق يتقص هو تاني",
          "'paste'" in checkout_html and "formatPhone(text)" in checkout_html)
    check("الحروف اللي ماشي أرقام ممنوعة",
          "e.preventDefault()" in checkout_html)

    # ═══════ مشاركة رابط الموديل ═══════
    # 🔴 قبل، رابط المشاركة كان دايمًا الصفحة العامة — الزبون يشارك
    # موديلاً معيّنًا وصاحبو يلقى الموديل الافتراضي بسعر آخر.
    prod_html = open("templates/shop/product.html", encoding="utf-8").read()
    check("رابط المشاركة يبنى بـ«?v=»", '"?v=" + encodeURIComponent(vid)' in prod_html)
    check("الواتساب والفيسبوك والنسخ كلهم يتحدّثو",
          'id="shareWa"' in prod_html and 'id="shareFb"' in prod_html
          and 'copy.setAttribute("data-link", link)' in prod_html)
    check("الصفحة تختار الموديل من الرابط",
          "selectVariantFromUrl" in prod_html)
    check("«?v» ماشي «#» — الشبكات تقرا الرابط فالسيرفر",
          "window.location.hash" not in prod_html)
    check("قراءة الرابط محميّة (متصفح قديم)",
          "search.match(/[?&]v=([^&]+)/)" in prod_html)

    # ═══════ شعار وأيقونة اللوحة ═══════
    admin_base = open("templates/admin/base_admin.html", encoding="utf-8").read()
    check("اللوحة عندها favicon", 'rel="icon"' in admin_base)
    check("اللوحة عندها apple-touch-icon", "apple-touch-icon" in admin_base)
    check("شعار اللوحة صورة ماشي نص",
          "admin-brand-logo" in admin_base and "logo.png" in admin_base)
    check("الشعار يودّي للمتجر", "shop.home" in admin_base)

    admin_css = open("static/css/admin.css", encoding="utf-8").read()
    check("الشعار محدود الارتفاع (ما ياكلش الشريط)",
          ".admin-brand-logo img" in admin_css and "height: 38px" in admin_css)

    # ⚠️ رسالة «اطلب رأيه» — الرابط لازم يكون **كاملاً** وإلا الواتساب
    # ما يخدمهش، ولازم يمشي لصفحة تقييمات المتجر ماشي لصفحة منتج.
    with app.app_context():
        o = _O3.query.first()
        rev = o.whatsapp_message("review", "BUDAC", "https://budacdz.com")
        check("رأي: فيها رابط /reviews كامل",
              "https://budacdz.com/reviews" in rev, rev[:80])
        fallback = o.whatsapp_message("review", "BUDAC")
        check("رأي بلا base_url: رابط احتياطي ماشي مكسور",
              "https://budacdz.com/reviews" in fallback, fallback[:80])
        check("رأي: فيها اسم الزبون", "علي" in rev)

    check("زر «اطلب رأيه» فالصفحة", "اطلب رأيه" in page)
    check("زر الرأي يمرّر رابط الموقع", "/reviews" in page or "%2Freviews" in page)

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

    # ───────── 7.18) تعديل الطلبية ─────────
    section("7.18 تعديل الطلبية (زيادة · نقصان · حذف)")
    from models import Order as _O4, OrderItem as _OI2, Coupon as _Cp

    with app.app_context():
        # طلبية نظيفة بكوبون 10% وتوصيل 500
        db.session.add(_Cp(code="EDIT10", discount_type=_Cp.TYPE_PERCENT,
                           value=10, is_active=True))
        prod6 = _P.query.filter_by(slug=slug).first()
        var6 = prod6.default_variant

        o = _O4(order_number="BD-EDIT", customer_name="زبون",
                customer_phone="0551000000", wilaya_code="16",
                wilaya_name="الجزائر", delivery_type="home",
                coupon_code="EDIT10", subtotal=0, discount_amount=0,
                shipping_price=500, total=0)
        db.session.add(o)
        db.session.flush()
        db.session.add(_OI2(order_id=o.id, product_id=prod6.id,
                            variant_id=var6.id, item_name=prod6.name,
                            variant_label=var6.label,
                            unit_price=100, quantity=2))
        db.session.commit()
        o.recalculate()
        db.session.commit()
        eid, item_id = o.id, o.items[0].id
        var_id = var6.id
        check("الحالة الأولى: 200-20+500=680", o.total == 680, o.total)

    # زيادة الكمية
    client.post(f"/admin/orders/{eid}/items/{item_id}/update",
                data={"quantity": 5, "unit_price": 100})
    with app.app_context():
        o = db.session.get(_O4, eid)
        check("كمية 5 → subtotal 500", o.subtotal == 500, o.subtotal)
        check("⚠️ الخصم يتحسب من الجديد (50)", o.discount_amount == 50,
              o.discount_amount)
        check("المجموع 500-50+500=950", o.total == 950, o.total)

    # نقصان
    client.post(f"/admin/orders/{eid}/items/{item_id}/update",
                data={"quantity": 1, "unit_price": 100})
    with app.app_context():
        o = db.session.get(_O4, eid)
        check("كمية 1 → المجموع 100-10+500=590", o.total == 590, o.total)

    # كمية صفر مرفوضة
    client.post(f"/admin/orders/{eid}/items/{item_id}/update",
                data={"quantity": 0})
    with app.app_context():
        check("كمية 0 مرفوضة", db.session.get(_O4, eid).items[0].quantity == 1)

    # سعر سالب مرفوض
    client.post(f"/admin/orders/{eid}/items/{item_id}/update",
                data={"unit_price": -50})
    with app.app_context():
        check("سعر سالب مرفوض",
              db.session.get(_O4, eid).items[0].unit_price == 100)

    # زيادة منتج
    client.post(f"/admin/orders/{eid}/items/add",
                data={"variant_id": var_id, "quantity": 3, "unit_price": 200})
    with app.app_context():
        o = db.session.get(_O4, eid)
        check("اتزاد عنصر", len(o.items) == 2, len(o.items))
        check("subtotal 100+600=700", o.subtotal == 700, o.subtotal)
        check("المجموع 700-70+500=1130", o.total == 1130, o.total)

    # 🔴 الحذف — هنا كان الخلل: العلاقة تبقى محمّلة فالذاكرة
    client.post(f"/admin/orders/{eid}/items/{item_id}/delete")
    with app.app_context():
        o = db.session.get(_O4, eid)
        check("بقى عنصر واحد", len(o.items) == 1, len(o.items))
        check("⚠️ الحذف يعيد الحساب (600)", o.subtotal == 600, o.subtotal)
        check("المجموع 600-60+500=1040", o.total == 1040, o.total)

    # آخر عنصر محمي
    with app.app_context():
        last_id = db.session.get(_O4, eid).items[0].id
    client.post(f"/admin/orders/{eid}/items/{last_id}/delete")
    with app.app_context():
        check("آخر عنصر ما ينمسحش",
              len(db.session.get(_O4, eid).items) == 1)

    # سعر التوصيل يدويًا
    client.post(f"/admin/orders/{eid}/shipping", data={"shipping_price": 0})
    with app.app_context():
        o = db.session.get(_O4, eid)
        check("توصيل 0 → المجموع 540", o.total == 540, o.total)
    client.post(f"/admin/orders/{eid}/shipping", data={"shipping_price": -100})
    with app.app_context():
        check("توصيل سالب مرفوض",
              db.session.get(_O4, eid).shipping_price == 0)

    # ⚠️ بعد الإرسال لشركة التوصيل: التعديل ممنوع
    with app.app_context():
        o = db.session.get(_O4, eid)
        o.tracking_number = "ZR-LOCK"
        db.session.commit()
        frozen = o.total
        check("is_editable = False بعد الإرسال", o.is_editable is False)

    client.post(f"/admin/orders/{eid}/items/{last_id}/update",
                data={"quantity": 99})
    client.post(f"/admin/orders/{eid}/items/add",
                data={"variant_id": var_id, "quantity": 5})
    client.post(f"/admin/orders/{eid}/shipping", data={"shipping_price": 9999})
    with app.app_context():
        o = db.session.get(_O4, eid)
        check("⚠️ كل التعديلات مرفوضة بعد الإرسال",
              o.total == frozen and len(o.items) == 1, o.total)

    page_e = client.get(f"/admin/orders/{eid}").get_data(as_text=True)
    check("الصفحة توري أنها مقفولة", "ما تتعدّلش" in page_e)

    # طلبية قابلة للتعديل: الواجهة تبان
    with app.app_context():
        o = db.session.get(_O4, eid)
        o.tracking_number = None
        db.session.commit()
    page_e = client.get(f"/admin/orders/{eid}").get_data(as_text=True)
    check("فورم زيادة منتج يبان", "add-item-form" in page_e)
    check("حقول تعديل الكمية تبان", "item-edit" in page_e)
    check("تعديل سعر التوصيل يبان", "ship-edit" in page_e)

    # ───────── 7.19) وحدة البيع ─────────
    section("7.19 وحدة البيع (حبة · متر · مخصّص)")
    with app.app_context():
        prod7 = _P.query.filter_by(slug=slug).first()

        for raw, label, short in [
            ("piece", "الحبة", "حبة"),
            ("meter", "المتر", "متر"),
            ("رولو", "رولو", "رولو"),
            ("", "الحبة", "حبة"),          # فارغ = حبة
        ]:
            prod7.unit = raw
            db.session.commit()
            check(f"«{raw}» → {prod7.unit_label} / {prod7.unit_short}",
                  prod7.unit_label == label and prod7.unit_short == short,
                  f"{prod7.unit_label} / {prod7.unit_short}")

        prod7.unit = "meter"
        db.session.commit()

    shop7 = client.get(f"/product/{slug}").get_data(as_text=True)
    check("المتجر يقول «سعر المتر»", "سعر المتر" in shop7)

    # ⚠️ «سعر الحبة» تبقى موجودة فتعليقات JS — ما تبانش للزبون.
    # نفحصو النص المرئي برك (بلا تعليقات ولا كتل سكربت).
    visible = re.sub(r"<script.*?</script>", "", shop7, flags=re.S)
    visible = re.sub(r"\{#.*?#\}", "", visible, flags=re.S)
    visible = re.sub(r"<!--.*?-->", "", visible, flags=re.S)
    check("النص المرئي ما فيهش «سعر الحبة»",
          "سعر الحبة" not in visible,
          [l.strip() for l in visible.split("\n") if "سعر الحبة" in l][:2])

    check("JS عندو اسم الوحدة", "var UNIT_SHORT" in shop7)
    m_unit = re.search(r"var UNIT_SHORT = (.+);", shop7)
    # ⚠️ `tojson` يهرّب العربية لـ\uXXXX — سليم فالمتصفح، فنفكّو
    # الترميز قبل ما نقارنو بدل ما نبدّلو الفلتر (التهريب حماية من XSS).
    import json as _json
    check("قيمة UNIT_SHORT = متر",
          bool(m_unit) and _json.loads(m_unit.group(1)) == "متر",
          m_unit.group(1) if m_unit else "ماكانش")

    adm7 = client.get(f"/admin/products/{pid}/edit").get_data(as_text=True)
    check("منتقي الوحدة موجود", 'id="unitPicker"' in adm7)
    check("حقل الوحدة المخصّصة موجود", 'id="unitCustom"' in adm7)

    # الحفظ من الفورم
    # ⚠️ لازم فورم **كامل**: المسار يقرا كل الحقول، واللي ناقص يولّي
    # فارغًا. فورم بلا `is_active` يعطّل المنتج ويخفيه من المتجر —
    # وهذا سمّم فحوصًا بعديه فنسخة سابقة من هاد الاختبار.
    def full_form(**extra):
        with app.app_context():
            p_ = _P.query.filter_by(slug=slug).first()
            base = {
                "name": p_.name,
                "slug": p_.slug,
                "price": p_.price,
                "category_id": p_.category_id or "",
                "description": p_.description or "",
                "is_active": "1",
            }
        base.update(extra)
        return base

    client.post(f"/admin/products/{pid}/edit",
                data=full_form(unit="custom", unit_custom="علبة"))
    with app.app_context():
        check("حفظ وحدة مخصّصة",
              _P.query.filter_by(slug=slug).first().unit == "علبة",
              _P.query.filter_by(slug=slug).first().unit)

    # «مخصّص» بلا نص → يرجع للحبة بدل ما يخزّن فراغًا
    client.post(f"/admin/products/{pid}/edit",
                data=full_form(unit="custom", unit_custom="   "))
    with app.app_context():
        check("مخصّص فارغ → حبة",
              _P.query.filter_by(slug=slug).first().unit == "piece",
              _P.query.filter_by(slug=slug).first().unit)

    # نرجّعو المتر للفحوص اللي جاية
    client.post(f"/admin/products/{pid}/edit", data=full_form(unit="meter"))
    with app.app_context():
        p_check = _P.query.filter_by(slug=slug).first()
        check("⚠️ المنتج مازال مفعّلاً بعد الحفظ", p_check.is_active is True)
        check("⚠️ التصنيف ما ضاعش", p_check.category_id is not None)
        check("⚠️ الموديلات ما ضاعوش", len(p_check.variants) > 0,
              len(p_check.variants))

    # ───────── 7.20) توفّر الموديل ─────────
    section("7.20 متوفر / غير متوفر")
    with app.app_context():
        prod8 = _P.query.filter_by(slug=slug).first()
        # ⚠️ لازم موديل **من هاد المنتج** وماشي الافتراضي — وإلا نعطّلو
        # موديلاً من منتج آخر ونستنّاو أثرًا فصفحة غالطة.
        variants = [v for v in prod8.ordered_variants
                    if v.id != prod8.default_variant.id]
        check("عند المنتج موديل غير الافتراضي", bool(variants),
              [v.label for v in prod8.ordered_variants])
        target = variants[0]
        tid = target.id
        check("الموديل متوفر بالبداية", target.is_available is True)
        check("is_in_stock = True", target.is_in_stock is True)

    client.post(f"/admin/variants/{tid}/available")
    with app.app_context():
        v = db.session.get(_V, tid)
        check("اتعطّل", v.is_available is False)
        check("⚠️ is_in_stock ولّى False", v.is_in_stock is False)

    shop8 = client.get(f"/product/{slug}").get_data(as_text=True)
    with app.app_context():
        off_label = db.session.get(_V, tid).label
    # ⚠️ نتأكدو أن **هاد** الموديل بالضبط عندو is-out، ماشي أي واحد
    # ⚠️ نجمعو **كل** الشرائح المشطوبة — المنتج عندو أكثر من موديل،
    # و`re.search` يرجّع الأول برك. المهم أن موديلنا داخل فيهم.
    out_labels = [
        m.group(1).strip()
        for m in re.finditer(
            r'class="variant-chip[^"]*is-out[^"]*"[^>]*>\s*([^<]+?)\s*<', shop8)
    ]
    all_chips = re.findall(
        r'class="variant-chip[^"]*"[^>]*>\s*([^<]+?)\s*<', shop8)
    check(f"«{off_label}» يبان مشطوبًا",
          off_label in out_labels,
          f"المشطوبين: {out_labels} | كل الشرائح: {all_chips}")

    client.post(f"/admin/variants/{tid}/available")
    with app.app_context():
        check("رجع متوفرًا", db.session.get(_V, tid).is_available is True)

    # ⚠️ تعطيل الافتراضي ينقل الافتراضية لموديل آخر متوفر
    with app.app_context():
        prod8 = _P.query.filter_by(slug=slug).first()
        default_id = prod8.default_variant.id
    client.post(f"/admin/variants/{default_id}/available")
    with app.app_context():
        prod8 = _P.query.filter_by(slug=slug).first()
        new_default = prod8.default_variant
        check("⚠️ الافتراضي انتقل لموديل متوفر",
              new_default.id != default_id and new_default.is_available,
              f"{new_default.label} (available={new_default.is_available})")
    client.post(f"/admin/variants/{default_id}/available")

    adm8 = client.get(f"/admin/products/{pid}/edit").get_data(as_text=True)
    check("زر التوفّر موجود فاللوحة", "avail-btn" in adm8)

    # ───────── 7.21) عدّاد المنتجات والنسخ ─────────
    section("7.21 عدّاد الطلبية والخانات القابلة للنسخ")
    with app.app_context():
        o9 = _O4.query.filter_by(order_number="BD-EDIT").first()
        oid9 = o9.id
        lines, pieces = len(o9.items), o9.item_count
    page9 = client.get(f"/admin/orders/{oid9}").get_data(as_text=True)
    check(f"عدد الأسطر ({lines}) يبان", f"{lines} سطر" in page9)
    check(f"عدد القطع ({pieces}) يبان", f"{pieces} قطعة" in page9)
    check("الاسم قابل للنسخ", 'class="copyable"' in page9)
    check("زر نسخ كل المعلومات", "انسخ كل المعلومات" in page9)
    check("عدد الخانات القابلة للنسخ ≥ 3",
          page9.count("copyable") >= 3, page9.count("copyable"))

    # ───────── 7.22) الحالات الجديدة ─────────
    section("7.22 قيد التجهيز · تم التجهيز")
    from models import Order as _O5, Review as _R

    check("STATUS_PREPARING موجودة", hasattr(_O5, "STATUS_PREPARING"))
    check("STATUS_PREPARED موجودة", hasattr(_O5, "STATUS_PREPARED"))
    check("«قيد التجهيز» فالتسميات",
          _O5.STATUS_LABELS.get("preparing") == "قيد التجهيز")
    check("«تم التجهيز» فالتسميات",
          _O5.STATUS_LABELS.get("prepared") == "تم التجهيز")
    check("الترتيب فيه 7 مراحل", len(_O5.STATUS_FLOW) == 7, _O5.STATUS_FLOW)
    check("«فالمكتب» فالتسميات",
          _O5.STATUS_LABELS.get("at_office") == "فالمكتب")
    check("«راجعة» فالتسميات",
          _O5.STATUS_LABELS.get("returned") == "راجعة")
    check("«فالمكتب» بين الشحن والتسليم",
          _O5.STATUS_FLOW.index("shipped")
          < _O5.STATUS_FLOW.index("at_office")
          < _O5.STATUS_FLOW.index("delivered"))
    # ⚠️ «فالمكتب» ما تخصّش توصيل الدار — الزبون ما يلزموش يشوف
    # مرحلة ما تجيش عمرها.
    o_home = _O5(order_number="X-1", customer_name="ن", customer_phone="0555",
                 wilaya_code="16", wilaya_name="الجزائر", total=0,
                 delivery_type="home", status="shipped")
    o_off = _O5(order_number="X-2", customer_name="ن", customer_phone="0555",
                wilaya_code="16", wilaya_name="الجزائر", total=0,
                delivery_type="office", status="shipped")
    keys_home = [s["key"] for s in o_home.progress_steps]
    keys_off = [s["key"] for s in o_off.progress_steps]
    check("توصيل الدار: بلا مرحلة «فالمكتب»", "at_office" not in keys_home, keys_home)
    check("توصيل المكتب: فيه مرحلة «فالمكتب»", "at_office" in keys_off, keys_off)

    o_ret = _O5(order_number="X-3", customer_name="ن", customer_phone="0555",
                wilaya_code="16", wilaya_name="الجزائر", total=0,
                status="returned")
    check("«راجعة»: ماكانش شريط تقدّم", o_ret.progress_steps == [])
    check("«راجعة»: الطلبية ماشي مفتوحة", not o_ret.is_open)

    check("«راجعة» حالة نهائية برّا المسار",
          "returned" in _O5.STATUS_TERMINAL
          and "returned" not in _O5.STATUS_FLOW)
    check("الترتيب منطقي: تجهيز قبل الشحن",
          _O5.STATUS_FLOW.index("preparing") < _O5.STATUS_FLOW.index("shipped"))

    css = open("static/css/admin.css", encoding="utf-8").read()
    for st in ["preparing", "prepared"]:
        check(f"لون .status-{st} معرّف", f".status-{st}" in css)

    with app.app_context():
        o = _O5.query.first()
        o.status = "preparing"
        db.session.commit()
        oid_s = o.id
    pg = client.get(f"/admin/orders/{oid_s}").get_data(as_text=True)
    check("الحالة تبان فصفحة الطلبية", "قيد التجهيز" in pg)
    check("⚠️ التعديل مسموح فحالة التجهيز",
          "add-item-form" in pg or "ما تتعدّلش" not in pg)

    # ───────── 7.23) بطاقات الموبايل ─────────
    section("7.23 بطاقات الطلبيات للموبايل")
    lst2 = client.get("/admin/orders").get_data(as_text=True)
    check("البطاقات موجودة", "order-cards" in lst2)
    check("الجدول مازال موجود (سطح المكتب)", "orders-table" in lst2)
    check("البطاقة فيها المجموع", "order-card-total" in lst2)
    check("البطاقة فيها زر واتساب", "wa.me" in lst2)

    css_a = open("static/css/admin.css", encoding="utf-8").read()
    check("CSS يخفي البطاقات فالكبير", ".order-cards { display: none; }" in css_a)
    check("CSS يخفي الجدول فالموبايل",
          ".orders-table { display: none; }" in css_a)

    # الجداول الأخرى قابلة للزحلقة
    # ⚠️ الغلاف يبان غير كي يكون فيه صفوف — صفحة فارغة ما تعرضش
    # جدولاً أصلاً. نفحصو صفحات فيها بيانات.
    for path, label in [("/admin/products", "المنتجات"),
                        ("/admin/categories", "التصنيفات"),
                        ("/admin/coupons", "الكوبونات")]:
        pg2 = client.get(path).get_data(as_text=True)
        check(f"{label}: جدول قابل للزحلقة", "admin-table-wrap" in pg2,
              "الصفحة فارغة؟" if "admin-table" not in pg2 else "الغلاف ناقص")

    # القالب نفسو فيه الغلاف حتى لو الصفحة فارغة درك
    rv_tpl = open("templates/admin/reviews.html", encoding="utf-8").read()
    check("قالب التقييمات فيه الغلاف", "admin-table-wrap" in rv_tpl)

    # ───────── 7.24) تقييمات المتجر ─────────
    section("7.24 تقييمات المتجر")
    with app.app_context():
        check("ماكانش تقييمات بالبداية", _R.store_stats() == (0, 0),
              _R.store_stats())

    # الصفحة تخدم وهي فارغة
    r = client.get("/reviews")
    check(f"/reviews يتعرض ({r.status_code})", r.status_code == 200)
    empty_pg = r.get_data(as_text=True)
    check("حالة فارغة صادقة (ماكانش 0 من 5)",
          "rv-empty" in empty_pg and "aggregateRating" not in empty_pg)
    check("الفورم موجود", "add_store_review" in empty_pg
          or "rv-form" in empty_pg)

    # إرسال تقييم
    r = client.post("/reviews", data={"author_name": "أمين ب.",
                                      "rating": 5,
                                      "comment": "خدمة سريعة وتغليف مليح"})
    check(f"إرسال التقييم ({r.status_code})", r.status_code in (200, 302))
    with app.app_context():
        rv = _R.query.filter(_R.product_id.is_(None)).first()
        check("انحفظ كتقييم للمتجر", rv is not None and rv.product_id is None)
        check("⚠️ ما يبانش قبل الموافقة", rv.is_approved is False)
        check("is_store_review = True", rv.is_store_review is True)
        check("target_label = المتجر", rv.target_label == "المتجر")
        rid = rv.id

    # ⚠️ ما يبانش قبل الموافقة — نفحصو **قائمة التقييمات** برك،
    # لأن رسالة الشكر فالفلاش ممكن تكون فالصفحة كذلك.
    pg3 = client.get("/reviews").get_data(as_text=True)
    rv_section = pg3.split('class="rv-list"')[1] if 'class="rv-list"' in pg3 else ""
    check("غير المعتمد ما يبانش فالقائمة",
          "أمين ب." not in rv_section,
          "موجود فالقائمة" if rv_section else "ماكانش قائمة (سليم)")

    # بعد الموافقة
    client.post(f"/admin/reviews/{rid}/approve")
    pg4 = client.get("/reviews").get_data(as_text=True)
    check("بعد الموافقة يبان", "أمين ب." in pg4)
    check("البيانات المنظمة تبان", "aggregateRating" in pg4)
    with app.app_context():
        check("الإحصائيات (5.0, 1)", _R.store_stats() == (5.0, 1),
              _R.store_stats())

    # مدخلات فاسدة
    for name, data in [
        ("اسم فارغ", {"author_name": "", "rating": 5}),
        ("اسم حرف واحد", {"author_name": "أ", "rating": 5}),
        ("بلا نجوم", {"author_name": "سعاد", "rating": ""}),
        ("نجوم 0", {"author_name": "سعاد", "rating": 0}),
        ("نجوم 9", {"author_name": "سعاد", "rating": 9}),
        ("نجوم نص", {"author_name": "سعاد", "rating": "abc"}),
    ]:
        before_n = None
        with app.app_context():
            before_n = _R.query.filter(_R.product_id.is_(None)).count()
        client.post("/reviews", data=data)
        with app.app_context():
            after_n = _R.query.filter(_R.product_id.is_(None)).count()
        check(f"{name} مرفوض", after_n == before_n, f"{before_n} → {after_n}")

    # ⚠️ تقييم المتجر ما يبانش فصفحة منتج
    prod_pg = client.get(f"/product/{slug}").get_data(as_text=True)
    check("تقييم المتجر ما يبانش فصفحة المنتج", "أمين ب." not in prod_pg)

    # لوحة الأدمن ما تطيحش مع تقييم بلا منتج
    adm_r = client.get("/admin/reviews?show=all")
    check(f"لوحة التقييمات تتعرض ({adm_r.status_code})",
          adm_r.status_code == 200)
    check("تبويب المتجر موجود", "🏪 المتجر" in adm_r.get_data(as_text=True))

    adm_store = client.get("/admin/reviews?show=store")
    check("فلترة تقييمات المتجر تخدم", adm_store.status_code == 200)
    check("أمين يبان فيها", "أمين ب." in adm_store.get_data(as_text=True))

    # الرابط فالفوتر
    home = client.get("/").get_data(as_text=True)
    check("رابط «آراء الزبائن» فالفوتر", "آراء الزبائن" in home)

    # ───────── 7.25) طلبياتي ─────────
    section("7.25 صفحة «طلبياتي» للزبون")
    from models import Order as _O6

    r = client.get("/my-orders")
    check(f"الصفحة تتعرض ({r.status_code})", r.status_code == 200)
    mo = r.get_data(as_text=True)
    check("فورم البحث موجود", 'id="lookupForm"' in mo)
    check("⚠️ يقرا CSRF من meta ماشي input",
          'meta[name="csrf-token"]' in mo)

    with app.app_context():
        o = _O6.query.filter_by(order_number="BD-EDIT").first()
        o.customer_phone = "0551486151"
        o.status = _O6.STATUS_PENDING
        o.tracking_number = None
        db.session.commit()
        onum = o.order_number

    # البحث
    r = client.post("/my-orders/lookup",
                    data={"order_number": onum, "phone": "0551486151"})
    check(f"البحث الصحيح ({r.status_code})", r.status_code == 200)
    data = r.get_json() or {}
    check("الرد فيه الطلبية", data.get("ok") and "order" in data)
    if data.get("ok"):
        od = data["order"]
        check("فيه المراحل", len(od.get("steps", [])) == 6, len(od.get("steps", [])))
        check("فيه المنتجات", len(od.get("items", [])) > 0)
        check("can_edit = True فحالة «جديدة»", od.get("can_edit") is True)

    # ⚠️ صيغ الهاتف المختلفة لازم تخدم كلها
    for variant in ["0551486151", "551486151", "213551486151",
                    "+213 551 48 61 51", "0551-48-61-51"]:
        rr = client.post("/my-orders/lookup",
                         data={"order_number": onum, "phone": variant})
        check(f"هاتف «{variant}» يخدم", rr.status_code == 200, rr.status_code)

    # ⚠️ الأمان: رقم صحيح + هاتف غالط = مرفوض
    for name, num, ph in [
        ("هاتف غالط", onum, "0999999999"),
        ("رقم غالط", "BD-NOPE", "0551486151"),
        ("بلا هاتف", onum, ""),
        ("بلا رقم", "", "0551486151"),
        ("هاتف حروف", onum, "abcdefgh"),
    ]:
        rr = client.post("/my-orders/lookup",
                         data={"order_number": num, "phone": ph})
        check(f"🔒 {name} مرفوض", rr.status_code == 404, rr.status_code)

    # الرسالة وحدة — ما تكشفش واش الرقم موجود
    r1 = client.post("/my-orders/lookup",
                     data={"order_number": onum, "phone": "0999999999"})
    r2 = client.post("/my-orders/lookup",
                     data={"order_number": "BD-NOPE", "phone": "0999999999"})
    check("🔒 نفس الرسالة للحالتين",
          (r1.get_json() or {}).get("message") == (r2.get_json() or {}).get("message"))

    # ملاحظة الزبون
    r = client.post(f"/my-orders/{onum}/note",
                    data={"phone": "0551486151",
                          "note": "زيدلي 2 من الأبيض"})
    check(f"إرسال ملاحظة ({r.status_code})", r.status_code == 200)
    with app.app_context():
        o = _O6.query.filter_by(order_number=onum).first()
        check("الملاحظة انحفظات فالطلبية",
              "زيدلي 2 من الأبيض" in (o.customer_note or ""),
              o.customer_note)

    r = client.post(f"/my-orders/{onum}/note",
                    data={"phone": "0551486151", "note": "x"})
    check("ملاحظة قصيرة مرفوضة", r.status_code == 400)

    r = client.post(f"/my-orders/{onum}/note",
                    data={"phone": "0999999999", "note": "محاولة اختراق"})
    check("🔒 ملاحظة بهاتف غالط مرفوضة", r.status_code == 404)

    # ⚠️ بعد ما تخرج للتوصيل: الزبون ما يقدرش يعدّل
    with app.app_context():
        o = _O6.query.filter_by(order_number=onum).first()
        o.status = _O6.STATUS_SHIPPED
        db.session.commit()
        check("customer_can_edit = False فـ«فالطريق»",
              o.customer_can_edit is False)

    r = client.post(f"/my-orders/{onum}/note",
                    data={"phone": "0551486151", "note": "بدّلي الموديل"})
    check("🔒 التعديل مرفوض بعد الشحن", r.status_code == 400)
    r = client.post(f"/my-orders/{onum}/cancel", data={"phone": "0551486151"})
    check("🔒 الإلغاء مرفوض بعد الشحن", r.status_code == 400)
    with app.app_context():
        check("الحالة ما تبدّلتش",
              _O6.query.filter_by(order_number=onum).first().status == "shipped")

    # الإلغاء يخدم فحالة «جديدة»
    with app.app_context():
        o = _O6.query.filter_by(order_number=onum).first()
        o.status = _O6.STATUS_PENDING
        db.session.commit()
    r = client.post(f"/my-orders/{onum}/cancel", data={"phone": "0551486151"})
    check(f"الإلغاء يخدم ({r.status_code})", r.status_code == 200)
    with app.app_context():
        o = _O6.query.filter_by(order_number=onum).first()
        check("الحالة ولّات «ملغاة»", o.status == "cancelled", o.status)
        check("الملغاة ماكانش عندها مراحل", o.progress_steps == [],
              o.progress_steps)

    # صفحة النجاح تحفظ الطلبية تلقائيًا
    succ = open("templates/shop/order_success.html", encoding="utf-8").read()
    check("صفحة النجاح تحفظ فالتخزين المحلي", "budac_orders" in succ)

    # الروابط اتبدّلو
    home2 = client.get("/").get_data(as_text=True)
    check("رابط «طلبياتي» فالموقع", "طلبياتي" in home2)

    # ───────── 7.26) نسخ المنتج ─────────
    section("7.26 نسخة كاملة للمنتج")
    from models import VariantPriceTier as _T, VariantPack as _Pk

    with app.app_context():
        src = _P.query.filter_by(slug=slug).first()
        # نضمنو أن فيه أسعار جملة وعرض جاهز باش النسخة تتفحص كامل
        v_src = src.default_variant
        if not v_src.price_tiers:
            db.session.add(_T(variant_id=v_src.id, min_quantity=10,
                              max_quantity=None, unit_price=90))
        if not v_src.packs:
            db.session.add(_Pk(variant_id=v_src.id, quantity=12,
                               label="رولو", price=700, sort_order=0))
        src.unit = "meter"
        db.session.commit()

        before = {
            "products": _P.query.count(),
            "name": src.name,
            "slug": src.slug,
            "variants": len(src.variants),
            "images": len(src.images),
            "tiers": sum(len(v.price_tiers) for v in src.variants),
            "packs": sum(len(v.packs) for v in src.variants),
            "unit": src.unit,
        }
        src_id = src.id

    r = client.post(f"/admin/products/{src_id}/duplicate")
    check(f"النسخ نجح ({r.status_code})", r.status_code in (200, 302))

    with app.app_context():
        check("اتزاد منتج واحد", _P.query.count() == before["products"] + 1,
              _P.query.count())

        copy = _P.query.filter(_P.slug.like(before["slug"] + "-%")).first()
        check("النسخة موجودة", copy is not None)

    if copy is None:
        check("باقي فحوص النسخ", False, "ماكانش نسخة")
    else:
        with app.app_context():
            copy = _P.query.filter(_P.slug.like(before["slug"] + "-%")).first()
            src = _P.query.filter_by(slug=before["slug"]).first()

            check("⚠️ النسخة معطّلة", copy.is_active is False)
            check("الاسم فيه «(نسخة)»", "(نسخة)" in copy.name, copy.name)
            check("الـslug فريد", copy.slug != before["slug"], copy.slug)
            check("السعر اتنسخ", copy.price == src.price)
            check("الوحدة اتنسخت", copy.unit == before["unit"], copy.unit)
            check("التصنيف اتنسخ", copy.category_id == src.category_id)

            check(f"الصور اتنسخو ({len(copy.images)})",
                  len(copy.images) == before["images"],
                  f"{len(copy.images)} بدل {before['images']}")
            check(f"الموديلات اتنسخو ({len(copy.variants)})",
                  len(copy.variants) == before["variants"],
                  f"{len(copy.variants)} بدل {before['variants']}")
            check("أسعار الجملة اتنسخو",
                  sum(len(v.price_tiers) for v in copy.variants) == before["tiers"],
                  sum(len(v.price_tiers) for v in copy.variants))
            check("العروض الجاهزة اتنسخو",
                  sum(len(v.packs) for v in copy.variants) == before["packs"],
                  sum(len(v.packs) for v in copy.variants))
            check("فيها موديل افتراضي", copy.default_variant is not None)

            # ⚠️ SKU ما يتنسخش — لازم يبقى فريدًا
            check("SKU ما اتنسخش",
                  all(v.sku is None for v in copy.variants),
                  [v.sku for v in copy.variants])

            # 🔴 الأهم: النسخة مستقلة — تعديلها ما يمسّش الأصل
            copy.name = "اسم مبدّل"
            copy.price = 9999
            copy.variants[0].label = "موديل مبدّل"
            copy.variants[0].price = 8888
            db.session.commit()

            src = _P.query.filter_by(slug=before["slug"]).first()
            check("🔴 الأصل ما تبدّلش اسمو", src.name == before["name"], src.name)
            check("🔴 الأصل ما تبدّلش سعرو", src.price != 9999, src.price)
            check("🔴 موديلات الأصل ما تبدّلوش",
                  all(v.label != "موديل مبدّل" for v in src.variants),
                  [v.label for v in src.variants])

            copy_id = copy.id

        # ⚠️ حذف النسخة ما يمسّش الأصل ولا صورو
        with app.app_context():
            src_images = [i.image_filename
                          for i in _P.query.filter_by(slug=before["slug"]).first().images]

        client.post(f"/admin/products/{copy_id}/delete")

        with app.app_context():
            src = _P.query.filter_by(slug=before["slug"]).first()
            check("🔴 الأصل باقٍ بعد حذف النسخة", src is not None)
            check("🔴 صور الأصل باقية",
                  [i.image_filename for i in src.images] == src_images,
                  [i.image_filename for i in src.images])
            check("🔴 موديلات الأصل باقية",
                  len(src.variants) == before["variants"], len(src.variants))

    # نسخة من نسخة → slug مازال فريد
    client.post(f"/admin/products/{src_id}/duplicate")
    client.post(f"/admin/products/{src_id}/duplicate")
    with app.app_context():
        slugs = [p_.slug for p_ in
                 _P.query.filter(_P.slug.like(before["slug"] + "-%")).all()]
        check("نسخ متعددة بـslug فريد",
              len(slugs) == len(set(slugs)) and len(slugs) >= 2, slugs)

    # الأزرار فالواجهة
    lst3 = client.get("/admin/products").get_data(as_text=True)
    check("زر النسخة فقائمة المنتجات", "product_duplicate" in lst3
          or "⧉ نسخة" in lst3)
    frm = client.get(f"/admin/products/{src_id}/edit").get_data(as_text=True)
    check("زر النسخة فصفحة التعديل", "⧉ نسخة" in frm)

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
