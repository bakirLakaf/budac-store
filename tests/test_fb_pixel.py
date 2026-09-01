# -*- coding: utf-8 -*-
"""
اختبار بيكسل فيسبوك.

⚠️ علاش هاد الاختبار موجود؟

كود تحميل البيكسل كان **منسوخ 3 مرات** (base · landing/page ·
landing/thanks). حوّلناه لماكرو واحد فـ`templates/partials/fb_pixel.html`.

عطب فالبيكسل **ما يبانش**: الصفحة تتفتح عادي، الزبون يشري عادي،
وأنت تكتشف المشكل بعد أسبوعين كي تلقى أرقام الحملة غالطة وفلوس
الإعلانات اتصرفت على بيانات ناقصة.

هاد الاختبار يثبّت السلوك:
  • نسخة **وحدة** من كود التحميل فكل صفحة (نسختين = أحداث مضاعفة)
  • `Purchase` يتسجّل **غير** كي تكون طلبية حقيقية
  • ماكانش البيكسل معمّر → ما يتطبع حتى شي

التشغيل: python tests/test_fb_pixel.py
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "test-key-for-testing-only")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

PIXEL_ID = "1234567890"

PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name
          + ("  → " + str(detail) if not ok and detail != "" else ""))


def seed(app):
    """منتج + صفحة هبوط + طلبية."""
    from models import db, Product, Category, LandingPage, Order

    with app.app_context():
        db.create_all()

        category = Category(name="تجربة", slug="t")
        db.session.add(category)
        db.session.flush()

        product = Product(name="منتج تجربة", slug="p-test", price=1500,
                          category_id=category.id, is_active=True)
        db.session.add(product)
        db.session.flush()

        db.session.add(LandingPage(
            slug="promo", internal_name="تجربة", product_id=product.id,
            headline="عرض", cta_text="اطلب", is_active=True,
        ))
        db.session.add(Order(
            order_number="BD-TEST", customer_name="تجربة",
            customer_phone="0555000000", wilaya_code="09",
            wilaya_name="البليدة", total=1500,
        ))
        db.session.commit()


def main():
    print("\n" + "=" * 60)
    print("بيكسل فيسبوك")
    print("=" * 60)

    # ───────── البيكسل معمّر ─────────
    os.environ["FACEBOOK_PIXEL_ID"] = PIXEL_ID

    from app import create_app
    app = create_app()
    seed(app)
    client = app.test_client()

    pages = {
        "الصفحة الرئيسية": "/",
        "صفحة الهبوط": "/lp/promo",
        "صفحة الشكر": "/lp/promo/thanks?order=BD-TEST",
    }

    print("\n[1] كود التحميل — نسخة وحدة برك")
    for label, url in pages.items():
        body = client.get(url).get_data(as_text=True)

        # ⚠️ نسختين = fbq يتحمّل مرتين = كل حدث يتسجّل مرتين،
        # وأرقام الحملة تتضاعف غلط.
        count = body.count("connect.facebook.net")
        check(f"{label}: نسخة وحدة", count == 1, count)

        check(f"{label}: init بالمعرّف الصحيح",
              f"fbq('init', '{PIXEL_ID}')" in body)
        check(f"{label}: PageView", "fbq('track', 'PageView')" in body)

    print("\n[2] الأحداث الخاصة بكل صفحة")
    landing = client.get("/lp/promo").get_data(as_text=True)
    check("صفحة الهبوط: ViewContent", "fbq('track', 'ViewContent'" in landing)
    check("صفحة الهبوط: ماكانش Purchase",
          "fbq('track', 'Purchase'" not in landing)

    thanks = client.get("/lp/promo/thanks?order=BD-TEST").get_data(as_text=True)
    check("صفحة الشكر: Purchase", "fbq('track', 'Purchase'" in thanks)
    check("صفحة الشكر: المبلغ الصحيح", "value: 1500" in thanks)
    check("صفحة الشكر: العملة DZD", "currency: 'DZD'" in thanks)

    home = client.get("/").get_data(as_text=True)
    check("الرئيسية: ماكانش Purchase", "fbq('track', 'Purchase'" not in home)

    print("\n[3] بلا طلبية → ماكانش Purchase")
    # ⚠️ لو اتسجّل Purchase هنا، كل زيارة لصفحة الشكر تتحسب بيعة —
    # وفيسبوك يحسّن الحملة على أرقام كاذبة.
    no_order = client.get("/lp/promo/thanks").get_data(as_text=True)
    check("ماكانش Purchase", "fbq('track', 'Purchase'" not in no_order)
    check("PageView مازال يتسجّل", "fbq('track', 'PageView')" in no_order)

    print("\n[4] البيكسل مطفي → ما يتطبع والو")
    os.environ.pop("FACEBOOK_PIXEL_ID", None)

    import importlib
    import config as config_module
    importlib.reload(config_module)

    import app as app_module
    importlib.reload(app_module)

    off_app = app_module.create_app()
    seed(off_app)
    off_client = off_app.test_client()

    for label, url in pages.items():
        body = off_client.get(url).get_data(as_text=True)
        check(f"{label}: ماكانش fbq", "fbq(" not in body)
        check(f"{label}: ماكانش سكريبت فيسبوك",
              "connect.facebook.net" not in body)

    print("\n" + "=" * 60)
    print(f"نجح: {len(PASSED)}  ·  فشل: {len(FAILED)}")
    print("=" * 60)
    if FAILED:
        for f in FAILED:
            print("  ❌ " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
