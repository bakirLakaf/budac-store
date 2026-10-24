"""أداة النشر (social_publisher) — اختبارات بلا PostgreSQL: التوقف الآمن + الـ Adapter على موديلات المتجر."""
import os

import pytest


def test_store_runs_without_postgres(app):
    """مع SQLite (بيئة الاختبار) الأداة تتوقف، والمتجر يخدم عادي بلا استثناء."""
    assert "social_publisher" not in app.blueprints
    assert not app.config.get("SOCIAL_PUBLISHER_ENABLED")
    assert app.test_client().get("/admin/social/").status_code == 404
    assert app.test_client().get("/").status_code == 200


def test_media_urls_are_not_visits_and_prefix_skipped():
    from services.analytics import SKIP_PREFIXES
    assert "/sp/" in SKIP_PREFIXES


def _make_product(app, tmp_path):
    from models import db, Product, ProductImage

    img_dir = tmp_path / "uploads" / "img" / "products"
    img_dir.mkdir(parents=True, exist_ok=True)
    for name in ("main-aaa.webp", "main-aaa-thumb.webp", "two-bbb.webp", "extra-ccc.webp"):
        (img_dir / name).write_bytes(b"x")

    p = Product(name="مقبض ذهبي", slug="gold-handle", price=450, image_filename="main-aaa.webp", is_active=True)
    db.session.add(p)
    db.session.flush()
    db.session.add_all([
        ProductImage(product_id=p.id, image_filename="main-aaa.webp", sort_order=0),
        ProductImage(product_id=p.id, image_filename="two-bbb.webp", sort_order=1),
        ProductImage(product_id=p.id, image_filename="extra-ccc.webp", sort_order=2, is_extra=True),
    ])
    hidden = Product(name="منتج مخفي", slug="hidden", price=100, is_active=False)
    db.session.add(hidden)
    db.session.commit()
    return p.id


def test_budac_adapter_products_images_no_copy(app, tmp_path):
    from social_publisher.adapter import BudacAdapter
    from social_publisher.config import cfg

    cfg.load(app)
    cfg.public_base_url = "https://shop.test"
    a = BudacAdapter()
    with app.test_request_context():
        pid = _make_product(app, tmp_path)

        found = a.search_products("مقبض")
        assert [x["name"] for x in found] == ["مقبض ذهبي"]
        assert found[0]["thumb"] == "https://shop.test/static/img/products/main-aaa-thumb.webp"
        assert a.search_products("مخفي") == []  # المنتجات غير النشطة ما تظهرش
        assert len(a.search_products("")) == 1

        prod = a.get_product(str(pid))
        assert prod["url"] == "https://shop.test/product/gold-handle" and prod["price"] == 450
        ids = [i["id"] for i in prod["images"]]
        assert ids == ["main-aaa.webp", "two-bbb.webp", "extra-ccc.webp"]  # الرئيسية ثم التوضيحية، بلا تكرار
        assert prod["images"][0]["url"] == "https://shop.test/static/img/products/main-aaa.webp"
        # المسار في الـ Volume (UPLOAD_ROOT) → الأداة تقرا منو مباشرة بلا نسخ
        assert prod["images"][0]["local_path"] == str(tmp_path / "uploads" / "img" / "products" / "main-aaa.webp")

        assert a.get_product("999999") is None
        assert a.get_product("abc") is None


def test_default_temp_dir_goes_to_volume(app, tmp_path, monkeypatch):
    """الملفات المؤقتة تتحط في UPLOAD_ROOT/sp_tmp (باش ما تتمسحش مع كل نشر)."""
    from social_publisher.config import cfg
    monkeypatch.delenv("SP_TEMP_DIR", raising=False)
    cfg.load(app)
    assert cfg.temp_dir == (tmp_path / "uploads" / "sp_tmp").resolve()
    assert not cfg.temp_dir.exists()  # ما ننشئوهاش إلا كي تخدم الأداة فعلاً


def test_delete_file_never_touches_store_images(app, tmp_path):
    from social_publisher import media as M
    from social_publisher.config import cfg
    cfg.load(app)
    cfg.temp_dir = (tmp_path / "uploads" / "sp_tmp").resolve()
    cfg.temp_dir.mkdir(parents=True)
    shop_img = tmp_path / "uploads" / "img" / "products"
    shop_img.mkdir(parents=True)
    f = shop_img / "keep.webp"
    f.write_bytes(b"x")
    assert M.delete_file(str(f)) is False and f.exists()  # خارج مجلد الأداة: ما يتحذفش
    tmp = cfg.temp_dir / "abc.jpg"
    tmp.write_bytes(b"x")
    assert M.delete_file(str(tmp)) is True and not tmp.exists()
