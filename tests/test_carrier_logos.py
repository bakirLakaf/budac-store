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
