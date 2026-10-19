"""صفحة المنتج: زر الموديل وصور المعرض مترابطين فالاتجاهين."""
import pytest

from models import Product, ProductImage, ProductVariant, db


@pytest.fixture
def product(app):
    p = Product(name="مقبض", slug="maqbad", price=300, image_filename="a.jpg")
    db.session.add(p)
    db.session.flush()
    for i, f in enumerate(("a.jpg", "b.jpg", "c.jpg")):
        db.session.add(ProductImage(product_id=p.id, image_filename=f, sort_order=i))
    for label, f in (("ذهبي", "a.jpg"), ("فضي", "b.jpg"), ("أسود", "c.jpg")):
        db.session.add(ProductVariant(product_id=p.id, label=label, image_filename=f,
                                      stock_quantity=5))
    db.session.commit()
    return p


def test_product_page_has_two_way_sync_hooks(client, product):
    html = client.get("/product/maqbad").get_data(as_text=True)
    assert "__syncVariantFromImage" in html          # الصورة ← الموديل
    assert "__setPinnedImage" in html                # الموديل ← الصورة
    assert html.count('class="gallery-thumb') == 3
    assert html.count("data-variant-id=") >= 3
