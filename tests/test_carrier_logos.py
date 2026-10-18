"""شعارات شركات التوصيل: ملفات PNG شفافة + ظهورها فصفحة الأسعار والواجهة الرئيسية."""
import os

import pytest
from PIL import Image

from models import Carrier, ShippingRate, db

ROOT = os.path.join(os.path.dirname(__file__), "..")
CODES = ["zrexpress", "worldexpress", "yalidine"]


@pytest.mark.parametrize("code", CODES)
def test_logo_is_png_with_transparent_corners(code):
    img = Image.open(os.path.join(ROOT, "static", "img", "carriers", f"{code}.png"))
    assert img.format == "PNG" and img.mode == "RGBA"
    w, h = img.size
    assert w <= 400                                   # خفيف
    for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        assert img.getpixel(xy)[3] == 0, f"ركن غير شفاف فـ{code}"
    assert any(p[3] > 200 for p in img.getdata())     # فيه محتوى فعلاً


@pytest.fixture
def carriers(app):
    for code, name in (("zrexpress", "ZR Express"), ("worldexpress", "World Express"),
                       ("yalidine", "Yalidine Express")):
        c = Carrier(code=code, name=name, is_active=True)
        db.session.add(c)
        db.session.flush()
        db.session.add(ShippingRate(carrier_id=c.id, wilaya_code="04", wilaya_name="أم البواقي",
                                    home_price=600, office_price=400))
    db.session.commit()


def test_shipping_page_has_logos_in_header(client, carriers):
    html = client.get("/shipping-prices").get_data(as_text=True)
    for code in CODES:
        assert f"img/carriers/{code}.png" in html
    assert 'alt="Yalidine Express"' in html


def test_home_shows_carrier_strip_and_links_to_prices(client, carriers):
    html = client.get("/").get_data(as_text=True)
    assert "carriers-strip" in html and "img/carriers/yalidine.png" in html
    assert "/shipping-prices" in html


def test_inactive_carrier_is_hidden_from_home(client, carriers):
    Carrier.query.filter_by(code="worldexpress").update({"is_active": False})
    db.session.commit()
    html = client.get("/").get_data(as_text=True)
    assert "img/carriers/worldexpress.png" not in html
    assert "img/carriers/zrexpress.png" in html


def test_carrier_without_logo_falls_back_to_name(client, app):
    db.session.add(Carrier(code="newco", name="شركة جديدة", is_active=True))
    db.session.commit()
    html = client.get("/").get_data(as_text=True)
    assert "شركة جديدة" in html and "img/carriers/newco.png" not in html


def test_sticky_needs_body_not_to_be_a_scroll_container():
    css = open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8").read()
    assert "body { overflow-x: clip; }" in css


# ── أرخص سعر + «تبدأ من» + بطاقات الهاتف ──

def _rate(code, wilaya, home, office):
    carrier = Carrier.query.filter_by(code=code).first()
    db.session.add(ShippingRate(carrier_id=carrier.id, wilaya_code=wilaya,
                                wilaya_name="ولاية " + wilaya, home_price=home, office_price=office))


def test_cheapest_is_marked_separately_for_home_and_office(client, carriers):
    # أم البواقي: كلهم 600/400. نبدّلو واحد: yalidine أرخص فالمنزل، zr أرخص فالمكتب
    ShippingRate.query.update({"home_price": 700, "office_price": 500})
    _rate("yalidine", "05", 500, 600)
    _rate("zrexpress", "05", 600, 300)
    _rate("worldexpress", "05", 700, 700)
    db.session.commit()
    html = client.get("/shipping-prices").get_data(as_text=True)
    assert html.count("is-best") > 0
    from routes.shop import shipping_prices  # noqa: F401  (المسار موجود)
    # بيانات التمييز تتحسب فالسيرفر: نتأكّد منها مباشرة من الصفحة
    row05 = html.split('data-code="05"')[1].split("</tr>")[0]
    assert row05.count("is-best") == 2          # واحد للمنزل + واحد للمكتب


def test_no_cheapest_mark_when_all_prices_are_equal(client, carriers):
    ShippingRate.query.update({"home_price": 700, "office_price": 500})
    db.session.commit()
    html = client.get("/shipping-prices").get_data(as_text=True)
    row04 = html.split('data-code="04"')[1].split("</tr>")[0]
    assert "is-best" not in row04


def test_no_cheapest_mark_with_a_single_carrier(client, app):
    c = Carrier(code="yalidine", name="Yalidine Express", is_active=True)
    db.session.add(c)
    db.session.flush()
    db.session.add(ShippingRate(carrier_id=c.id, wilaya_code="04", wilaya_name="أم البواقي",
                                home_price=600, office_price=400))
    db.session.commit()
    html = client.get("/shipping-prices").get_data(as_text=True)
    assert 'class="ship-best-mark"' not in html


def test_home_cards_show_starting_price_rounded_like_the_table(client, carriers):
    ShippingRate.query.filter(ShippingRate.carrier_id == Carrier.query.filter_by(
        code="yalidine").first().id).update({"home_price": 590, "office_price": 450})
    db.session.commit()
    html = client.get("/").get_data(as_text=True)
    card = html.split("img/carriers/yalidine.png")[1].split("</a>")[0]
    assert "تبدأ من" in card and "<strong>500</strong>" in card     # 450 ← 500 (round_up_to_100)


def test_shipping_page_has_mobile_cards_for_each_wilaya(client, carriers):
    html = client.get("/shipping-prices").get_data(as_text=True)
    assert 'class="ship-cards"' in html and 'class="ship-card"' in html
