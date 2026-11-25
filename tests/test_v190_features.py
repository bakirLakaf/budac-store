"""v190 — كود برومو من تفاصيل الطلبية، خانات إضافية فصفحة الهبوط،
موديل جديد بصورة (كاميرا)، وتثبيت اللوحة كتطبيق (PWA)."""
import io
import json

from PIL import Image

from models import db, Coupon, LandingPage, Order, OrderItem, Product


# ───────────────────────── PWA ─────────────────────────

def test_pwa_manifest_is_public_and_valid(client):
    r = client.get("/admin/manifest.webmanifest")          # بلا تسجيل دخول
    assert r.status_code == 200
    assert "manifest+json" in r.headers["Content-Type"]
    m = r.get_json()
    assert m["scope"] == "/admin/" and m["display"] == "standalone"
    sizes = {(i["sizes"], i["purpose"]) for i in m["icons"]}
    assert ("192x192", "any") in sizes and ("512x512", "maskable") in sizes


def test_pwa_service_worker_scope_header(client):
    r = client.get("/admin/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["Content-Type"]
    assert r.headers["Service-Worker-Allowed"] == "/admin/"
    body = r.get_data(as_text=True)
    assert "addEventListener('fetch'" in body


def test_pwa_icons_exist_and_login_links_manifest(client):
    for f in ("icon-192.png", "icon-512.png", "icon-maskable-192.png", "icon-maskable-512.png"):
        assert client.get(f"/static/img/brand/{f}").status_code == 200
    html = client.get("/admin/login").get_data(as_text=True)
    assert 'rel="manifest"' in html and "data-pwa-install" in html
    assert client.get("/admin/offline").status_code == 200


# ───────────────────────── كود برومو ─────────────────────────

def _order(app, seed, note=None):
    with app.app_context():
        o = Order(order_number="BD-1", customer_name="شمس", customer_phone="0696340715",
                  wilaya_code="39", wilaya_name="الوادي", subtotal=0, shipping_price=600,
                  total=0, customer_note=note)
        db.session.add(o)
        db.session.flush()
        db.session.add(OrderItem(order_id=o.id, product_id=seed["product_id"],
                                 item_name="شريط", unit_price=1000, quantity=10))
        db.session.flush()
        db.session.expire(o, ["items"])
        o.recalculate()
        db.session.add(Coupon(code="KW-JCQMT", discount_type="percent", value=8))
        db.session.commit()
        return o.id


def test_admin_applies_and_removes_coupon(logged_in_admin, app, seed):
    oid = _order(app, seed, note="عندي كود تخفيض 8%: KW-JCQMT")
    html = logged_in_admin.get(f"/admin/orders/{oid}").get_data(as_text=True)
    assert 'value="KW-JCQMT"' in html                      # الكود المقترح من الملاحظة

    logged_in_admin.post(f"/admin/orders/{oid}/coupon", data={"code": "kw-jcqmt"})
    with app.app_context():
        o = db.session.get(Order, oid)
        assert o.coupon_code == "KW-JCQMT" and o.discount_amount == 800
        assert o.total == 10000 - 800 + 600
        assert Coupon.query.filter_by(code="KW-JCQMT").first().used_count == 1

    logged_in_admin.post(f"/admin/orders/{oid}/coupon", data={"action": "remove"})
    with app.app_context():
        o = db.session.get(Order, oid)
        assert o.coupon_code is None and o.discount_amount == 0 and o.total == 10600
        assert Coupon.query.filter_by(code="KW-JCQMT").first().used_count == 0


def test_invalid_coupon_changes_nothing(logged_in_admin, app, seed):
    oid = _order(app, seed)
    logged_in_admin.post(f"/admin/orders/{oid}/coupon", data={"code": "NOPE"})
    with app.app_context():
        o = db.session.get(Order, oid)
        assert o.coupon_code is None and o.total == 10600


def test_exhausted_coupon_is_refused(logged_in_admin, app, seed):
    oid = _order(app, seed)
    with app.app_context():
        c = Coupon.query.filter_by(code="KW-JCQMT").first()
        c.usage_limit, c.used_count = 1, 1
        db.session.commit()
    logged_in_admin.post(f"/admin/orders/{oid}/coupon", data={"code": "KW-JCQMT"})
    with app.app_context():
        assert db.session.get(Order, oid).coupon_code is None


# ───────────────────────── خانات إضافية ─────────────────────────

def test_custom_fields_roundtrip_in_model(app, seed):
    with app.app_context():
        page = LandingPage.query.filter_by(slug="promo").first()
        page.custom_fields_json = json.dumps(
            [{"label": "الاسم اللي تحب نكتبو", "required": True}], ensure_ascii=False)
        assert page.custom_fields[0]["label"] == "الاسم اللي تحب نكتبو"
        page.custom_fields_json = "{ليس json"
        assert page.custom_fields == []                    # JSON فاسد ما يطيّحش الصفحة


def test_admin_saves_custom_fields(logged_in_admin, app, seed):
    with app.app_context():
        pid = LandingPage.query.filter_by(slug="promo").first().id
    r = logged_in_admin.post(f"/admin/landing-pages/{pid}/edit", data={
        "slug": "promo", "headline": "عرض", "product_id": seed["product_id"],
        "cf_label": ["الاسم اللي تحب نكتبو", "", "لون الخط"],
        "cf_placeholder": ["مثلاً: محمد", "", ""],
        "cf_required": ["1", "1", "0"],
    })
    assert r.status_code == 302
    with app.app_context():
        fields = LandingPage.query.get(pid).custom_fields
        assert [f["label"] for f in fields] == ["الاسم اللي تحب نكتبو", "لون الخط"]
        assert [f["required"] for f in fields] == [True, False]


def _lp_post(client, seed, **extra):
    data = {"name": "محمد بن علي", "phone": "0555123456", "quantity": "1",
            "wilaya_code": "39", "delivery_type": "home", "commune": "الوادي",
            "carrier_code": seed["carrier_code"]}
    data.update(extra)
    return client.post("/lp/promo", data=data)


def test_landing_shows_and_requires_custom_field(client, app, seed):
    with app.app_context():
        LandingPage.query.filter_by(slug="promo").first().custom_fields_json = json.dumps(
            [{"label": "الاسم اللي تحب نكتبو", "required": True}], ensure_ascii=False)
        db.session.commit()
    assert "الاسم اللي تحب نكتبو" in client.get("/lp/promo").get_data(as_text=True)

    r = _lp_post(client, seed)                              # بلا قيمة → خطأ
    assert r.status_code == 400
    assert "اكتب «الاسم اللي تحب نكتبو»" in r.get_data(as_text=True)
    with app.app_context():
        assert OrderItem.query.count() == 0


def test_custom_value_saved_on_order_item_and_shown_in_admin(client, app, seed):
    with app.app_context():
        LandingPage.query.filter_by(slug="promo").first().custom_fields_json = json.dumps(
            [{"label": "الاسم اللي تحب نكتبو", "required": True}], ensure_ascii=False)
        db.session.commit()
    r = _lp_post(client, seed, cf_0="  عبد الرحمن  ")
    assert r.status_code == 302, r.get_data(as_text=True)[:400]
    with app.app_context():
        item = OrderItem.query.first()
        assert item.custom_text == "الاسم اللي تحب نكتبو: عبد الرحمن"
        assert item.custom_lines == [("الاسم اللي تحب نكتبو", "عبد الرحمن")]
        oid = item.order_id
    from models import AdminUser
    client.post("/admin/login", data={"username": seed["admin_username"],
                                      "password": seed["admin_password"]})
    html = client.get(f"/admin/orders/{oid}").get_data(as_text=True)
    assert "عبد الرحمن" in html and "item-custom" in html


# ───────────────────────── موديل بصورة (كاميرا) ─────────────────────────

def _png():
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (200, 30, 30)).save(buf, "PNG")
    buf.seek(0)
    return buf


def test_variant_add_with_new_photo(logged_in_admin, app, seed):
    r = logged_in_admin.post(
        f"/admin/products/{seed['product_id']}/variants/add",
        data={"label": "أحمر", "new_image": (_png(), "cam.png")},
        content_type="multipart/form-data")
    assert r.status_code == 302
    with app.app_context():
        p = db.session.get(Product, seed["product_id"])
        v = p.variants[0]
        assert v.label == "أحمر" and v.image_filename
        assert any(i.image_filename == v.image_filename and not i.is_extra for i in p.images)
        assert p.image_filename == v.image_filename          # أول صورة تولّي الرئيسية


def test_variant_add_rejects_fake_image_without_creating_variant(logged_in_admin, app, seed):
    r = logged_in_admin.post(
        f"/admin/products/{seed['product_id']}/variants/add",
        data={"label": "مزيف", "new_image": (io.BytesIO(b"<?php evil ?>"), "x.png")},
        content_type="multipart/form-data")
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(Product, seed["product_id"]).variants == []


def test_product_form_has_camera_input(logged_in_admin, app, seed):
    html = logged_in_admin.get(f"/admin/products/{seed['product_id']}/edit").get_data(as_text=True)
    assert 'capture="environment"' in html and 'id="newVariantForm"' in html
