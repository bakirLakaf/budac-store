"""
اختبارات أمنية — كل واحد يمثّل ثغرة حقيقية تصلّحت.

⚠️ القاعدة: كل اختبار هنا لازم **يفشل** على الكود القديم.
اختبار ينجح فالحالتين ما يحمي من والو.
"""

import importlib
import os

import pytest


# ══════════════════════════════════════════════════════════════
# C-1 — SECRET_KEY
# ══════════════════════════════════════════════════════════════

def test_production_refuses_to_start_without_secret_key(monkeypatch):
    """
    الثغرة: كان SECRET_KEY يسقط لـ"dev-secret-key-change-me".
    نسيان المتغيّر فالإنتاج = أي واحد يزوّر كوكي أدمن ويدخل.

    المتوقّع: التطبيق **ما يقلعش** أصلاً.
    """
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("FLASK_ENV", "production")

    import config as config_module
    importlib.reload(config_module)

    with pytest.raises(config_module.ConfigError) as excinfo:
        config_module.Config.validate()

    assert "SECRET_KEY" in str(excinfo.value)


def test_production_refuses_known_default_secret(monkeypatch):
    """حتى لو حط أحد المفتاح القديم صراحة، نرفضوه."""
    monkeypatch.setenv("SECRET_KEY", "dev-secret-key-change-me")
    monkeypatch.setenv("FLASK_ENV", "production")

    import config as config_module
    importlib.reload(config_module)

    with pytest.raises(config_module.ConfigError):
        config_module.Config.validate()


def test_production_refuses_short_secret(monkeypatch):
    """مفتاح قصير = قابل للتخمين بالقوة الغاشمة."""
    monkeypatch.setenv("SECRET_KEY", "abc123")
    monkeypatch.setenv("FLASK_ENV", "production")

    import config as config_module
    importlib.reload(config_module)

    with pytest.raises(config_module.ConfigError):
        config_module.Config.validate()


def test_development_still_works_without_secret(monkeypatch):
    """⚠️ التشديد ما يخصّوش يكسر التطوير المحلي."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("FLASK_ENV", "development")

    import config as config_module
    importlib.reload(config_module)

    resolved = config_module.Config.validate()
    assert resolved["SECRET_KEY"]


def test_forged_session_cannot_reach_admin(app, seed):
    """
    الثغرة الأصلية: كوكي جلسة مزوّر بالمفتاح الافتراضي كان يعطي
    وصولاً كاملاً للوحة التحكم (أثبتناه بـPoC).

    درك المفتاح عشوائي، فكوكي موقّع بالمفتاح القديم لازم يترفض.
    """
    from flask import Flask
    from flask.sessions import SecureCookieSessionInterface

    # نبنيو تطبيق Flask حقيقيًا بالمفتاح الافتراضي القديم — بالضبط
    # كيما كان المهاجم يدير: يوقّع كوكي بمفتاح يعرفو.
    attacker_app = Flask(__name__)
    attacker_app.secret_key = "dev-secret-key-change-me"

    serializer = SecureCookieSessionInterface().get_signing_serializer(attacker_app)
    forged = serializer.dumps({"admin_id": seed["admin_id"], "admin_username": "hacker"})

    client = app.test_client()
    client.set_cookie("session", forged, domain="localhost")

    response = client.get("/admin/")
    assert response.status_code == 302, "الكوكي المزوّر دخل للوحة التحكم!"
    assert "/admin/login" in response.headers["Location"]


def test_all_admin_routes_require_login(app):
    """
    ⚠️ اختبار وقائي: أي مسار أدمن جديد بلا @login_required
    يفشل هنا فورًا، بدل ما يوصل للإنتاج مفتوحًا.
    """
    public = {"admin.login", "admin.logout", "admin.static"}
    client = app.test_client()

    unprotected = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/admin"):
            continue
        if rule.endpoint in public:
            continue
        if "GET" not in rule.methods:
            continue
        if rule.arguments:
            continue

        response = client.get(rule.rule)
        if response.status_code == 200:
            unprotected.append(rule.rule)

    assert not unprotected, f"مسارات أدمن مفتوحة: {unprotected}"


# ══════════════════════════════════════════════════════════════
# H-1 — IDOR على صفحة الشكر
# ══════════════════════════════════════════════════════════════

def _make_order(app, number="BD-11112222", total=4500):
    from models import db, Order
    with app.app_context():
        order = Order(
            order_number=number, customer_name="علي بن علي",
            customer_phone="0666255200", wilaya_code="39", wilaya_name="الوادي",
            subtotal=total - 500, shipping_price=500, total=total,
            status=Order.STATUS_PENDING,
        )
        db.session.add(order)
        db.session.commit()
        return number


def test_thanks_page_hides_orders_from_other_sessions(client, app, seed):
    """
    🔴 الثغرة المؤكدة: /lp/<slug>/thanks?order=BD-XXXXXX كانت تعرض
    مبالغ **أي** طلبية بلا تحقق. مع أرقام 6 خانات وبلا حد معدّل،
    مهاجم يعدّ كل طلبيات المتجر ويقرا رقم أعمالك.
    """
    number = _make_order(app, "BD-11112222", total=4500)

    response = client.get(f"/lp/{seed['page_slug']}/thanks?order={number}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert number not in body, "رقم الطلبية بان لجلسة ماشي صاحبتها!"
    assert "4,500" not in body, "مبلغ الطلبية بان لجلسة ماشي صاحبتها!"


def test_thanks_page_shows_order_to_its_owner(client, app, seed):
    """⚠️ التشديد ما يخصّوش يكسر التدفّق العادي للزبون."""
    number = _make_order(app, "BD-33334444", total=4500)

    # نحاكيو أن هاد المتصفح دار الطلبية
    with client.session_transaction() as sess:
        sess["my_order_numbers"] = [number]

    body = client.get(
        f"/lp/{seed['page_slug']}/thanks?order={number}"
    ).get_data(as_text=True)

    assert number in body, "صاحب الطلبية ما شافش طلبيتو!"


def test_thanks_page_survives_missing_and_bogus_order(client, seed):
    """رقم غالط ولا بلا رقم = صفحة عادية، ماشي خطأ 500."""
    assert client.get(f"/lp/{seed['page_slug']}/thanks").status_code == 200
    assert client.get(
        f"/lp/{seed['page_slug']}/thanks?order=BD-00000000"
    ).status_code == 200


def test_thanks_page_gives_no_existence_oracle(client, app, seed):
    """
    ⚠️ دقيقة لكن مهمة: لو رجّعنا 404 للطلبيات اللي ماشي تاعنا،
    الرد نفسو يولّي مؤشرًا (404 = ماكانش · 200 = موجود) وترجع
    ثغرة التعداد من باب آخر. الردّان لازم يكونو متطابقين.
    """
    real = _make_order(app, "BD-55556666")

    existing = client.get(f"/lp/{seed['page_slug']}/thanks?order={real}")
    missing = client.get(f"/lp/{seed['page_slug']}/thanks?order=BD-99998888")

    assert existing.status_code == missing.status_code
    assert len(existing.get_data()) == len(missing.get_data())


def test_order_success_requires_session_ownership(client, app):
    """نفس القاعدة على /order/success — ما نصلّحوش مسارًا وننساو خاه."""
    number = _make_order(app, "BD-77778888")
    assert client.get(f"/order/success/{number}").status_code == 404


# ══════════════════════════════════════════════════════════════
# Open Redirect
# ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("target,expected", [
    ("/admin/orders", True),
    ("/", True),
    ("/admin/products?page=2", True),
    ("//evil.com", False),          # المتصفح يقراها https://evil.com
    ("/\\evil.com", False),
    ("https://evil.com", False),
    ("http://evil.com", False),
    ("javascript:alert(1)", False),
    ("", False),
    (None, False),
])
def test_redirect_target_validation(app, target, expected):
    """الفحص القديم startswith('/') كان يمرّر //evil.com."""
    from routes.admin import _is_safe_redirect
    assert _is_safe_redirect(target) is expected


# ══════════════════════════════════════════════════════════════
# CSRF
# ══════════════════════════════════════════════════════════════

def test_state_changing_post_rejected_without_csrf_token(csrf_client, app):
    """POST بلا توكن لازم يترفض — طبقة أساسية ما تتكسرش بالغلط."""
    _make_order(app, "BD-12341234")
    response = csrf_client.post(
        "/my-orders/BD-12341234/cancel", data={"phone": "0666255200"}
    )
    assert response.status_code == 400


# ══════════════════════════════════════════════════════════════
# XSS
# ══════════════════════════════════════════════════════════════

def test_malicious_product_name_is_escaped_in_html(client, app):
    """
    اسم منتج فيه HTML يجي من استيراد YouCan/Excel — بيانات خارجية.
    لازم يبان كنص، ماشي يتنفّذ.
    """
    from models import db, Product

    payload = '<img src=x onerror=alert(1)>'
    with app.app_context():
        db.session.add(Product(
            name=payload, slug="xss-test", price=100, is_active=True,
        ))
        db.session.commit()

    body = client.get("/product/xss-test").get_data(as_text=True)
    assert "<img src=x onerror=" not in body
    assert "&lt;img" in body


def test_products_api_escapes_nothing_raw(client, app):
    """
    الـAPI اللي يغذّي «شفتها مؤخرًا» يرجّع JSON — التهريب مسؤولية
    JavaScript. نتأكدو برك أن الـAPI ما يطيحش على اسم فارغ
    (كان p.name[0] يرمي IndexError).
    """
    from models import db, Product

    with app.app_context():
        db.session.add(Product(name="", slug="empty-name", price=100, is_active=True))
        db.session.commit()

    response = client.get("/api/products-by-slug?slugs=empty-name")
    assert response.status_code == 200


def test_js_has_single_shared_escape_helper():
    """
    ⚠️ اختبار regression للسبب الجذري: الثغرة صرات لأن escapeHtml
    كانت مكرّرة فكل وحدة، ووحدة نسيتها. نتأكدو أنها وحدة ومشتركة،
    وأن الروابط تمرّ من safeUrl (escapeHtml ما تسدّش javascript:).
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "js", "site.js",
    )
    source = open(path, encoding="utf-8").read()

    assert source.count("function escapeHtml") == 1, "escapeHtml مكرّرة — نفس سبب الثغرة"
    assert "BudacSafe" in source
    assert "safeUrl(p.url)" in source
    assert "safeUrl(p.image)" in source
    assert "escapeHtml(p.name)" in source


def test_safe_url_rejects_dangerous_schemes():
    """نفس منطق safeUrl تاع JavaScript، متحقّق منّو هنا."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "js", "site.js",
    )
    source = open(path, encoding="utf-8").read()
    # الشرط الحرج: يقبل غير اللي يبدا بـ/ وماشي //
    assert "trimmed.charAt(0) !== '/'" in source
    assert "trimmed.charAt(1) === '/'" in source


# ══════════════════════════════════════════════════════════════
# تسريب الأخطاء
# ══════════════════════════════════════════════════════════════

def test_order_failure_does_not_leak_internals(app):
    """
    رسالة SQLAlchemy فيها جملة SQL بأسماء الجداول والقيم.
    الزبون ما يخصّوش يشوفها.
    """
    from services.orders import create_order, OrderError
    from services.cart import CartLine

    with app.app_context():
        line = CartLine(
            key="product:999999:0", kind="product", name="غير موجود",
            unit_price=100, quantity=1, product_id=999999,
        )
        with pytest.raises(OrderError) as excinfo:
            create_order(
                lines=[line],
                customer_data={
                    "name": "علي", "phone": "0666255200",
                    "wilaya_code": "39", "wilaya_name": "الوادي",
                    "commune": "الوادي", "address_note": None,
                },
                shipping_info={
                    "carrier_code": "zrexpress", "carrier_name": "ZR",
                    "delivery_type": "home", "price": 600,
                },
            )

    message = str(excinfo.value)
    for leak in ("SELECT", "INSERT", "Traceback", "sqlalchemy", "orders."):
        assert leak not in message, f"تسريب داخلي فرسالة الزبون: {leak}"
