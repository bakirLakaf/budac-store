"""v182 — سلايدر العروض: ألوان/تخطيط، صورة كاملة، زر واضح، حافة الهيدر بلا شريط أبيض."""
from models import db, Offer, OfferItem, Product


def _offer(**kw):
    p = Product(name="غطاء", slug="cover-x", price=450, is_active=True,
                stock_quantity=10, image_filename="c.jpg")
    db.session.add(p)
    db.session.flush()
    data = dict(title="عرض الأغطية", slug="o1", bundle_price=900, is_active=True,
                banner_filename="b.jpg")
    data.update(kw)
    o = Offer(**data)
    db.session.add(o)
    db.session.flush()
    db.session.add(OfferItem(offer_id=o.id, product_id=p.id, quantity=2))
    db.session.commit()
    return o


def test_legacy_offer_without_style_defaults_to_gold_split(app):
    o = _offer(hero_style=None, hero_layout=None)
    assert (o.style, o.layout) == ("gold", "split")
    o.hero_style, o.hero_layout = "weird", "x"
    assert (o.style, o.layout) == ("gold", "split")


def test_slide_gets_style_class_and_full_image_marker(client, app):
    _offer(hero_style="craft")
    html = client.get("/").get_data(as_text=True)
    assert "hero-slide hs-craft" in html
    assert "hero-slide-media has-banner" in html     # صورة كاملة، ماشي مقصوصة
    assert "btn-primary btn-hero" in html


def test_banner_layout_marks_slide_and_hides_duplicate_text(client, app):
    _offer(hero_layout="banner", hero_style="soft")
    html = client.get("/").get_data(as_text=True)
    assert "hs-soft is-banner" in html and 'class="hs-sr"' in html
    assert 'class="hero-title"' not in html          # النص فالصورة، العنوان مخفي للقرّاء برك


def test_banner_layout_without_image_falls_back_to_split(client, app):
    _offer(hero_layout="banner", banner_filename=None)
    html = client.get("/").get_data(as_text=True)
    assert "is-banner" not in html.split("hero-slide-inner")[0] and 'class="hero-title"' in html


def test_header_scallop_takes_first_slide_color(client, app):
    _offer(hero_style="clean")
    html = client.get("/").get_data(as_text=True)
    assert "--scallop-bg: #FFFFFF" in html          # بلا شريط أبيض مخالف


def test_no_offers_means_default_scallop(client, app):
    assert "--scallop-bg" not in client.get("/").get_data(as_text=True)


def test_admin_saves_style_and_layout_and_rejects_garbage(logged_in_admin, app):
    o = _offer()
    r = logged_in_admin.post(f"/admin/offers/{o.id}/edit", data={
        "title": "عرض", "bundle_price": "900", "hero_style": "fun", "hero_layout": "banner",
        f"qty_{Product.query.first().id}": "1", "product_ids": [str(Product.query.first().id)],
    })
    o = db.session.get(Offer, o.id)
    assert r.status_code in (200, 302) and (o.hero_style, o.hero_layout) == ("fun", "banner")
    logged_in_admin.post(f"/admin/offers/{o.id}/edit", data={
        "title": "عرض", "bundle_price": "900", "hero_style": "<script>", "hero_layout": "zzz",
        "product_ids": [str(Product.query.first().id)],
    })
    o = db.session.get(Offer, o.id)
    assert (o.hero_style, o.hero_layout) == ("gold", "split")


def test_admin_offer_form_lists_styles_and_layouts(logged_in_admin, app):
    html = logged_in_admin.get("/admin/offers/new").get_data(as_text=True)
    for code in ("gold", "clean", "story", "fun", "soft", "craft"):
        assert f'name="hero_style" value="{code}"' in html
    assert 'name="hero_layout" value="banner"' in html


# ───────────── v183: أماكن الظهور + نص الزر ─────────────

def _html(client):
    return client.get("/").get_data(as_text=True)


def test_offer_shows_in_hero_and_cards_by_default(client, app):
    _offer()
    html = _html(client)
    assert "hero-slide hs-" in html and 'class="offer-card"' in html


def test_only_top_hides_cards(client, app):
    _offer(show_in_grid=False)
    html = _html(client)
    assert "hero-slide hs-" in html and "offers-section" not in html


def test_only_cards_hides_slider_and_scallop_color(client, app):
    _offer(show_in_hero=False)
    html = _html(client)
    assert "hero-slide hs-" not in html and "--scallop-bg" not in html
    assert 'class="offer-card"' in html


def test_bottom_only_uses_spotlight_and_nothing_else(client, app):
    _offer(show_in_hero=False, show_in_grid=False, spotlight_position="bottom")
    html = _html(client)
    assert "hero-slide hs-" not in html and "offers-section" not in html
    assert "spotlight-section" in html


def test_top_and_bottom_together(client, app):
    _offer(show_in_grid=False, spotlight_position="bottom")
    html = _html(client)
    assert "hero-slide hs-" in html and "spotlight-section" in html and "offers-section" not in html


def test_legacy_null_flags_mean_visible(app):
    o = _offer(show_in_hero=None, show_in_grid=None)
    assert o.in_hero and o.in_grid


def test_buttons_say_order_now(client, app):
    _offer(spotlight_position="bottom")
    html = _html(client)
    assert "اطلب العرض" not in html and html.count("اطلب الآن") >= 2


def test_admin_saves_placements(logged_in_admin, app):
    o = _offer()
    pid = str(Product.query.first().id)
    logged_in_admin.post(f"/admin/offers/{o.id}/edit", data={
        "title": "عرض", "bundle_price": "900", "product_ids": [pid], "show_in_hero": "on"})
    o = db.session.get(Offer, o.id)
    assert o.show_in_hero is True and o.show_in_grid is False
