"""
اختبارات انحدار — التدفّقات العادية لازم تبقى تخدم بعد الإصلاحات.

⚠️ هاد الملف أهم من اختبارات الأمان بمعنى واحد: الإصلاح الأمني
اللي يكسر البيع أسوأ من الثغرة. كل تدفّق يجيب فلوس ولا يستعملو
الأدمن يوميًا لازم يكون هنا.
"""

import io

import pytest

from models import db, Product, Order, Review, SiteVisit


# ══════════════════════════════════════════════════════════════
# صفحات المتجر
# ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("path", [
    "/", "/cart", "/track", "/my-orders", "/search?q=شريط",
    "/sitemap.xml", "/robots.txt", "/admin/login",
])
def test_public_pages_render(client, seed, path):
    assert client.get(path).status_code == 200


def test_product_page_renders(client, seed):
    assert client.get(f"/product/{seed['product_slug']}").status_code == 200


def test_landing_page_renders(client, seed):
    assert client.get(f"/lp/{seed['page_slug']}").status_code == 200


def test_homepage_category_filter_still_works(client, app, seed):
    """
    ⚠️ الفلترة بالتصنيف ولّات من السيرفر (?category=) بدل الفلترة
    الفورية بـJavaScript — هاذي ماتت مع الترقيم لأن المنتجات ماشي
    كلها فالصفحة. لازم تبقى تخدم للروابط المباشرة وللزواحف.
    """
    assert client.get("/").status_code == 200
    assert client.get("/?category=decor").status_code == 200

    body = client.get("/?category=decor").get_data(as_text=True)
    assert "شريط كريستال" in body


def test_homepage_stays_bounded_on_a_huge_catalog(app, seed):
    """
    ⚠️ هاد الاختبار تبدّل مرتين مع الطراز، وهذا مقصود — يقيس
    السلوك اللي يهمّ حقًّا، ماشي تفاصيل التنفيذ:

      v139: استعلامان يحمّلو الكتالوج **كامل** مرتين
      v141: ترقيم من السيرفر — 9 منتجات، لكن كل نقرة إعادة تحميل
      v142: ترقيم فالمتصفح — الكتالوج كامل مرة وحدة، تبديل فوري
            **مع حارس**: فوق MAX_CLIENT_PAGINATION نرجعو للسيرفر

    اللي نحميوه درك: كتالوج ضخم ما يخصّوش يبعث كلشي للمتصفح.
    """
    from models import Product
    from routes.shop import MAX_CLIENT_PAGINATION, PRODUCTS_PER_PAGE
    from flask import template_rendered

    with app.app_context():
        for i in range(MAX_CLIENT_PAGINATION + 20):
            db.session.add(Product(
                name=f"منتج ضخم {i}", slug=f"bulk-{i}",
                price=100, is_active=True,
            ))
        db.session.commit()
        total = Product.query.filter_by(is_active=True).count()

    captured = []

    def record(sender, template, context, **extra):
        if template.name == "shop/home.html":
            captured.append(context)

    template_rendered.connect(record, app)
    try:
        app.test_client().get("/")
    finally:
        template_rendered.disconnect(record, app)

    context = captured[0]
    assert total > MAX_CLIENT_PAGINATION
    assert context["client_paginated"] is False, "الحارس ما اشتغلش"
    assert len(context["products"]) == PRODUCTS_PER_PAGE, (
        "كتالوج ضخم مازال يتبعث كامل للمتصفح"
    )


def test_small_catalog_is_sent_whole_for_instant_switching(app, seed):
    """
    الوجه الثاني: كتالوج صغير (الوضع العادي) يتبعث كامل — هو
    اللي يخلّي تبديل الصفحات فوريًا بلا إعادة تحميل.
    """
    from models import Product
    from flask import template_rendered

    with app.app_context():
        for i in range(30):
            db.session.add(Product(
                name=f"منتج {i}", slug=f"small-{i}", price=100, is_active=True,
            ))
        db.session.commit()
        total = Product.query.filter_by(is_active=True).count()

    captured = []

    def record(sender, template, context, **extra):
        if template.name == "shop/home.html":
            captured.append(context)

    template_rendered.connect(record, app)
    try:
        app.test_client().get("/")
    finally:
        template_rendered.disconnect(record, app)

    context = captured[0]
    assert context["client_paginated"] is True
    assert len(context["products"]) == total


# ══════════════════════════════════════════════════════════════
# السلة والشراء
# ══════════════════════════════════════════════════════════════

def test_full_checkout_flow(client, app, seed):
    """التدفّق الكامل: زيد للسلة → اشتري → صفحة النجاح."""
    add = client.post("/cart/add", data={
        "kind": "product", "item_id": seed["product_id"], "quantity": "2",
    })
    assert add.status_code in (200, 302)

    cart = client.get("/api/cart").get_json()
    # ⚠️ "count" = عدد الأسطر فالسلة، ماشي عدد القطع
    assert cart["count"] == 1
    assert cart["lines"][0]["quantity"] == 2
    assert cart["subtotal"] == 2400

    checkout = client.post("/checkout", data={
        "name": "علي بن علي", "phone": "0666255200",
        "wilaya_code": "39", "commune": "الوادي",
        "carrier_code": "zrexpress", "delivery_type": "home",
    })
    assert checkout.status_code == 302

    with app.app_context():
        order = Order.query.first()
        assert order is not None
        assert order.total == 3000          # 2400 + 600
        number = order.order_number

    # صاحب الطلبية يشوف صفحة النجاح
    assert client.get(f"/order/success/{number}").status_code == 200


def test_cart_update_and_remove(client, seed):
    client.post("/cart/add", data={
        "kind": "product", "item_id": seed["product_id"], "quantity": "1",
    })
    cart = client.get("/api/cart").get_json()
    key = cart["lines"][0]["key"]

    client.post("/api/cart/update", json={"key": key, "quantity": 3})
    assert client.get("/api/cart").get_json()["lines"][0]["quantity"] == 3

    client.post("/api/cart/update", json={"key": key, "quantity": 0})
    assert client.get("/api/cart").get_json()["count"] == 0


def test_shipping_price_api(client, seed):
    response = client.get(
        "/api/shipping-price?carrier=zrexpress&wilaya=39&type=home"
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["price"] == 600, data


# ══════════════════════════════════════════════════════════════
# تتبّع الطلبية وإلغاؤها
# ══════════════════════════════════════════════════════════════

def _place_order(client, seed):
    client.post("/cart/add", data={
        "kind": "product", "item_id": seed["product_id"], "quantity": "1",
    })
    client.post("/checkout", data={
        "name": "علي بن علي", "phone": "0666255200",
        "wilaya_code": "39", "commune": "الوادي",
        "carrier_code": "zrexpress", "delivery_type": "home",
    })


def test_order_tracking_with_correct_credentials(client, app, seed):
    _place_order(client, seed)
    with app.app_context():
        number = Order.query.first().order_number

    body = client.post("/track", data={
        "order_number": number, "phone": "0666255200",
    }, follow_redirects=True).get_data(as_text=True)
    assert number in body


def test_order_tracking_rejects_wrong_phone(client, app, seed):
    _place_order(client, seed)
    with app.app_context():
        number = Order.query.first().order_number

    body = client.post("/track", data={
        "order_number": number, "phone": "0555111222",
    }, follow_redirects=True).get_data(as_text=True)
    assert number not in body


def test_customer_can_cancel_pending_order(client, app, seed):
    _place_order(client, seed)
    with app.app_context():
        number = Order.query.first().order_number

    client.post(f"/my-orders/{number}/cancel",
                data={"phone": "0666255200"}, follow_redirects=True)

    with app.app_context():
        assert Order.query.first().status == Order.STATUS_CANCELLED


def test_customer_cannot_cancel_someone_elses_order(client, app, seed):
    _place_order(client, seed)
    with app.app_context():
        number = Order.query.first().order_number

    client.post(f"/my-orders/{number}/cancel",
                data={"phone": "0555999888"}, follow_redirects=True)

    with app.app_context():
        assert Order.query.first().status == Order.STATUS_PENDING


# ══════════════════════════════════════════════════════════════
# التقييمات
# ══════════════════════════════════════════════════════════════

def test_review_submission_and_moderation(client, app, seed):
    """التقييم يتسجّل غير معتمد — ما يبانش قبل موافقة الأدمن."""
    client.post("/reviews", data={
        "author_name": "فاطمة", "rating": "5", "comment": "خدمة مليحة بزاف",
    }, follow_redirects=True)

    with app.app_context():
        review = Review.query.first()
        assert review is not None
        assert review.is_approved is False


# ══════════════════════════════════════════════════════════════
# لوحة التحكم
# ══════════════════════════════════════════════════════════════

def test_admin_login_and_dashboard(client, seed):
    response = client.post("/admin/login", data={
        "username": seed["admin_username"],
        "password": seed["admin_password"],
    }, follow_redirects=True)
    assert response.status_code == 200
    assert client.get("/admin/").status_code == 200


def test_admin_login_rejects_wrong_password(client, seed):
    client.post("/admin/login", data={
        "username": seed["admin_username"], "password": "wrong",
    })
    assert client.get("/admin/").status_code == 302


@pytest.mark.parametrize("path", [
    "/admin/", "/admin/orders", "/admin/products", "/admin/coupons",
    "/admin/reviews", "/admin/shipping", "/admin/settings",
])
def test_admin_pages_render_for_logged_in_admin(logged_in_admin, path):
    assert logged_in_admin.get(path).status_code == 200


def test_admin_can_change_order_status(logged_in_admin, app, seed):
    with app.app_context():
        order = Order(
            order_number="BD-40004000", customer_name="علي",
            customer_phone="0666255200", wilaya_code="39", wilaya_name="الوادي",
            subtotal=1200, shipping_price=600, total=1800,
            status=Order.STATUS_PENDING,
        )
        db.session.add(order)
        db.session.commit()
        order_id = order.id

    logged_in_admin.post(f"/admin/orders/{order_id}/status",
                         data={"status": Order.STATUS_CONFIRMED},
                         follow_redirects=True)

    with app.app_context():
        assert db.session.get(Order, order_id).status == Order.STATUS_CONFIRMED


def test_admin_action_is_written_to_audit_log(logged_in_admin, app, seed):
    """سجل التدقيق يسجّل الدخول فعلاً."""
    from models import AdminAction

    with app.app_context():
        logins = AdminAction.query.filter_by(action=AdminAction.LOGIN).count()
        assert logins >= 1


# ══════════════════════════════════════════════════════════════
# Excel
# ══════════════════════════════════════════════════════════════

def test_excel_export_then_import_roundtrip(logged_in_admin, app, seed):
    """
    ⚠️ بدّلنا الاستيراد لوضع read_only — القراءة بالبثّ ما تدعمش
    sheet[1]. هاد الاختبار يتأكد أن الدورة الكاملة مازالت تخدم.
    """
    export = logged_in_admin.get("/admin/products/export")
    assert export.status_code == 200
    assert len(export.data) > 1000

    upload = logged_in_admin.post(
        "/admin/products/import",
        data={"file": (io.BytesIO(export.data), "products.xlsx")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert upload.status_code == 200
    assert "فشل الاستيراد" not in upload.get_data(as_text=True)


def test_excel_export_neutralises_formula_injection(app, seed):
    """
    اسم منتج يبدا بـ= يتنفّذ كصيغة كي يفتح الأدمن الملف.
    الأسماء تجي من استيراد YouCan — بيانات خارجية.
    """
    from openpyxl import load_workbook
    from services.excel_io import export_workbook

    with app.app_context():
        product = db.session.get(Product, seed["product_id"])
        product.name = '=HYPERLINK("http://evil.com","اضغط")'
        db.session.commit()

        stream = export_workbook()

    workbook = load_workbook(stream)
    found = False
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and "HYPERLINK" in cell.value:
                    found = True
                    assert cell.value.startswith("'"), "الصيغة ما اتحيّدتش"
    assert found, "ما لقيناش الخانة — الاختبار ما فحص والو"


def test_safe_cell_keeps_numbers_intact():
    """
    ⚠️ الحماية تخصّ النصوص برك — لو حوّلنا الأرقام لنصوص،
    تنكسر الحسابات والفرز فالملف وينكسر الاستيراد.
    """
    from services.excel_io import _safe_cell

    assert _safe_cell(1200) == 1200
    assert _safe_cell(None) is None
    assert _safe_cell(True) is True
    assert _safe_cell("شريط عادي") == "شريط عادي"
    assert _safe_cell("=1+1") == "'=1+1"
    assert _safe_cell("@SUM(A1)") == "'@SUM(A1)"


# ══════════════════════════════════════════════════════════════
# تتبّع الزوار
# ══════════════════════════════════════════════════════════════

def test_spoofed_forwarded_header_cannot_flood_visits(client, app, seed):
    """
    🔴 كانت analytics تقرا X-Forwarded-For خامًا وتاخذ **أول** قيمة —
    واللي هي بالضبط الجزء اللي يكتبو العميل. فمهاجم يبدّلها فكل طلب
    → بصمة جديدة → صف جديد → تضخّم جدول بلا حد.

    ⚠️ نحاكيو البروكسي كيما يخدم فعلاً: Railway **يزيد** العنوان
    الحقيقي اللي شافو فآخر السلسلة. ProxyFix(x_for=1) ياخذ آخر
    قيمة — يعني الحقيقية — ويحطّها فـremote_addr.

    فالمهاجم يقدر يكتب اللي يحب فبداية السلسلة، وما يتبدّل والو.
    """
    real_client_ip = "197.200.10.55"

    for i in range(30):
        client.get("/", headers={
            # الجزء الأول من العميل (مزوّر) + العنوان الحقيقي اللي
            # يزيدو البروكسي فالآخر
            "X-Forwarded-For": f"10.0.0.{i}, {real_client_ip}",
        })

    with app.app_context():
        assert SiteVisit.query.count() <= 2, (
            "الهيدر المزوّر مازال يولّد صفوفًا — البصمة تتبدّل مع كل طلب"
        )


def test_visitor_identity_uses_proxyfix_not_raw_header(app, seed):
    """
    ⚠️ اختبار للسبب الجذري: نتأكدو أن الكود ما يقراش الهيدر الخام.

    مهم يتوثّق: أمان هاد الجزء **مشروط** بأن `x_for` فـProxyFix
    يطابق عدد البروكسيات الحقيقية. Railway = بروكسي واحد = x_for=1.
    لو زدت CDN قدّامو (Cloudflare مثلاً)، لازم x_for=2 — وإلا
    ترجع الثغرة من باب آخر.
    """
    import inspect
    from services import analytics

    source = inspect.getsource(analytics._visitor_key)
    assert "X-Forwarded-For" not in source or "request.remote_addr" in source
    assert "request.remote_addr" in source

    # وProxyFix مركّب فعلاً بعدد بروكسي واحد (Railway)
    import app as app_module
    app_source = inspect.getsource(app_module.create_app)
    assert "ProxyFix" in app_source
    assert "x_for=1" in app_source


def test_visits_are_still_counted(client, app, seed):
    """⚠️ الإصلاح ما يخصّوش يعطّل العدّاد كليًا."""
    client.get("/")
    with app.app_context():
        assert SiteVisit.query.count() >= 1


# ══════════════════════════════════════════════════════════════
# تحديد معدّل الطلبات
# ══════════════════════════════════════════════════════════════

def test_rate_limits_are_actually_registered(app):
    """
    ⚠️ الحدود تتطبّق بتبديل app.view_functions بعد تسجيل المسارات.
    لو الآلية ما خدماتش، الحدود تبان مكتوبة وما تشتغل والو —
    وهذا أسوأ من غيابها (أمان وهمي).
    """
    limiter = app.extensions.get("budac_limiter")
    assert limiter is not None, "المحدِّد ما اتركّبش"

    limited = [
        endpoint for endpoint in app.view_functions
        if endpoint in {
            "cart.checkout", "landing.submit", "landing.thanks",
            "shop.add_store_review", "cart.my_orders_lookup", "shop.search",
        }
    ]
    assert len(limited) >= 6, f"مسارات ناقصة: {limited}"

    assert limiter.enabled


def test_rate_limit_blocks_excessive_review_spam(app, seed):
    """5 تقييمات فالساعة — السادس يترفض بـ429."""
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()

    codes = []
    for i in range(9):
        response = client.post("/reviews", data={
            "author_name": f"زائر {i}", "rating": "5",
            "comment": "تعليق تجريبي طويل شوية",
        })
        codes.append(response.status_code)

    assert 429 in codes, f"الحد ما اشتغلش: {codes}"


# ══════════════════════════════════════════════════════════════
# السابق/التالي للطلبيات
# ══════════════════════════════════════════════════════════════

def test_order_neighbours_navigation(logged_in_admin, app, seed):
    """صفحة الطلبية تعرض روابط السابق/التالي صحيحة حسب الترتيب."""
    from datetime import datetime, timedelta

    with app.app_context():
        base = datetime.utcnow()
        order_ids = []
        for i in range(3):
            o = Order(
                order_number=f"BD-NAV{i:03d}", customer_name=f"زبون {i}",
                customer_phone="0555000000", wilaya_code="16",
                wilaya_name="الجزائر", subtotal=500, shipping_price=0,
                total=500, status=Order.STATUS_PENDING,
                created_at=base - timedelta(minutes=i),
            )
            db.session.add(o)
            db.session.flush()
            order_ids.append(o.id)
        db.session.commit()

    # الطلبية الوسطى (الثانية بالترتيب الزمني تنازليًا)
    middle_id = order_ids[1]
    resp = logged_in_admin.get(f"/admin/orders/{middle_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "neighbours-pos" in html
    assert f"BD-NAV{2:03d}" in html or f"BD-NAV{0:03d}" in html


# ══════════════════════════════════════════════════════════════
# تحديث حالة التتبّع من ZR — ما يطيحش بصفحة 500
# ══════════════════════════════════════════════════════════════

def test_refresh_tracking_handles_unexpected_zr_response(logged_in_admin, app, seed):
    """
    استثناء غير متوقع من get_parcel_status (مو ZRError) لازم يرجّع
    رسالة واضحة، ماشي صفحة 500 خام.

    ⚠️ هاد الاختبار وُجد بعد بلاغ حقيقي: زر "حدّث حالة التتبّع من ZR"
    كان يوقع صفحة 500 عامة بلا أي تفاصيل تفيد الأدمن.
    """
    from unittest.mock import patch

    with app.app_context():
        order = Order(
            order_number="BD-TRK001", customer_name="زبون تتبّع",
            customer_phone="0555000000", wilaya_code="16",
            wilaya_name="الجزائر", subtotal=500, shipping_price=0,
            total=500, status=Order.STATUS_CONFIRMED,
            tracking_number="FCQIKGY725-ZR-24",
        )
        db.session.add(order)
        db.session.commit()
        oid = order.id

    # نحاكيو استثناء غير متوقع (ماشي ZRError) — بالضبط نوع العطب
    # اللي كان يطيح 500
    with patch(
        "services.zrexpress.get_parcel_status",
        side_effect=AttributeError("'NoneType' object has no attribute 'get'"),
    ):
        resp = logged_in_admin.post(
            f"/admin/orders/{oid}/refresh-tracking", follow_redirects=True
        )

    assert resp.status_code == 200, "لازم يرجّع رسالة واضحة ماشي 500"
    html = resp.get_data(as_text=True)
    assert "خطأ غير متوقع" in html


def test_refresh_tracking_handles_malformed_status_response(logged_in_admin, app, seed):
    """رد ZR بشكل غير متوقع (None ولا بلا مفتاح state) يرجّع رسالة واضحة."""
    from unittest.mock import patch

    with app.app_context():
        order = Order(
            order_number="BD-TRK002", customer_name="زبون تتبّع 2",
            customer_phone="0555000001", wilaya_code="16",
            wilaya_name="الجزائر", subtotal=500, shipping_price=0,
            total=500, status=Order.STATUS_CONFIRMED,
            tracking_number="FCQIKGY726-ZR-25",
        )
        db.session.add(order)
        db.session.commit()
        oid = order.id

    with patch("services.zrexpress.get_parcel_status", return_value=None):
        resp = logged_in_admin.post(
            f"/admin/orders/{oid}/refresh-tracking", follow_redirects=True
        )

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "ما فيهش حالة واضحة" in html
