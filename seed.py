"""
يعمر قاعدة البيانات بمنتجات تجريبية (من كتالوجك الحقيقي) باش تقدر تشوف الموقع خدام.
تشغيل: python seed.py
"""

from app import create_app
from models import db, Product, Category

app = create_app()

SAMPLE_CATEGORIES = [
    {"name": "مرايا", "slug": "mirrors"},
    {"name": "إكسسوارات", "slug": "accessories"},
]

SAMPLE_PRODUCTS = [
    {
        "name": "أشرطة المرايا (جوان ميروار)",
        "slug": "mirror-strip-joan-miroir",
        "description": "أشرطة مرايا ديكورية أنيقة، تضيف لمسة عصرية لأي جدار أو أثاث.",
        "price": 120,
        "category_slug": "mirrors",
        "color_variant": "دوري",
        "stock_quantity": 25,
    },
    {
        "name": "أشرطة المرايا (استراس)",
        "slug": "mirror-strip-strass",
        "description": "أشرطة مرايا مرصعة بحجارة استراس لامعة، تناسب الديكورات الفخمة.",
        "price": 450,
        "compare_at_price": 550,
        "category_slug": "mirrors",
        "color_variant": "دوري",
        "stock_quantity": 12,
    },
    {
        "name": "إطار مرآة ذهبي دائري",
        "slug": "golden-round-mirror-frame",
        "description": "مرآة بإطار ذهبي دائري الشكل، قطعة مركزية أنيقة لأي غرفة.",
        "price": 2800,
        "category_slug": "mirrors",
        "color_variant": "ذهبي",
        "stock_quantity": 6,
    },
    {
        "name": "طقم شموع ديكورية",
        "slug": "decorative-candle-set",
        "description": "طقم شموع بروائح هادئة، مثالي لأجواء دافئة ومريحة.",
        "price": 950,
        "category_slug": "accessories",
        "color_variant": "بيج",
        "stock_quantity": 0,
    },
]


def run():
    with app.app_context():
        db.create_all()

        categories_by_slug = {}
        for cat_data in SAMPLE_CATEGORIES:
            cat = Category.query.filter_by(slug=cat_data["slug"]).first()
            if not cat:
                cat = Category(**cat_data, is_active=True)
                db.session.add(cat)
                db.session.flush()
            categories_by_slug[cat_data["slug"]] = cat

        for item in SAMPLE_PRODUCTS:
            exists = Product.query.filter_by(slug=item["slug"]).first()
            if exists:
                continue

            item = dict(item)
            category_slug = item.pop("category_slug", None)
            category = categories_by_slug.get(category_slug)

            product = Product(**item, category=category, is_active=True)
            db.session.add(product)

        db.session.commit()
        print(f"✅ تمت إضافة/تأكيد {len(SAMPLE_PRODUCTS)} منتج و{len(SAMPLE_CATEGORIES)} تصنيف فقاعدة البيانات.")


if __name__ == "__main__":
    run()
