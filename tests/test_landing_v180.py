"""v180 — تاريخ نهاية العرض، العدّاد الاختياري، الكمية المتبقية، سعر واحد، تخطيط الحاسوب، قالبين جداد."""
from datetime import date, timedelta

import pytest

from models import db, LandingPage, Product, ProductVariant


def _make(app, **kw):
    """صفحة بسيطة: منتج واحد، بلا باقة، بلا اختيار موديل."""
    with app.app_context():
        main = Product(name="غطاء", slug="cover", price=900, is_active=True,
                       stock_quantity=50, image_filename="c.jpg")
        db.session.add(main)
        db.session.flush()
        page = LandingPage(slug="ac", internal_name="x", product_id=main.id,
                           headline="عرض", **kw)
        db.session.add(page)
        db.session.commit()
        return page.id


def _today():
    return LandingPage._today_dz()


# ---------- السعر الواحد ----------

def test_single_price_without_compare_shows_no_discount(client, app):
    _make(app, campaign_price=800)
    html = client.get("/lp/ac").get_data(as_text=True)
    assert "800 دج" in html
    assert "<s>" not in html and "lp-badge" not in html


# ---------- تاريخ العرض ----------

def test_offer_date_label_uses_algerian_month_names(app):
    with app.app_context():
        pid = _make(app, offer_ends_on=date(2026, 10, 31))
        page = db.session.get(LandingPage, pid)
        assert page.offer_ends_label == "31 أكتوبر 2026"
        assert page.offer_deadline_iso == "2026-10-31T23:59:59+01:00"
        page.offer_ends_on = date(2026, 1, 5)
        assert page.offer_ends_label == "5 جانفي 2026"


def test_offer_date_shown_without_countdown(client, app):
    _make(app, offer_ends_on=_today() + timedelta(days=3), show_countdown=False)
    html = client.get("/lp/ac").get_data(as_text=True)
    assert "العرض ساري حتى" in html
    assert 'id="lpCountdown"' not in html          # التايمر اختياري


def test_no_offer_date_means_nothing_shown(client, app):
    _make(app)
    html = client.get("/lp/ac").get_data(as_text=True)
    assert "العرض ساري حتى" not in html and "انتهى العرض" not in html


def test_countdown_runs_to_fixed_deadline_when_date_set(client, app):
    d = _today() + timedelta(days=5)
    _make(app, offer_ends_on=d, show_countdown=True)
    html = client.get("/lp/ac").get_data(as_text=True)
    assert 'id="lpCountdown"' in html
    assert f'data-deadline="{d.isoformat()}T23:59:59+01:00"' in html


def test_countdown_without_date_uses_hours_only(client, app):
    _make(app, show_countdown=True, countdown_hours=12)
    html = client.get("/lp/ac").get_data(as_text=True)
    assert 'data-hours="12"' in html and "data-deadline" not in html


def test_last_day_is_still_open(app):
    with app.app_context():
        pid = _make(app, offer_ends_on=_today())
        assert db.session.get(LandingPage, pid).offer_expired is False


def test_expired_offer_shows_notice_and_blocks_orders(client, app):
    _make(app, offer_ends_on=_today() - timedelta(days=1),
          show_countdown=True, stock_left=5)
    html = client.get("/lp/ac").get_data(as_text=True)
    assert "انتهى العرض" in html
    assert 'id="lpCountdown"' not in html and "باقي 5" not in html
    assert 'id="stickyCta"' not in html
    assert "<fieldset class=\"lp-fieldset\" disabled>" in html

    r = client.post("/lp/ac", data={
        "name": "محمد بن علي", "phone": "0550123456", "quantity": "1",
        "wilaya_code": "16", "carrier_code": "x", "delivery_type": "home",
        "commune": "الجزائر",
    })
    assert r.status_code == 400
    assert "انتهى العرض" in r.get_data(as_text=True)


# ---------- الكمية المتبقية ----------

def test_stock_left_number_is_optional_and_wins_over_text(client, app):
    _make(app, stock_left=9, stock_warning="نص قديم")
    html = client.get("/lp/ac").get_data(as_text=True)
    assert "باقي 9 قطع فقط" in html and "نص قديم" not in html


def test_legacy_stock_text_still_works_and_empty_hides(client, app):
    _make(app, stock_warning="الكمية محدودة")
    assert "الكمية محدودة" in client.get("/lp/ac").get_data(as_text=True)


def test_no_stock_line_when_nothing_set(client, app):
    _make(app)
    assert "lp-stock" not in client.get("/lp/ac").get_data(as_text=True)


# ---------- القوالب والتخطيط ----------

@pytest.mark.parametrize("theme", ["gold", "clean", "story", "fun", "soft", "craft"])
@pytest.mark.parametrize("layout", ["split", "stack"])
def test_every_theme_and_layout_renders(client, app, theme, layout):
    _make(app, template=theme, layout=layout)
    html = client.get("/lp/ac").get_data(as_text=True)
    assert f"lp-theme-{theme}" in html and f"lp-layout-{layout}" in html
    assert 'class="lp-shell"' in html and 'id="order"' in html


def test_legacy_page_without_layout_defaults_to_split(app):
    with app.app_context():
        pid = _make(app)
        page = db.session.get(LandingPage, pid)
        page.layout = None
        assert page.page_layout == "split"
        page.layout = "weird"
        assert page.page_layout == "split"


def test_layout_preview_is_admin_only(client, app):
    _make(app)
    assert "lp-layout-stack" not in client.get("/lp/ac?layout=stack").get_data(as_text=True)
    with client.session_transaction() as s:
        s["admin_id"] = 1
    assert "lp-layout-stack" in client.get("/lp/ac?layout=stack").get_data(as_text=True)


# ---------- فورم الأدمن ----------

def test_admin_form_reader_parses_new_fields(app):
    from routes.admin import _read_landing_form
    from werkzeug.datastructures import MultiDict
    with app.test_request_context():
        f = _read_landing_form(MultiDict({
            "offer_ends_on": "2026-10-31", "stock_left": "9", "layout": "stack",
            "template": "craft"}))
        assert f["offer_ends_on"] == date(2026, 10, 31)
        assert f["stock_left"] == 9 and f["layout"] == "stack" and f["template"] == "craft"
        g = _read_landing_form(MultiDict({"offer_ends_on": "غلط", "layout": "x"}))
        assert g["offer_ends_on"] is None and g["stock_left"] is None and g["layout"] == "split"
