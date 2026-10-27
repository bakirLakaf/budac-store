"""
تجهيزات مشتركة للاختبارات.

⚠️ مبدأ: كل اختبار يبدا بقاعدة بيانات نظيفة فملف مؤقت. ما نشاركوش
حالة بين الاختبارات — اختبار يعتمد على ترتيب تشغيلو هو اختبار كذّاب.
"""

import os
import secrets
import sys

import pytest

# نخلّيو جذر المشروع فمسار الاستيراد باش pytest يلقى app/models
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_SECRET = "test-" + secrets.token_hex(24)


@pytest.fixture
def app(tmp_path, monkeypatch):
    """تطبيق Flask جاهز للاختبار، بقاعدة بيانات SQLite مؤقتة."""
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("FLASK_ENV", "")
    monkeypatch.setenv("UPLOAD_ROOT", str(tmp_path / "uploads"))

    # ⚠️ نعاودو استيراد config: القيم تتحسب وقت الاستيراد، وبلا
    # إعادة التحميل الاختبار ياخذ إعدادات اختبار سابق.
    import importlib
    import config as config_module
    importlib.reload(config_module)

    import app as app_module
    importlib.reload(app_module)

    application = app_module.create_app()
    application.config["TESTING"] = True

    # ⚠️ ذاكرة تجميع الزيارات عامة على مستوى الوحدة — لازم تتفرّغ
    # بين الاختبارات وإلا الاختبار الثاني يظن أن الكتابة صرات خلاص.
    from services.analytics import reset_pending
    reset_pending()

    from models import db
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()


@pytest.fixture
def client(app):
    """عميل HTTP بلا حماية CSRF — للاختبارات اللي ما تخصّش CSRF."""
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_client()


@pytest.fixture
def csrf_client(app):
    """عميل بحماية CSRF شغّالة — لاختبار الحماية نفسها."""
    app.config["WTF_CSRF_ENABLED"] = True
    return app.test_client()


@pytest.fixture
def seed(app):
    """
    بيانات أساسية: تصنيف، منتج، صفحة هبوط، حساب أدمن.
    يرجّع قاموسًا بالمعرّفات (ماشي كائنات — تولّي منفصلة عن الجلسة).
    """
    from models import (
        db, Category, Product, LandingPage, AdminUser, ShippingRate, Carrier,
    )

    with app.app_context():
        category = Category(name="ديكور", slug="decor")
        db.session.add(category)
        db.session.flush()

        product = Product(
            name="شريط كريستال", slug="chrit-crystal",
            price=1200, is_active=True, category_id=category.id,
        )
        db.session.add(product)
        db.session.flush()

        page = LandingPage(
            slug="promo", internal_name="حملة سبتمبر", headline="عرض خاص",
            product_id=product.id, is_active=True,
        )
        db.session.add(page)

        admin = AdminUser(username="admin")
        admin.set_password("Str0ngTestPass!2026")
        db.session.add(admin)

        # شركة توصيل + سعر لولاية الوادي، باش الـcheckout يخدم
        carrier = Carrier(name="ZR Express", code="zrexpress", is_active=True)
        db.session.add(carrier)
        db.session.flush()

        db.session.add(ShippingRate(
            carrier_id=carrier.id,
            wilaya_code="39", wilaya_name="الوادي",
            home_price=600, office_price=400,
        ))

        db.session.commit()

        return {
            "carrier_id": carrier.id,
            "carrier_code": carrier.code,
            "product_id": product.id,
            "product_slug": product.slug,
            "page_slug": page.slug,
            "admin_id": admin.id,
            "admin_username": "admin",
            "admin_password": "Str0ngTestPass!2026",
        }


@pytest.fixture
def logged_in_admin(client, seed):
    """عميل داخل للوحة التحكم."""
    client.post("/admin/login", data={
        "username": seed["admin_username"],
        "password": seed["admin_password"],
    }, follow_redirects=True)
    return client
