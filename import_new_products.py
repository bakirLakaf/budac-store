# -*- coding: utf-8 -*-
"""
استيراد المنتجات الجداد من مجلد الصور — تلقائي بالكامل ما عدا الأسعار.

واش يدير:
  1. يتحقق من كل الصور قبل ما يكتب حتى حاجة فقاعدة البيانات
  2. يخلق التصنيفات الناقصة
  3. يخلق المنتجات (سعر 0، غير مفعّلة)
  4. يحوّل كل صورة لـWebP + مصغّرة (نفس المعالجة تاع لوحة التحكم)
  5. يربط كل صورة بموديلها

التشغيل:
    python import_new_products.py --images "C:\\...\\منتجات BUDAC" --dry-run
    python import_new_products.py --images "C:\\...\\منتجات BUDAC"

الخيارات:
    --images PATH   مجلد الصور (إجباري)
    --dry-run       يوريك واش راح يصرا، بلا ما يكتب والو
    --only SLUG     يستورد منتج واحد برك (للتجربة)

⚠️ السكريبت **ما يحذفش** ولا **يبدّل** أي منتج موجود.
   إذا لقى slug موجود، يتخطاه ويقولك.
"""

import argparse
import os
import sys

# نضيفو مجلد المشروع للمسار باش نقدرو نستوردو app و models
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from models import db, Category, Product, ProductVariant, ProductImage
from services.images import process_image, get_upload_dir, ImageProcessError

from products_data import PRODUCTS


# ألوان بسيطة للطرفية (تخدم فويندوز 10+ وفلينكس)
class C:
    OK = "\033[92m"
    WARN = "\033[93m"
    ERR = "\033[91m"
    INFO = "\033[96m"
    OFF = "\033[0m"


def log_ok(msg):
    print(f"{C.OK}  [OK]{C.OFF} {msg}")


def log_warn(msg):
    print(f"{C.WARN}  [!] {C.OFF} {msg}")


def log_err(msg):
    print(f"{C.ERR}  [X] {C.OFF} {msg}")


def log_info(msg):
    print(f"{C.INFO}{msg}{C.OFF}")


# ═══════════════════════════════════════════════════════════
#  1) التحقق قبل الكتابة
# ═══════════════════════════════════════════════════════════

def collect_image_paths(product):
    """يجمع كل مسارات الصور تاع منتج واحد."""
    paths = [v["image"] for v in product["variants"]]
    paths += [e["image"] for e in product.get("extras", [])]
    return paths


def validate(images_root, products):
    """
    يتأكد من كل شي قبل ما نكتبو فقاعدة البيانات.

    ⚠️ علاش نتحققو مسبقًا؟ لأن الاستيراد نصفو (10 منتجات من 26) أسوأ
    من الفشل الكامل: تبقى ما تعرفش وين وقف، وتعاود من الصفر بمنتجات
    مكررة. نتأكدو أولاً، ونكتبو من بعد.
    """
    problems = []
    slugs = set()

    for product in products:
        slug = product["slug"]

        if slug in slugs:
            problems.append(f"slug مكرر فملف البيانات: {slug}")
        slugs.add(slug)

        if not product["variants"]:
            problems.append(f"{slug}: ماكانش حتى موديل")

        for rel_path in collect_image_paths(product):
            full = os.path.join(images_root, rel_path)
            if not os.path.isfile(full):
                problems.append(f"{slug}: الصورة ماكانتش → {rel_path}")

    return problems


def check_existing_slugs(products):
    """يرجع قائمة الـslugs اللي راهم ديجا فقاعدة البيانات."""
    wanted = [p["slug"] for p in products]
    found = Product.query.filter(Product.slug.in_(wanted)).all()
    return {p.slug for p in found}


# ═══════════════════════════════════════════════════════════
#  2) التصنيفات
# ═══════════════════════════════════════════════════════════

def get_or_create_category(name, cache, dry_run):
    """
    يجيب التصنيف بالاسم، ويخلقو إذا ماكانش.

    ⚠️ البحث بالاسم بالضبط (بلا تطبيع). إذا التصنيف موجود بمسافة
    زائدة ولا حرف مختلف، راح يتخلق تصنيف جديد — لهذا نطبعو
    التصنيفات المخلوقة الجدد فالتقرير باش تشوفهم بعينك.
    """
    if name in cache:
        return cache[name]

    category = Category.query.filter_by(name=name).first()

    if category is None:
        if dry_run:
            log_warn(f"تصنيف جديد (راح يتخلق): {name}")
            cache[name] = None
            return None

        from utils import slugify
        category = Category(name=name, slug=slugify(name), is_active=True)
        db.session.add(category)
        db.session.flush()   # باش ياخذ id بلا commit
        log_warn(f"تصنيف جديد اتخلق: {name}")

    cache[name] = category
    return category


# ═══════════════════════════════════════════════════════════
#  3) الصور
# ═══════════════════════════════════════════════════════════

def save_product_image(images_root, rel_path, slug, index):
    """
    يعالج صورة وحدة ويحفظها فـstatic/img/products/.

    يرجع اسم ملف WebP المحفوظ.
    الاسم يتبنى من الـslug + رقم — فريد ومقروء (مثلاً bouton-kojak-03.webp).
    """
    source = os.path.join(images_root, rel_path)
    target_dir = get_upload_dir("products")
    base_name = f"{slug}-{index:02d}"

    main_filename, _thumb = process_image(source, target_dir, base_name, make_thumb=True)
    return main_filename


# ═══════════════════════════════════════════════════════════
#  4) الاستيراد
# ═══════════════════════════════════════════════════════════

def import_product(product, images_root, cat_cache, dry_run):
    """يستورد منتج واحد بكل موديلاتو وصورو."""
    slug = product["slug"]
    variants = product["variants"]
    extras = product.get("extras", [])

    log_info(f"\n▸ {product['name']}  ({slug})")

    category = get_or_create_category(product["category"], cat_cache, dry_run)

    if dry_run:
        print(f"    التصنيف : {product['category']}")
        print(f"    الموديلات: {len(variants)}  →  " + " · ".join(v["label"] for v in variants))
        print(f"    الصور    : {len(variants) + len(extras)}")
        return "dry"

    # ---------- المنتج ----------
    # ⚠️ السعر 0 وغير مفعّل قصدًا: المتجر ما يعرضش منتج غير مفعّل،
    #    فمستحيل زبون يشوف سلعة بـ0 دج قبل ما تعمّر السعر.
    new_product = Product(
        name=product["name"],
        slug=slug,
        description=product["description"],
        price=0,
        stock_quantity=0,
        category_id=category.id if category else None,
        is_active=False,
        is_featured=False,
    )
    db.session.add(new_product)
    db.session.flush()

    image_index = 0

    # ---------- الموديلات + صورهم ----------
    for position, variant in enumerate(variants):
        image_index += 1

        try:
            filename = save_product_image(images_root, variant["image"], slug, image_index)
        except ImageProcessError as e:
            raise RuntimeError(f"{slug}: فشلت معالجة {variant['image']} — {e}")

        # أول صورة = الصورة الرئيسية تاع المنتج (اللي تبان فبطاقة المنتج والسلة)
        if position == 0:
            new_product.image_filename = filename

        db.session.add(ProductImage(
            product_id=new_product.id,
            image_filename=filename,
            sort_order=position,
            is_extra=False,
        ))

        db.session.add(ProductVariant(
            product_id=new_product.id,
            label=variant["label"],
            price=None,           # يورث سعر المنتج حتى تعمّرو
            stock_quantity=0,
            image_filename=filename,
            is_default=(position == 0),
            is_available=True,
        ))

    # ---------- الصور التوضيحية ----------
    for position, extra in enumerate(extras):
        image_index += 1

        try:
            filename = save_product_image(images_root, extra["image"], slug, image_index)
        except ImageProcessError as e:
            raise RuntimeError(f"{slug}: فشلت معالجة {extra['image']} — {e}")

        db.session.add(ProductImage(
            product_id=new_product.id,
            image_filename=filename,
            sort_order=len(variants) + position,
            is_extra=True,
            caption=extra.get("caption"),
        ))

    log_ok(f"{len(variants)} موديل · {image_index} صورة")
    return "created"


def run():
    parser = argparse.ArgumentParser(description="استيراد المنتجات الجداد لمتجر BUDAC")
    parser.add_argument("--images", default=None,
                        help="مسار مجلد الصور (افتراضيًا: import_images حذا هاد الملف)")
    parser.add_argument("--dry-run", action="store_true", help="تجربة بلا كتابة")
    parser.add_argument("--only", help="استيراد slug واحد برك")
    args = parser.parse_args()

    # ⚠️ الافتراضي = مجلد import_images حذا السكريبت. هكذا على Railway
    # تكتب `python import_new_products.py` برك، بلا ما تكتب حتى مسار.
    if args.images:
        images_root = os.path.abspath(args.images)
    else:
        images_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "import_images")

    if not os.path.isdir(images_root):
        log_err(f"المجلد ماكانش: {images_root}")
        return 1

    products = PRODUCTS
    if args.only:
        products = [p for p in PRODUCTS if p["slug"] == args.only]
        if not products:
            log_err(f"ما لقيناش منتج بـslug: {args.only}")
            return 1

    print("=" * 60)
    print(f"  مجلد الصور : {images_root}")
    print(f"  المنتجات   : {len(products)}")
    print(f"  الوضع      : {'تجربة (بلا كتابة)' if args.dry_run else 'استيراد حقيقي'}")
    print("=" * 60)

    # ---------- التحقق ----------
    log_info("\n[1/3] التحقق من الصور...")
    problems = validate(images_root, products)

    if problems:
        log_err(f"لقينا {len(problems)} مشكل — ما بدّلنا والو:\n")
        for problem in problems:
            print(f"    • {problem}")
        return 1

    log_ok("كل الصور موجودة")

    app = create_app()

    with app.app_context():
        # ---------- التحقق من التكرار ----------
        log_info("\n[2/3] التحقق من التكرار مع المتجر...")
        existing = check_existing_slugs(products)

        if existing:
            log_warn(f"{len(existing)} منتج موجود ديجا — راح نتخطاوهم:")
            for slug in sorted(existing):
                print(f"    • {slug}")

        todo = [p for p in products if p["slug"] not in existing]

        if not todo:
            log_warn("ماكانش حتى منتج جديد للاستيراد")
            return 0

        log_ok(f"{len(todo)} منتج جديد للاستيراد")

        # ---------- الاستيراد ----------
        log_info(f"\n[3/3] الاستيراد...")
        cat_cache = {}
        created = 0

        try:
            for product in todo:
                if import_product(product, images_root, cat_cache, args.dry_run) == "created":
                    created += 1

            if args.dry_run:
                db.session.rollback()
                log_info("\n" + "=" * 60)
                log_ok("التجربة كملت — ما اتكتب والو فقاعدة البيانات")
                log_info("علاش تشوف: أسماء التصنيفات الجدد فوق. إذا فيهم واحد")
                log_info("موجود ديجا باسم مختلف شوية، صحّحو ف products_data.py")
                log_info("قبل ما تشغّل الاستيراد الحقيقي.")
            else:
                # ⚠️ commit واحد فالآخر: يا الكل يدخل، يا والو.
                #    هكذا ما نلقاوش روحنا بـ13 منتج مستورد و13 لا.
                db.session.commit()
                log_info("\n" + "=" * 60)
                log_ok(f"اتخلقو {created} منتج")
                log_info("\nالخطوة الجاية:")
                log_info("  1. افتح لوحة التحكم → المنتجات")
                log_info("  2. عمّر الأسعار (المنتج + كل موديل)")
                log_info("  3. فعّل المنتج باش يبان فالمتجر")

        except Exception as e:
            db.session.rollback()
            log_err(f"\nفشل الاستيراد — رجّعنا كل شي للوراء:\n    {e}")
            log_info("ما اتبدّل والو فقاعدة البيانات. صحّح المشكل وعاود.")
            log_warn("ملاحظة: بعض ملفات WebP يمكن اتكتبو فـstatic/img/products/")
            log_warn("        ما يضرّوش — يتكتبو فوقهم فالمحاولة الجاية.")
            return 1

    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(run())
