"""v179 — قوالب صفحة الهبوط، الباقة، اختيار الموديل، المعاينة."""
import pytest
from models import db, LandingPage, Product, ProductVariant


def _make(app, **kw):
    with app.app_context():
        main = Product(name="غطاء داخلي", slug="inner", price=350, is_active=True,
                       stock_quantity=50, image_filename="in.jpg")
        outer = Product(name="غطاء خارجي", slug="outer", price=550, is_active=True,
                        stock_quantity=50, image_filename="out.jpg")
        db.session.add_all([main, outer]); db.session.flush()
        v1 = ProductVariant(product_id=main.id, label="ورود", stock_quantity=5,
                            image_filename="v1.jpg", is_default=True)
        v2 = ProductVariant(product_id=main.id, label="نجوم", stock_quantity=5,
                            image_filename="v2.jpg")
        db.session.add_all([v1, v2]); db.session.flush()
        page = LandingPage(slug="ac", internal_name="x", product_id=main.id,
                           headline="عرض", campaign_price=800, compare_at_price=900,
                           bundle_product_id=outer.id, show_variant_picker=True, **kw)
        db.session.add(page); db.session.commit()
        return page.id, main.id, outer.id, v1.id, v2.id


def test_price_split_sums_to_campaign_price(app):
    with app.app_context():
        pid, *_ = _make(app)
        page = db.session.get(LandingPage, pid)
        a, b = page.price_split()
        assert a + b == 800 and (a, b) == (311, 489)


def test_legacy_page_without_new_columns_uses_defaults(app):
    with app.app_context():
        pid, *_ = _make(app)
        page = db.session.get(LandingPage, pid)
        page.template = None; page.image_fit = None
        assert page.theme == "gold" and page.fit == "contain"
        page.template = "hacker"
        assert page.theme == "gold"


@pytest.mark.parametrize("theme", ["gold", "clean", "story", "fun"])
def test_every_theme_renders_with_slider_and_picker(client, app, theme):
    pid, *_ = _make(app, template=theme)
    html = client.get("/lp/ac").get_data(as_text=True)
    assert f"lp-theme-{theme}" in html
    assert 'id="lpTrack"' in html and html.count('class="lp-slide"') >= 3
    assert 'name="variant_id"' in html and "غطاء خارجي" in html


def test_preview_override_is_admin_only(client, app):
    _make(app)
    assert "lp-theme-fun" not in client.get("/lp/ac?theme=fun").get_data(as_text=True)
    with client.session_transaction() as s:
        s["admin_id"] = 1
    html = client.get("/lp/ac?theme=fun&fit=cover").get_data(as_text=True)
    assert "lp-theme-fun" in html and "lp-fit-cover" in html


def _form(**o):
    d = dict(name="علي بن علي", phone="0666255200", quantity="1", carrier_code="zrexpress",
             wilaya_code="39", delivery_type="home", commune="الوادي")
    d.update(o); return d


def test_bundle_order_creates_two_lines_totalling_campaign_price(client, app, seed):
    from models import Order, OrderItem
    pid, main_id, outer_id, v1, v2 = _make(app)
    r = client.post("/lp/ac", data=_form(variant_id=str(v2), quantity="2"))
    assert r.status_code == 302, r.get_data(as_text=True)[:400]
    with app.app_context():
        order = Order.query.first()
        items = OrderItem.query.filter_by(order_id=order.id).all()
        assert order.subtotal == 1600          # 800 × 2
        assert len(items) == 2
        assert {i.product_id for i in items} == {main_id, outer_id}


def test_foreign_variant_id_is_rejected(client, app, seed):
    _make(app)
    r = client.post("/lp/ac", data=_form(variant_id="99999"))
    assert r.status_code == 400 and "اختر الموديل" in r.get_data(as_text=True)


def test_admin_form_renders_and_saves_new_fields(logged_in_admin, app, seed):
    c = logged_in_admin
    html = c.get("/admin/landing-pages/new").get_data(as_text=True)
    assert "lpf-card" in html and 'name="template"' in html and 'name="image_fit"' in html
    r = c.post("/admin/landing-pages/new", data=dict(
        internal_name="t", slug="t-page", product_id=seed["product_id"], headline="h",
        template="story", image_fit="blur", show_variant_picker="on"))
    assert r.status_code == 302
    with app.app_context():
        p = LandingPage.query.filter_by(slug="t-page").one()
        assert (p.template, p.image_fit, p.show_variant_picker) == ("story", "blur", True)
        eid = p.id
    assert c.get(f"/admin/landing-pages/{eid}/edit").status_code == 200


def test_bundle_same_product_is_rejected(logged_in_admin, seed):
    r = logged_in_admin.post("/admin/landing-pages/new", data=dict(
        internal_name="t", slug="t2", product_id=seed["product_id"], headline="h",
        bundle_product_id=seed["product_id"], campaign_price="800"), follow_redirects=True)
    assert "غير المنتج الأساسي" in r.get_data(as_text=True)
