"""v185 — لوحة تحكم الصفحة الرئيسية + سلايدر المنتجات."""
import json

import pytest

from models import db, Product, StoreSetting, Review
from services import homepage


def _products(n, prefix="p"):
    out = []
    for i in range(n):
        p = Product(name=f"منتج {prefix}{i}", slug=f"{prefix}{i}", price=100 + i, is_active=True,
                    stock_quantity=10, image_filename="x.jpg")
        db.session.add(p)
        out.append(p)
    db.session.commit()
    return out


def _html(client):
    return client.get("/").get_data(as_text=True)


def _newest_block(html):
    """قسم «وصل حديثًا» (نعرفوه بالـclass ماشي بالنص: النص يتكرر فـaria-label)."""
    return html.split("strip-newest")[1].split("</section>")[0]


# ───────────── الخدمة ─────────────

def test_default_layout_matches_the_requested_changes(app):
    layout = homepage.get_layout()
    keys = [s["key"] for s in layout]
    on = {s["key"]: s["enabled"] for s in layout}
    assert on["recently_viewed"] is False                       # «شفتها مؤخرًا» محيّد
    assert keys.index("testimonials") < keys.index("best_sellers") < keys.index("catalog")  # الآراء فوق
    opts = {s["key"]: s["options"] for s in layout}
    assert opts["best_sellers"] == {"count": 6, "mode": "slider", "autoplay": True}
    assert opts["newest"]["count"] == 6


def test_garbage_json_and_unknown_sections_fall_back_safely(app):
    StoreSetting.set("home_layout", "{not json")
    assert [s["key"] for s in homepage.get_layout()] == [s["key"] for s in homepage.default_layout()]
    StoreSetting.set("home_layout", json.dumps([
        {"key": "ghost", "enabled": True}, {"key": "newest", "enabled": False}, {"key": "newest"}]))
    layout = homepage.get_layout()
    keys = [s["key"] for s in layout]
    assert "ghost" not in keys and keys.count("newest") == 1 and keys[0] == "newest"
    assert len(keys) == len(homepage.SECTIONS)                  # الناقصين يتزادو فالآخر


def test_options_are_clamped_and_validated(app):
    StoreSetting.set("home_layout", json.dumps([{"key": "newest", "enabled": True,
        "options": {"count": 999, "mode": "<script>", "autoplay": 0}}]))
    o = homepage.section_options("newest")
    assert o == {"count": 12, "mode": "slider", "autoplay": False}
    StoreSetting.set("home_layout", json.dumps([{"key": "newest", "options": {"count": "abc"}}]))
    assert homepage.section_options("newest")["count"] == 6
    StoreSetting.set("home_layout", json.dumps([{"key": "newest", "options": {"count": 1}}]))
    assert homepage.section_options("newest")["count"] == 3


def test_catalog_can_never_be_hidden(app):
    StoreSetting.set("home_layout", json.dumps([{"key": "catalog", "enabled": False}]))
    assert "catalog" in [s["key"] for s in homepage.active_sections()]


# ───────────── الصفحة ─────────────

def test_recently_viewed_hidden_by_default_and_shown_when_enabled(client, app):
    _products(2)
    assert "recentlyViewed" not in _html(client)
    lay = homepage.default_layout()
    for s in lay:
        if s["key"] == "recently_viewed":
            s["enabled"] = True
    StoreSetting.set("home_layout", json.dumps(lay))
    assert "recentlyViewed" in _html(client)


def test_strip_is_a_slider_with_six_products(client, app):
    _products(8)
    html = _html(client)
    assert 'data-ss' in html and 'data-autoplay="1"' in html
    new_block = _newest_block(html)
    assert new_block.count('class="product-card"') == 6          # 6 ماشي 8


def test_strip_count_option_and_grid_mode(client, app):
    _products(8)
    lay = homepage.default_layout()
    for s in lay:
        if s["key"] == "newest":
            s["options"] = {"count": 4, "mode": "grid", "autoplay": False}
    StoreSetting.set("home_layout", json.dumps(lay))
    new_block = _newest_block(_html(client))
    assert new_block.count('class="product-card"') == 4 and "data-ss" not in new_block


def test_three_or_fewer_products_render_as_plain_grid(client, app):
    _products(3)
    assert "data-ss" not in _html(client)


def test_sections_follow_configured_order(client, app):
    _products(2)
    lay = homepage.default_layout()
    lay.sort(key=lambda s: s["key"] != "catalog")                # الشبكة أولاً
    StoreSetting.set("home_layout", json.dumps(lay))
    html = _html(client)
    assert html.index('id="products"') < html.index("strip-newest")


def test_hidden_section_is_not_rendered_or_queried(client, app):
    _products(8)
    lay = homepage.default_layout()
    for s in lay:
        if s["key"] in ("newest", "best_sellers", "trust"):
            s["enabled"] = False
    StoreSetting.set("home_layout", json.dumps(lay))
    html = _html(client)
    assert "strip-newest" not in html and "الأكثر طلبًا" not in html and "trust-bar" not in html
    assert 'id="products"' in html


def test_testimonials_come_before_products_by_default(client, app):
    p = _products(1)[0]
    db.session.add(Review(product_id=p.id, author_name="سمير", rating=5,
                          comment="ممتاز جدًا والتوصيل سريع", is_approved=True))
    db.session.commit()
    html = _html(client)
    assert "تقييمات الزبائن" in html
    assert html.index("تقييمات الزبائن") < html.index('id="products"')


# ───────────── الأدمن ─────────────

def test_admin_panel_renders_and_saves(logged_in_admin, app):
    r = logged_in_admin.get("/admin/homepage")
    assert r.status_code == 200 and "الصفحة الرئيسية" in r.get_data(as_text=True)
    keys = [s["key"] for s in homepage.default_layout()]
    keys.reverse()
    data = {"order": keys, "on_catalog": "1", "on_newest": "1",
            "count_newest": "9", "mode_newest": "grid"}
    logged_in_admin.post("/admin/homepage", data=data)
    layout = homepage.get_layout()
    assert [s["key"] for s in layout] == keys
    on = {s["key"]: s["enabled"] for s in layout}
    assert on["newest"] and not on["hero"]                       # غير المعلّم = مخفي
    assert homepage.section_options("newest") == {"count": 9, "mode": "grid", "autoplay": False}


def test_admin_reset_and_rejects_forged_keys(logged_in_admin, app):
    logged_in_admin.post("/admin/homepage", data={"order": ["evil", "newest"], "on_newest": "1"})
    assert [s["key"] for s in homepage.get_layout()][0] == "newest"
    logged_in_admin.post("/admin/homepage", data={"action": "reset"})
    assert StoreSetting.get("home_layout", "") == ""
    assert [s["key"] for s in homepage.get_layout()][0] == "hero"
