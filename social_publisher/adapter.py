"""الـ Adapter = الحلقة بين أداة النشر ومتجر Budac (موديلات Product و ProductImage).

القاعدة: صور المتجر ما نكوبيوهاش أبدًا — نرجّعو رابطها العام ومسارها في القرص (UPLOAD_ROOT)
والأداة تقراها مباشرة وقت النشر، وما تخزّن منها حتى نسخة.
"""
import os

from .config import cfg


class BaseAdapter:
    def search_products(self, q, limit=12):
        """يرجّع [{id, name, price, thumb}]"""
        raise NotImplementedError

    def get_product(self, product_id):
        """يرجّع {id, name, price, url, images:[{id, url, local_path}]} أو None"""
        raise NotImplementedError


class BudacAdapter(BaseAdapter):
    SUBFOLDER = "products"

    # ---------- مسارات الصور ----------
    def _img_url(self, filename, thumb=False):
        from services.images import thumb_name
        name = thumb_name(filename) if thumb else filename
        return f"{cfg.public_base_url}/static/img/{self.SUBFOLDER}/{name}"

    def _img_path(self, filename):
        """مسار الملف في القرص (الـ Volume ولا static/img)؛ None لو ماكانش."""
        from flask import current_app
        from services.images import get_upload_dir
        name = os.path.basename(filename)
        d = get_upload_dir(self.SUBFOLDER)
        if not os.path.isabs(d):
            d = os.path.join(current_app.root_path, d)
        # القرص الدائم أولاً، وبعدها static/img (كيما يدير المتجر في uploaded_image)
        for folder in (d, os.path.join(current_app.root_path, "static", "img", self.SUBFOLDER)):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return os.path.abspath(path)
        return None

    # ---------- المنتجات ----------
    def search_products(self, q, limit=12):
        from models import Product
        query = Product.query.filter(Product.is_active.is_(True))
        if q:
            like = f"%{q}%"
            query = query.filter(Product.name.ilike(like) | Product.slug.ilike(like))
        out = []
        for p in query.order_by(Product.id.desc()).limit(limit).all():
            out.append({"id": str(p.id), "name": p.name, "price": p.price,
                        "thumb": self._img_url(p.image_filename, thumb=True) if p.image_filename else None})
        return out

    def get_product(self, product_id):
        from flask import url_for
        from models import Product, db
        try:
            p = db.session.get(Product, int(product_id))
        except (TypeError, ValueError):
            return None
        if p is None:
            return None
        # صور المنتج أولاً (بترتيب المعرض)، بعدها الصور التوضيحية؛ بلا تكرار
        names = list(p.gallery_images) + [i.image_filename for i in p.extra_images]
        seen, images = set(), []
        for n in names:
            if n and n not in seen:
                seen.add(n)
                images.append({"id": n, "url": self._img_url(n), "thumb": self._img_url(n, thumb=True),
                               "local_path": self._img_path(n)})
        url = f"{cfg.public_base_url}{url_for('shop.product_detail', slug=p.slug)}" if cfg.public_base_url else ""
        return {"id": str(p.id), "name": p.name, "price": p.price, "url": url, "images": images}
