# -*- coding: utf-8 -*-
"""
اختبار نقل الصور بين «صور المنتج» و«الصور التوضيحية».

⚠️ علاش هاد الاختبار موجود؟

كي ترفع صورة موديل بالغلط فخانة «توضيحية»، ماكانش طريقة تصلحها:
أزرار ‹ › تحرّك الصورة داخل مجموعتها برك. الحل الوحيد كان تمسحها
وتعاود ترفعها.

زدنا مسار `product_image_regroup`. هاد الاختبار يحرس الحالات
الخطيرة فيه — وأخطر وحدة: نقل **الصورة الرئيسية** تاع المنتج
للتوضيحية. بلا حراسة، المنتج يبقى بـimage_filename يشير لصورة
برّا المعرض، وصفحة المنتج تبان بصورة مكسورة.

التشغيل: python tests/test_image_regroup.py
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "test-key-for-testing-only")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import create_app
from models import db, Product, Category, ProductImage, ProductVariant, AdminUser

PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name
          + ("  → " + str(detail) if not ok and detail != "" else ""))


def build(app):
    """منتج بصورتين رئيسيتين، وحدة توضيحية، وموديلين."""
    with app.app_context():
        db.session.query(ProductVariant).delete()
        db.session.query(ProductImage).delete()
        db.session.query(Product).delete()
        db.session.commit()

        category = Category.query.first()
        product = Product(
            name="كركوبة تجربة", slug="test-regroup", price=100,
            category_id=category.id, image_filename="a.webp",
        )
        db.session.add(product)
        db.session.flush()

        db.session.add(ProductImage(product_id=product.id, image_filename="a.webp",
                                    sort_order=0, is_extra=False))
        db.session.add(ProductImage(product_id=product.id, image_filename="b.webp",
                                    sort_order=1, is_extra=False))
        extra = ProductImage(product_id=product.id, image_filename="c.webp",
                             sort_order=0, is_extra=True, caption="وصف")
        db.session.add(extra)

        db.session.add(ProductVariant(product_id=product.id, label="دوري",
                                      image_filename="a.webp", is_default=True))
        second = ProductVariant(product_id=product.id, label="أرجونتي",
                                image_filename=None)
        db.session.add(second)
        db.session.commit()

        return product.id, extra.id, second.id


def main():
    print("\n" + "=" * 60)
    print("نقل الصور بين المجموعتين")
    print("=" * 60)

    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False

    with app.app_context():
        db.create_all()
        db.session.add(Category(name="تجربة", slug="t"))
        admin = AdminUser(username="tester")
        admin.set_password("x")
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id

    def url(pid, iid):
        return f"/admin/products/{pid}/images/{iid}/regroup"

    # ───────── 1) توضيحية ⇐ رئيسية، مربوطة بموديل ─────────
    print("\n[1] توضيحية ⇐ صور المنتج (مع ربط موديل)")
    pid, extra_id, variant_id = build(app)
    client.post(url(pid, extra_id), data={"to": "main", "variant_id": str(variant_id)})

    with app.app_context():
        product = db.session.get(Product, pid)
        image = db.session.get(ProductImage, extra_id)
        variant = db.session.get(ProductVariant, variant_id)

        check("ولّات صورة منتج", image.is_extra is False)
        check("الوصف اتمسح", image.caption is None)
        check("اتربطت بالموديل", variant.image_filename == "c.webp")
        check("اتحطّت فالآخر", image.sort_order == 2, image.sort_order)
        check("ماكانش توضيحية", len(product.extra_images) == 0)
        check("3 صور منتج", len(product.main_images) == 3)

    # ───────── 2) بلا ربط موديل ─────────
    print("\n[2] توضيحية ⇐ صور المنتج (بلا موديل)")
    pid, extra_id, _v = build(app)
    client.post(url(pid, extra_id), data={"to": "main"})

    with app.app_context():
        product = db.session.get(Product, pid)
        check("ولّات صورة منتج", db.session.get(ProductImage, extra_id).is_extra is False)
        check("ماكانش موديل اتربط غلط",
              sum(1 for v in product.variants if v.image_filename == "c.webp") == 0)

    # ───────── 3) الاتجاه العكسي يفكّ الموديل ─────────
    print("\n[3] صورة منتج ⇐ توضيحية")
    pid, extra_id, _v = build(app)
    with app.app_context():
        linked = ProductImage.query.filter_by(
            product_id=pid, image_filename="a.webp", is_extra=False
        ).first()
        linked_id = linked.id

    client.post(url(pid, linked_id), data={"to": "extra"})

    with app.app_context():
        product = db.session.get(Product, pid)
        check("ولّات توضيحية", db.session.get(ProductImage, linked_id).is_extra is True)
        check("الموديل اتفكّ منها",
              all(v.image_filename != "a.webp" for v in product.variants))

        # ⚠️ أخطر حالة: كانت هي الصورة الرئيسية تاع المنتج
        check("صورة المنتج الرئيسية اتبدّلت لوحدة أخرى",
              product.image_filename == "b.webp", product.image_filename)

    # ───────── 4) تفريغ كل صور المنتج ─────────
    print("\n[4] الحالة القصوى: كل الصور تولّي توضيحية")
    pid, _e, _v = build(app)
    with app.app_context():
        ids = [i.id for i in db.session.get(Product, pid).main_images]

    for image_id in ids:
        client.post(url(pid, image_id), data={"to": "extra"})

    with app.app_context():
        product = db.session.get(Product, pid)
        check("ماكانش صور منتج", len(product.main_images) == 0)
        check("image_filename ولّى None", product.image_filename is None)

    check("صفحة المنتج ما تطيحش",
          client.get("/product/test-regroup").status_code == 200)
    check("صفحة التعديل ما تطيحش",
          client.get(f"/admin/products/{pid}/edit").status_code == 200)

    # ───────── 5) الرفض ─────────
    print("\n[5] الحالات المرفوضة")
    pid, extra_id, _v = build(app)

    response = client.post(url(pid, extra_id), data={"to": "extra"}, follow_redirects=True)
    check("نقل لنفس المجموعة يترفض",
          "ديجا فهاد المجموعة" in response.get_data(as_text=True))

    check("صورة تاع منتج آخر → 404",
          client.post(url(99999, extra_id), data={"to": "main"}).status_code == 404)

    check("صورة ماكانتش → 404",
          client.post(url(pid, 99999), data={"to": "main"}).status_code == 404)

    print("\n" + "=" * 60)
    print(f"نجح: {len(PASSED)}  ·  فشل: {len(FAILED)}")
    print("=" * 60)
    if FAILED:
        for f in FAILED:
            print("  ❌ " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
