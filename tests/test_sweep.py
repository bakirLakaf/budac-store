# -*- coding: utf-8 -*-
"""
فحص شامل — يمشي على **كل** المسارات والحالات الحدّية، ماشي على
الميزة اللي خدمنا عليها آخر مرة برك.

⚠️ علاش هاد الملف موجود؟
الاختبارات الأخرى تفحص السلوك اللي **قصدناه**. هذا يفحص السلوك
تحت الضغط: مدخلات فاسدة، بيانات ناقصة، قيم سالبة، ملفات مغشوشة.
هنا لقينا الكوبون بنسبة سالبة اللي كان **يزيد** فالفاتورة.

التشغيل: python tests/test_sweep.py
"""

import os, sys, io, tempfile, shutil, atexit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "sweep-test-key")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

# ⚠️ الرفع يمشي لمجلد مؤقت — ما نلوّثوش صور المتجر
_TMP = tempfile.mkdtemp(prefix="budac-sweep-")
os.environ["UPLOAD_ROOT"] = _TMP
atexit.register(shutil.rmtree, _TMP, True)

from app import create_app
from models import (db, Product, ProductVariant, Category, ProductImage,
                    VariantPriceTier, AdminUser, Carrier, ShippingRate,
                    Coupon)

PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name + ("  → " + str(detail) if not ok else ""))


def section(t):
    print(f"\n{'='*60}\n{t}\n{'='*60}")


def build():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = True   # نتركو CSRF مفعّلاً كيما الإنتاج
    app.config["TESTING"] = True

    with app.app_context():
        db.create_all()

        admin = AdminUser(username="admin")
        admin.set_password("pass1234")
        db.session.add(admin)

        cat = Category(name="تصنيف", slug="cat")
        db.session.add(cat)
        db.session.commit()

        p = Product(name="منتج", slug="prod", price=100,
                    category_id=cat.id, stock_quantity=0)
        db.session.add(p)
        db.session.commit()

        v = ProductVariant(product_id=p.id, label="موديل", price=120,
                           stock_quantity=0, is_default=True)
        db.session.add(v)
        db.session.flush()

        db.session.add(VariantPriceTier(variant_id=v.id, min_quantity=10,
                                        max_quantity=99, unit_price=100))
        db.session.add(VariantPriceTier(variant_id=v.id, min_quantity=100,
                                        max_quantity=None, unit_price=80))

        carrier = Carrier(name="ZR", code="zrexpress", is_active=True)
        db.session.add(carrier)
        db.session.commit()
        db.session.add(ShippingRate(carrier_id=carrier.id, wilaya_code="16",
                                    wilaya_name="الجزائر",
                                    home_price=500, office_price=300))
        db.session.commit()

        return app, p.id, v.id


def cart_qty(client):
    """مجموع الكميات فالسلة — الشكل يقدر يكون رقمًا ولا قاموسًا."""
    with client.session_transaction() as sess:
        cart = sess.get("cart", {}) or {}
    total = 0
    for value in cart.values():
        total += value if isinstance(value, int) else value.get("quantity", 0)
    return total


def main():
    app, pid, vid = build()
    client = app.test_client()

    # ═════════ 1) كل مسارات GET ═════════
    section("1. كل مسارات GET — ماكانش 500")
    tested, broken = 0, []
    for rule in sorted(app.url_map.iter_rules(), key=str):
        if "GET" not in rule.methods or rule.endpoint == "static":
            continue
        args = {}
        skip = False
        for arg in rule.arguments:
            conv = rule._converters.get(arg).__class__.__name__
            if "Integer" in conv:
                args[arg] = 999999          # مُعرّف ماشي موجود
            elif any(k in conv for k in ("Path", "Unicode", "String")):
                args[arg] = "does-not-exist"
            else:
                skip = True
        if skip:
            continue
        try:
            url = rule.build(args)
            if isinstance(url, tuple):
                url = url[1]
            resp = client.get(url)
            tested += 1
            if resp.status_code >= 500:
                broken.append(f"{url} → {resp.status_code}")
        except Exception as e:
            broken.append(f"{rule} → {repr(e)[:80]}")
    check(f"{tested} مسار GET بمُعرّفات وهمية", not broken, broken[:5])

    # ═════════ 2) كل مسارات POST بجسم فارغ ═════════
    section("2. كل مسارات POST بجسم فارغ")
    tested, broken = 0, []
    for rule in sorted(app.url_map.iter_rules(), key=str):
        if "POST" not in rule.methods or rule.endpoint == "static":
            continue
        if "logout" in rule.endpoint:
            continue
        args, skip = {}, False
        for arg in rule.arguments:
            conv = rule._converters.get(arg).__class__.__name__
            if "Integer" in conv:
                args[arg] = pid
            elif any(k in conv for k in ("Path", "Unicode", "String")):
                args[arg] = "prod"
            else:
                skip = True
        if skip:
            continue
        try:
            url = rule.build(args)
            if isinstance(url, tuple):
                url = url[1]
            resp = client.post(url, data={})
            tested += 1
            if resp.status_code >= 500:
                broken.append(f"{url} → {resp.status_code}")
        except Exception as e:
            broken.append(f"{rule} → {repr(e)[:80]}")
    check(f"{tested} مسار POST بلا معطيات", not broken, broken[:5])

    # ═════════ 3) السلة بمدخلات فاسدة ═════════
    section("3. السلة — مدخلات فاسدة")
    hostile = [
        ("كمية سالبة", -5), ("كمية صفر", 0), ("كمية ضخمة", 10 ** 9),
        ("كمية نص", "abc"), ("كمية عشرية", "2.7"), ("كمية فارغة", ""),
        ("حقن SQL", "1; DROP TABLE orders"),
        ("XSS", "<script>alert(1)</script>"),
    ]
    for name, qty in hostile:
        c = app.test_client()
        c.post("/cart/add", data={"kind": "product", "item_id": pid,
                                  "quantity": qty})
        total = cart_qty(c)
        check(f"{name} → السلة فيها {total}", 0 <= total <= 5000, total)

    c = app.test_client()
    c.post("/cart/add", data={"kind": "product", "item_id": 999999,
                              "quantity": 1})
    check("منتج ماشي موجود ما يدخلش للسلة", cart_qty(c) == 0, cart_qty(c))

    # ═════════ 4) الحساب المالي ═════════
    section("4. أسعار الجملة — الحدود بالضبط")
    with app.app_context():
        v = db.session.get(ProductVariant, vid)
        for qty, want in [(1, 120), (9, 120), (10, 100), (99, 100),
                          (100, 80), (5000, 80), (0, 120), (-5, 120)]:
            got = v.price_for_quantity(qty)
            check(f"كمية {qty} → {got} دج", got == want, f"المتوقّع {want}")

    section("5. الكوبونات — القيم العدائية")
    with app.app_context():
        def coupon(code, dtype, value, max_discount=None):
            c = Coupon(code=code, discount_type=dtype, value=value,
                       max_discount=max_discount, is_active=True)
            db.session.add(c)
            db.session.commit()
            return c

        cases = [
            # (الاسم، النوع، القيمة، السقف، المجموع، الشحن، الخصم المتوقّع)
            ("نسبة عادية 10%", Coupon.TYPE_PERCENT, 10, None, 1000, 500, 100),
            ("نسبة 200% تنحدّ بالمجموع", Coupon.TYPE_PERCENT, 200, None, 1000, 500, 1000),
            ("سقف الخصم محترم", Coupon.TYPE_PERCENT, 50, 100, 1000, 500, 100),
            ("خصم ثابت أكبر من المجموع", Coupon.TYPE_FIXED, 500, None, 300, 500, 300),
            ("مجموع صفر", Coupon.TYPE_PERCENT, 10, None, 0, 500, 0),
            # 🔴 هاد التلاتة هما اللي كشفو الخلل
            ("نسبة سالبة", Coupon.TYPE_PERCENT, -20, None, 1000, 500, 0),
            ("خصم ثابت سالب", Coupon.TYPE_FIXED, -500, None, 1000, 500, 0),
            ("سقف سالب", Coupon.TYPE_PERCENT, 50, -100, 1000, 500, 0),
        ]
        for i, (name, dtype, value, cap, sub, ship, want) in enumerate(cases):
            c = coupon(f"SWEEP{i}", dtype, value, cap)
            got, _ = c.compute_discount(sub, ship)
            check(f"{name} → {got}", got == want and got >= 0,
                  f"المتوقّع {want}")

        fs = coupon("SWEEPFS", Coupon.TYPE_FREE_SHIPPING, 0)
        d, s = fs.compute_discount(1000, 500)
        check("توصيل مجاني", d == 0 and s == 500, (d, s))
        d, s = fs.compute_discount(1000, -500)
        check("شحن سالب ما يعطيش خصمًا سالبًا", d >= 0 and s >= 0, (d, s))

    # ═════════ 6) حالات حدّية فالمنتجات ═════════
    section("6. منتجات ناقصة — الخصائص ما تطيحش")
    with app.app_context():
        cat_id = Category.query.first().id
        edge = [
            ("بلا صور ولا موديلات", dict(name="E1", slug="e1", price=100,
                                          category_id=cat_id, stock_quantity=0)),
            ("سعر صفر", dict(name="E2", slug="e2", price=0,
                             category_id=cat_id, stock_quantity=0)),
            ("بلا تصنيف", dict(name="E3", slug="e3", price=50,
                               category_id=None, stock_quantity=0)),
            ("خصم مقلوب", dict(name="E4", slug="e4", price=200,
                               compare_at_price=100, category_id=cat_id,
                               stock_quantity=0)),
        ]
        for _, kwargs in edge:
            db.session.add(Product(**kwargs))
        db.session.commit()

        # موديل بلا سعر + موديل بصورة ماشي موجودة
        e1 = Product.query.filter_by(slug="e1").first()
        db.session.add(ProductVariant(product_id=e1.id, label="بلا سعر",
                                      price=None, stock_quantity=0))
        db.session.add(ProductVariant(product_id=e1.id, label="صورة وهمية",
                                      price=10, stock_quantity=0,
                                      image_filename="ghost.webp"))
        db.session.commit()

        props = ["display_price", "price_display", "has_discount",
                 "discount_percent", "gallery_images", "main_images",
                 "extra_images", "ordered_variants", "default_variant",
                 "_image_rank", "is_in_stock", "available_quantity"]
        broken = []
        for label, kwargs in edge:
            p = Product.query.filter_by(slug=kwargs["slug"]).first()
            for prop in props:
                try:
                    getattr(p, prop)
                except Exception as e:
                    broken.append(f"{label}.{prop}: {repr(e)[:60]}")
        check(f"{len(edge) * len(props)} خاصية على منتجات ناقصة",
              not broken, broken[:4])

    for slug in ["e1", "e2", "e3", "e4"]:
        r = client.get(f"/product/{slug}")
        check(f"/product/{slug} يتعرض", r.status_code == 200, r.status_code)

    # ═════════ 7) استيراد Excel بملفات فاسدة ═════════
    section("7. استيراد Excel — ملفات مغشوشة")
    admin = app.test_client()
    page = admin.get("/admin/login").get_data(as_text=True)
    import re
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page)
    admin.post("/admin/login", data={"username": "admin",
                                     "password": "pass1234",
                                     "csrf_token": token.group(1) if token else ""})

    export = admin.get("/admin/products/export")
    check(f"التصدير يخدم ({export.status_code})", export.status_code == 200)

    with app.app_context():
        before = Product.query.count()

    bogus = [
        ("ملف نصي", io.BytesIO(b"not excel at all"), "x.xlsx"),
        ("ملف فارغ", io.BytesIO(b""), "empty.xlsx"),
        ("zip مغشوش", io.BytesIO(b"PK\x03\x04garbage"), "fake.xlsx"),
        ("امتداد غالط", io.BytesIO(export.data), "file.txt"),
    ]
    for name, buf, fname in bogus:
        r = admin.post("/admin/products/import",
                       data={"file": (buf, fname)},
                       content_type="multipart/form-data")
        check(f"{name} ما يكسرش ({r.status_code})", r.status_code < 500,
              r.status_code)

    with app.app_context():
        after = Product.query.count()
    check("⚠️ ماكانش منتج انمحى ولا انزاد", after == before,
          f"{before} → {after}")

    # ═════════ 8) رفع صور عدائية ═════════
    section("8. رفع الصور — ملفات عدائية")
    from PIL import Image as _Img

    def real_png():
        """PNG حقيقي — باش الرفض يكون بسبب **الاسم** ماشي المحتوى."""
        buf = io.BytesIO()
        _Img.new("RGB", (60, 60), (10, 120, 200)).save(buf, "PNG")
        buf.seek(0)
        return buf

    up = f"/admin/products/{pid}/images/upload"

    # ⚠️ هاذو **محتواهم غالط** — لازم يترفضو مهما كان الاسم
    for name, blob, fname in [
        ("تنفيذي .exe", b"MZ\x90\x00", "virus.exe"),
        ("سكربت .php", b"<?php system($_GET[0]); ?>", "shell.php"),
        ("بلا امتداد", b"\x89PNG\r\n\x1a\n", "noext"),
        ("اسم فارغ", b"\x89PNG\r\n\x1a\n", ""),
    ]:
        r = admin.post(up, data={"image": (io.BytesIO(blob), fname)},
                       content_type="multipart/form-data",
                       headers={"X-Requested-With": "fetch"})
        check(f"{name} مرفوض ({r.status_code})", r.status_code == 400,
              r.status_code)

    # ⚠️ هاذو **صور حقيقية بأسماء خطرة**. المهم ماشي واش يتقبلو —
    # المهم أن الاسم المحفوظ يكون آمن. الاختبار القديم كان يشوفهم
    # مرفوضين ويحسبها نجاحًا، وهو رفض بسبب المحتوى ماشي الاسم =
    # ثقة كاذبة.
    for name, fname in [
        ("مسار للخلف", "../../../etc/passwd.png"),
        ("مسار ويندوز", "..\\..\\windows\\system32\\evil.png"),
        ("امتداد مزدوج", "shell.php.png"),
        ("محارف تحكّم", "ev\x00il.png"),
        ("اسم طويل جدًا", "ا" * 300 + ".png"),
    ]:
        r = admin.post(up, data={"image": (real_png(), fname)},
                       content_type="multipart/form-data",
                       headers={"X-Requested-With": "fetch"})
        saved = (r.get_json() or {}).get("filename", "") if r.status_code == 200 else ""
        safe = (r.status_code == 400) or (
            "/" not in saved and "\\" not in saved and ".." not in saved
            and saved.endswith((".webp", ".png", ".jpg", ".jpeg"))
        )
        check(f"{name}: الاسم المحفوظ آمن", safe,
              f"status={r.status_code} saved={saved!r}")

    with app.app_context():
        names = [i.image_filename for i in ProductImage.query.all()]
    check("ماكانش اسم ملف فيه مسار",
          all("/" not in n and "\\" not in n and ".." not in n for n in names),
          [n for n in names if "/" in n or ".." in n][:3])

    # ⚠️ وماكانش ملف انكتب برّا مجلد الرفع
    escaped = []
    for root, _, files in os.walk(_TMP):
        for f in files:
            if ".." in f or f.startswith("passwd"):
                escaped.append(os.path.join(root, f))
    check("ماكانش ملف هرب من مجلد الرفع", not escaped, escaped[:3])

    # ═════════ النتيجة ═════════
    section("النتيجة")
    print(f"نجح: {len(PASSED)}/{len(PASSED) + len(FAILED)}")
    if FAILED:
        print("\n🔴 فشل:")
        for f in FAILED:
            print("  •", f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
