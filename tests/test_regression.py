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


# ══════════════════════════════════════════════════════════════
# get_parcel_status: ZR بدّلت شكل حقل state لكائن — ماشي نص
# ══════════════════════════════════════════════════════════════

def test_get_parcel_status_handles_new_zr_state_object(monkeypatch):
    """
    بلاغ إنتاج حقيقي (v148): ZR غيّرت رد /parcels/{id} — حقل `state`
    ولّى كائن {id, name, description, color, ...} بدل نص بسيط.

    الرد الحقيقي اللي جبناه من logs الإنتاج:
        {"state": {"id": "...", "name": "encaisse",
                    "description": "Encaissé", "color": "00796b", ...}}

    كان يخلّي `order.carrier_state = status["state"]` يحاول يحفظ
    dict كامل فعمود نص قصير، وpsycopg2 يرفض بـ:
        ProgrammingError: can't adapt type 'dict'
    """
    from unittest.mock import patch, MagicMock
    from services.zrexpress import get_parcel_status

    monkeypatch.setenv("ZR_TENANT_ID", "test-tenant")
    monkeypatch.setenv("ZR_API_KEY", "test-key")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.content = b"{}"
    fake_response.headers = {}
    fake_response.json.return_value = {
        "state": {
            "id": "f34dfc2c-ecfe-4e06-9a19-430d2d0b27b9",
            "name": "encaisse",
            "description": "Encaissé",
            "isBlocking": False,
            "isLocked": False,
            "visibleFor": 15,
            "editableBy": 2,
            "color": "00796b",
        }
    }

    with patch("services.zrexpress.requests.request", return_value=fake_response):
        result = get_parcel_status("FCQIKGY725-ZR-24")

    assert isinstance(result["state"], str), "state لازم يكون نص، ماشي dict"
    assert result["state"] == "Encaissé"
    assert result["state_id"] == "f34dfc2c-ecfe-4e06-9a19-430d2d0b27b9"


def test_get_parcel_status_still_supports_old_plain_string_format(monkeypatch):
    """الشكل القديم (state = نص مباشر) لازم يبقى يخدم، ماشي غير الجديد."""
    from unittest.mock import patch, MagicMock
    from services.zrexpress import get_parcel_status

    monkeypatch.setenv("ZR_TENANT_ID", "test-tenant")
    monkeypatch.setenv("ZR_API_KEY", "test-key")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.content = b"{}"
    fake_response.headers = {}
    fake_response.json.return_value = {"state": "delivered", "stateId": "xyz"}

    with patch("services.zrexpress.requests.request", return_value=fake_response):
        result = get_parcel_status("OLD-FORMAT-123")

    assert result["state"] == "delivered"
    assert result["state_id"] == "xyz"


def test_refresh_tracking_saves_real_zr_dict_response_without_crashing(
    logged_in_admin, app, seed, monkeypatch
):
    """
    اختبار شامل من الطرف للطرف بنفس البيانات الحقيقية من production
    logs — الروت كامل، مو الدالة وحدها.
    """
    from unittest.mock import patch, MagicMock

    monkeypatch.setenv("ZR_TENANT_ID", "test-tenant")
    monkeypatch.setenv("ZR_API_KEY", "test-key")

    with app.app_context():
        order = Order(
            order_number="BD-REALTRK", customer_name="زبون حقيقي",
            customer_phone="0555000002", wilaya_code="24",
            wilaya_name="قالمة", subtotal=720, shipping_price=700,
            total=1420, status=Order.STATUS_CONFIRMED,
            tracking_number="FCQIKGY725-ZR-24",
        )
        db.session.add(order)
        db.session.commit()
        oid = order.id

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.content = b"{}"
    fake_response.headers = {}
    fake_response.json.return_value = {
        "state": {
            "id": "f34dfc2c-ecfe-4e06-9a19-430d2d0b27b9",
            "name": "encaisse",
            "description": "Encaissé",
            "isBlocking": False,
            "isLocked": False,
            "visibleFor": 15,
            "editableBy": 2,
            "color": "00796b",
        }
    }

    with patch("services.zrexpress.requests.request", return_value=fake_response):
        resp = logged_in_admin.post(
            f"/admin/orders/{oid}/refresh-tracking", follow_redirects=True
        )

    assert resp.status_code == 200

    with app.app_context():
        updated = db.session.get(Order, oid)
        assert updated.carrier_state == "Encaissé"
        assert isinstance(updated.carrier_state, str)


# ══════════════════════════════════════════════════════════════
# startup._fix_null_flags: قيم BOOLEAN لازم TRUE/FALSE ماشي 0/1
# ══════════════════════════════════════════════════════════════

def test_fix_null_flags_uses_ansi_boolean_literals(app):
    """
    بلاغ إنتاج حقيقي (v148): كنا نستعملو `0`/`1` كقيم افتراضية فـSQL
    خام لتصليح أعمدة BOOLEAN اللي بقات NULL. Postgres يرفض هذا:

        DatatypeMismatch: column "is_extra" is of type boolean
        but expression is of type integer

    SQLite ساهل ويقبل 0/1 كـBOOLEAN، فالمشكل ما كانش يبان غير
    فالإنتاج (Postgres) — وكان يتكرر فكل نشر بلا ما يتصلّح أبدًا.
    """
    from sqlalchemy import text
    from startup import _fix_null_flags
    from models import Category, ProductImage, ProductVariant

    with app.app_context():
        cat = Category(name="اختبار قيم", slug="test-boolean-flags")
        db.session.add(cat)
        db.session.flush()

        product = Product(name="منتج", slug="test-boolean-flags-p",
                          price=100, category_id=cat.id, is_active=True)
        db.session.add(product)
        db.session.flush()

        db.session.add(ProductImage(product_id=product.id, image_filename="x.jpg"))
        db.session.commit()

        # ⚠️ العمود الطازج عندو NOT NULL DEFAULT FALSE، فما نقدروش
        # نحطّو NULL مباشرة. نحاكيو الحالة الواقعية (قاعدة بيانات
        # قديمة قبل ما تنزاد القيود) بإعادة بناء العمود بلا قيد.
        db.session.execute(text(
            "ALTER TABLE product_images RENAME COLUMN is_extra TO is_extra_bak"
        ))
        db.session.execute(text(
            "ALTER TABLE product_images ADD COLUMN is_extra BOOLEAN"
        ))
        db.session.commit()

        # ما يطيحش، ويصلّح القيمة فعليًا
        _fix_null_flags(db)

        fixed = db.session.execute(text(
            "SELECT is_extra FROM product_images WHERE product_id = :pid"
        ), {"pid": product.id}).scalar()

        assert fixed in (0, False), "لازم يتصلّح لـFalse، ماشي يبقى NULL"


def test_fix_null_flags_source_uses_ansi_literals_not_integers():
    """
    ⚠️ SQLite متساهل مع 0/1 كـBOOLEAN، فاختبار السلوك وحدو ما يكفيش
    يكشف عطب Postgres-specific. نتحقق مباشرة من القيم النصية
    المستعملة فـSQL — هذا هو الضمان الحقيقي ضد رجوع الخطأ:
        DatatypeMismatch: column "is_extra" is of type boolean
        but expression is of type integer
    """
    import inspect
    from startup import _fix_null_flags

    source = inspect.getsource(_fix_null_flags)

    assert '"FALSE"' in source or "'FALSE'" in source
    assert '"TRUE"' in source or "'TRUE'" in source
    # ما نلقاوش "0" ولا "1" كقيم افتراضية فداخل قائمة targets
    assert '"0"),' not in source, "لازم TRUE/FALSE ماشي 0/1 (Postgres يرفضهم)"
    assert '"1"),' not in source, "لازم TRUE/FALSE ماشي 0/1 (Postgres يرفضهم)"


# ══════════════════════════════════════════════════════════════
# create_app: لازم تخدم مهما كان شكل رابط Postgres اللي يبعثو Railway
# ══════════════════════════════════════════════════════════════

def test_create_app_works_with_psycopg3_style_database_url(monkeypatch):
    """
    بلاغ إنتاج حرج (v149): الموقع طاح بالكامل —
        ModuleNotFoundError: No module named 'psycopg'

    السبب: DATABASE_URL عند Railway صار يبدأ بـ"postgresql+psycopg://"
    (سائق النسخة 3)، وكنا مثبّتين psycopg2-binary برك. كل عامل
    (worker) كان يطيح فور الإقلاع — outage كامل، ماشي عطب جزئي.

    هذا الاختبار يتأكد أن create_app() تقدر توصل لخطوة استيراد
    السائق بلا ما تطيح، بغض النظر عن شكل الرابط.
    """
    import importlib
    import secrets

    monkeypatch.setenv("SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/fakedb"
    )

    import config
    importlib.reload(config)
    import app as app_module
    importlib.reload(app_module)

    # ما نطلبش اتصال حقيقي بقاعدة بيانات — نتأكد برك أن create_app()
    # توصل لآخرها بلا ModuleNotFoundError عند استيراد السائق (هنا
    # بالضبط كانت تطيح فالإنتاج، عند db.init_app → create_engine).
    created_app = app_module.create_app()
    assert created_app is not None


def test_create_app_works_with_psycopg2_style_database_url(monkeypatch):
    """الشكل القديم (بلا +psycopg صراحة) لازم يبقى يخدم، ماشي غير الجديد."""
    import importlib
    import secrets

    monkeypatch.setenv("SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://user:pass@localhost:5432/fakedb"
    )

    import config
    importlib.reload(config)
    import app as app_module
    importlib.reload(app_module)

    created_app = app_module.create_app()
    assert created_app is not None


# ══════════════════════════════════════════════════════════════
# v150: تحديث حالة الطلبية تلقائيًا من ZR + تعديل نجوم التقييم
# ══════════════════════════════════════════════════════════════

def test_map_zr_state_to_order_status():
    from services.zrexpress import map_state_to_order_status as m

    # «Encaissé» = ZR قبضات الكاش → مسلّمة (حالة حقيقية من الإنتاج)
    assert m("Encaissé") == "delivered"
    assert m("Livré") == "delivered"
    assert m("Retourné") == "returned"
    # «retour en cours» مازال ما رجعش
    assert m("Retour en cours") is None
    # ما نخلطوش «non livré» بـ«livré»
    assert m("Non livré") is None
    assert m("En attente") is None
    assert m("Expédié") == "shipped"
    assert m("En transit") == "shipped"
    # مكتب: يتحوّل لـ at_office غير للطلبيات نوعها مكتب
    assert m("Disponible au bureau", "office") == "at_office"
    assert m("Disponible au bureau", "home") == "shipped"
    # حالة مجهولة → ما نبدّلوش
    assert m("حاجة غريبة") is None
    assert m("") is None


def _zr_patch(label):
    from unittest.mock import patch
    return patch(
        "services.zrexpress.get_parcel_status",
        return_value={"state": label, "state_id": "x", "updated_at": None, "raw": {}},
    )


def _make_order(app, number, status, delivery_type="home"):
    with app.app_context():
        order = Order(
            order_number=number, customer_name="زبون", customer_phone="0555111222",
            wilaya_code="16", wilaya_name="الجزائر", subtotal=500,
            shipping_price=0, total=500, status=status,
            delivery_type=delivery_type, tracking_number="TRK-" + number,
        )
        db.session.add(order)
        db.session.commit()
        return order.id


def test_refresh_tracking_updates_order_status_forward(logged_in_admin, app, seed):
    oid = _make_order(app, "BD-V150A", Order.STATUS_CONFIRMED)
    with _zr_patch("Encaissé"):
        logged_in_admin.post(f"/admin/orders/{oid}/refresh-tracking",
                             follow_redirects=True)
    with app.app_context():
        o = db.session.get(Order, oid)
        assert o.carrier_state == "Encaissé"
        assert o.status == Order.STATUS_DELIVERED


def test_refresh_tracking_never_moves_status_backwards(logged_in_admin, app, seed):
    oid = _make_order(app, "BD-V150B", Order.STATUS_AT_OFFICE, "office")
    with _zr_patch("En transit"):   # shipped < at_office
        logged_in_admin.post(f"/admin/orders/{oid}/refresh-tracking",
                             follow_redirects=True)
    with app.app_context():
        assert db.session.get(Order, oid).status == Order.STATUS_AT_OFFICE


def test_refresh_tracking_leaves_delivered_and_cancelled_alone(
        logged_in_admin, app, seed):
    for number, st in (("BD-V150C", Order.STATUS_DELIVERED),
                       ("BD-V150D", Order.STATUS_CANCELLED)):
        oid = _make_order(app, number, st)
        with _zr_patch("Retourné"):
            logged_in_admin.post(f"/admin/orders/{oid}/refresh-tracking",
                                 follow_redirects=True)
        with app.app_context():
            assert db.session.get(Order, oid).status == st


def test_refresh_tracking_unknown_state_keeps_status(logged_in_admin, app, seed):
    oid = _make_order(app, "BD-V150E", Order.STATUS_PREPARED)
    with _zr_patch("حالة جديدة ما نعرفوهاش"):
        logged_in_admin.post(f"/admin/orders/{oid}/refresh-tracking",
                             follow_redirects=True)
    with app.app_context():
        o = db.session.get(Order, oid)
        assert o.status == Order.STATUS_PREPARED
        assert o.carrier_state == "حالة جديدة ما نعرفوهاش"


def test_admin_can_edit_review_rating(logged_in_admin, app, seed):
    from models import Review, AdminAction
    with app.app_context():
        r = Review(product_id=None, author_name="زبون", rating=1,
                   comment="ماشاء الله", is_approved=True)
        db.session.add(r)
        db.session.commit()
        rid = r.id

    resp = logged_in_admin.post(f"/admin/reviews/{rid}/rating",
                                data={"rating": "5"}, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        assert db.session.get(Review, rid).rating == 5
        assert AdminAction.query.filter_by(action=AdminAction.REVIEW_EDIT).count() == 1

    # قيمة خارج المجال ما تتقبلش
    logged_in_admin.post(f"/admin/reviews/{rid}/rating", data={"rating": "9"})
    with app.app_context():
        assert db.session.get(Review, rid).rating == 5


def test_public_review_forms_have_no_default_star(client, app, seed):
    """الفورم ما لازمش يجي بنجوم مختارة مسبقًا — الزبون يختار بنفسو."""
    html = client.get("/reviews").get_data(as_text=True)
    assert 'name="rating"' in html and "checked" not in html.split('name="rating"', 1)[1].split("</form>")[0]
