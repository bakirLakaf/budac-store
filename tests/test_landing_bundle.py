"""
اختبارات صفحة الهبوط بوضع الباقة:
الزبون يختار موديل الغلاف الداخلي + الغلاف الخارجي ثابت، بسعر موحّد.
"""

import pytest

from models import (
    db, Category, Product, ProductVariant, LandingPage, LandingItem, Order,
)
from routes.landing import _split_price


def _form(**overrides):
    form = {
        "name": "علي بن علي",
        "phone": "0666255200",
        "quantity": "1",
        "wilaya_code": "39",
        "commune": "الوادي",
        "carrier_code": "zrexpress",
        "delivery_type": "home",
    }
    form.update(overrides)
    return form


@pytest.fixture
def bundle(app, seed):
    """باقة: غلاف داخلي (3 موديلات، 350) + غلاف خارجي (550) بسعر 800."""
    with app.app_context():
        cat = Category.query.first()
        indoor = Product(name="غلاف كليماتيزور داخلي", slug="indoor",
                         price=350, is_active=True, category_id=cat.id)
        outdoor = Product(name="غلاف كليماتيزور خارجي", slug="outdoor",
                          price=550, is_active=True, category_id=cat.id)
        db.session.add_all([indoor, outdoor])
        db.session.flush()

        drops = ProductVariant(product_id=indoor.id, label="قطرات")
        stars = ProductVariant(product_id=indoor.id, label="نجوم")
        off = ProductVariant(product_id=indoor.id, label="مقطوع", is_available=False)
        gray = ProductVariant(product_id=outdoor.id, label="قري")
        db.session.add_all([drops, stars, off, gray])
        db.session.flush()

        page = LandingPage(
            slug="ac-offer", internal_name="عرض الغلاف", headline="عرض خاص",
            product_id=indoor.id, is_active=True,
            campaign_price=800, compare_at_price=900, max_quantity=5,
        )
        db.session.add(page)
        db.session.flush()

        items = {}
        for name, var in (("drops", drops), ("stars", stars), ("off", off)):
            it = LandingItem(landing_page_id=page.id, role="choice",
                             product_id=indoor.id, variant_id=var.id)
            db.session.add(it)
            items[name] = it
        db.session.add(LandingItem(landing_page_id=page.id, role="fixed",
                                   product_id=outdoor.id, variant_id=gray.id))
        db.session.commit()

        return {
            "slug": page.slug, "page_id": page.id,
            "drops": items["drops"].id, "stars": items["stars"].id,
            "off": items["off"].id,
            "indoor_id": indoor.id, "outdoor_id": outdoor.id,
        }


def test_split_price_sums_exactly():
    assert sum(_split_price(800, [350, 550])) == 800
    assert _split_price(800, [350, 550]) == [311, 489]
    # أسعار غريبة وكميات صغيرة — المجموع دايمًا مضبوط
    for total in (1, 99, 800, 1001):
        for weights in ([1, 1], [350, 550], [7, 3, 13], [0, 0]):
            assert sum(_split_price(total, weights)) == total


def test_page_renders_choices_and_fixed_item(client, bundle):
    body = client.get(f"/lp/{bundle['slug']}").get_data(as_text=True)
    assert "اختار الموديل" in body
    assert "قطرات" in body and "نجوم" in body
    assert "يجيك معاه فنفس الباقة" in body
    assert "غلاف كليماتيزور خارجي" in body


def test_bundle_order_creates_two_lines_totalling_the_bundle_price(client, app, bundle):
    r = client.post(f"/lp/{bundle['slug']}",
                    data=_form(choice_id=str(bundle["drops"]), quantity="2"))
    assert r.status_code == 302, r.get_data(as_text=True)[:400]

    with app.app_context():
        order = Order.query.one()
        assert len(order.items) == 2
        # 800 × 2 باقات = 1600 للمنتجات (بدون التوصيل)
        assert sum(i.unit_price * i.quantity for i in order.items) == 1600
        assert {i.product_id for i in order.items} == {
            bundle["indoor_id"], bundle["outdoor_id"]
        }


def test_bundle_requires_a_choice(client, app, bundle):
    r = client.post(f"/lp/{bundle['slug']}", data=_form())
    assert r.status_code == 400
    assert "اختر الموديل" in r.get_data(as_text=True)
    with app.app_context():
        assert Order.query.count() == 0


def test_bundle_rejects_choice_from_another_page(client, app, bundle, seed):
    """رقم اختيار ما ينتميش للصفحة = مرفوض (ما نصدّقوش الفورم)."""
    r = client.post(f"/lp/{bundle['slug']}", data=_form(choice_id="999999"))
    assert r.status_code == 400
    with app.app_context():
        assert Order.query.count() == 0


def test_bundle_rejects_unavailable_model(client, app, bundle):
    r = client.post(f"/lp/{bundle['slug']}",
                    data=_form(choice_id=str(bundle["off"])))
    assert r.status_code == 400
    with app.app_context():
        assert Order.query.count() == 0


def test_bundle_price_cannot_be_tampered_from_the_form(client, app, bundle):
    r = client.post(f"/lp/{bundle['slug']}",
                    data=_form(choice_id=str(bundle["stars"]),
                               unit_price="1", price="1"))
    assert r.status_code == 302
    with app.app_context():
        order = Order.query.one()
        assert sum(i.unit_price * i.quantity for i in order.items) == 800


def test_plain_landing_page_still_works(client, app, seed):
    """صفحة بلا عناصر = سلوكها القديم بالضبط."""
    r = client.post(f"/lp/{seed['page_slug']}", data=_form())
    assert r.status_code == 302
    with app.app_context():
        assert Order.query.one().items[0].quantity == 1


def test_admin_can_save_bundle_items(logged_in_admin, app, bundle):
    with app.app_context():
        page = db.session.get(LandingPage, bundle["page_id"])
        keys = [f"{i.product_id}:{i.variant_id}" for i in page.choices][:2]
        fixed = f"{page.fixed_items[0].product_id}:{page.fixed_items[0].variant_id}"
        base = {
            "internal_name": page.internal_name, "slug": page.slug,
            "product_id": page.product_id, "headline": "عنوان جديد",
            "campaign_price": 800, "compare_at_price": 900,
        }
    data = dict(base)
    data["choice_items"] = keys
    data["fixed_item"] = fixed
    r = logged_in_admin.post(f"/admin/landing-pages/{bundle['page_id']}/edit",
                             data=data)
    assert r.status_code == 302
    with app.app_context():
        page = db.session.get(LandingPage, bundle["page_id"])
        assert len(page.choices) == 2
        assert len(page.fixed_items) == 1


def test_admin_form_pages_render(logged_in_admin, bundle):
    assert logged_in_admin.get("/admin/landing-pages/new").status_code == 200
    r = logged_in_admin.get(f"/admin/landing-pages/{bundle['page_id']}/edit")
    assert r.status_code == 200
    assert "باقة بموديلات" in r.get_data(as_text=True)
