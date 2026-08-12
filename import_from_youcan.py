"""
يسحب كل المنتجات من متجرك عند YouCan (عبر API العمومي، بلا حاجة لمفتاح) ويضيفهم
مباشرة فقاعدة بيانات الموقع الجديد — بما فيهم:
  - التصنيفات (Category)
  - كل صور المنتج (ProductImage) — ماشي غير الصورة الرئيسية
  - التنويعات (ProductVariant) — لون/مقاس/إلخ، بأسعارها وكمياتها
  - الوصف: يتحول من HTML لنص واضح مقروء (ويتحفظ أيضًا بشكله الأصلي كاحتياط)

تشغيل: python import_from_youcan.py

⚠️ ملاحظة تقنية مهمة:
شكل بيانات الـvariants والـcategories عند YouCan ماعندوش توثيق رسمي دقيق، فالسكريبت
مبني بطريقة "دفاعية": يجرب أكثر من مفتاح محتمل لكل حقل، وإذا مالقاش شي متوقع
يطبع تحذير واضح فالـconsole بدل ما يكسر أو يعطي نتيجة غلط بصمت.
لأول منتج، السكريبت يطبع أيضًا شكل البيانات الخام (JSON) كاملة فملف
`debug_all_products.json` (بيانات **كل** المنتجات الخام) + تقرير `debug_report.txt`
يحلل واش لقى وواش ما لقاش فكل منتج — باش تشوف بالضبط فين المشكل.
"""

import io
import json
import os
import re
import time
import requests

from dotenv import load_dotenv

load_dotenv()  # باش YOUCAN_ACCESS_TOKEN يتقرا من ملف .env

from app import create_app
from models import db, Product, Category, ProductImage, ProductVariant
from services.images import process_image, ImageProcessError
from utils import html_to_text

# ⚙️ بدل هذا برابط متجرك الحقيقي (بلا / فالآخر)
YOUCAN_STORE_URL = "https://budacstore.youcan.store"

# ⚠️ بعض السيرفرات تحجب الطلبات اللي ما عندهاش User-Agent ديال متصفح حقيقي.
# هاد الهيدر يخلي الطلب يبان عادي.
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

IMAGES_DIR = os.path.join("static", "img", "products")
CATEGORY_IMAGES_DIR = os.path.join("static", "img", "categories")

# نجمعو بيانات كل المنتجات للتشخيص — ماشي غير الأول.
# السبب: المشاكل عادة ما تكونش فكل المنتجات. منتج عندو variants ومنتج لا،
# منتج عندو تصنيف ومنتج لا. بلا فحص الكل، نقدرو نخمنو غلط.
_debug_products = []      # البيانات الخام
_debug_analysis = []      # تحليل كل منتج


def slugify_fallback(text, product_id):
    """إذا المنتج ما عندوش slug، نبنيو وحدة بسيطة من الاسم."""
    cleaned = re.sub(r"[^\w\s-]", "", text).strip().lower()
    cleaned = re.sub(r"[\s_-]+", "-", cleaned)
    return cleaned or f"product-{product_id}"


def download_image(url, base_name, target_dir=IMAGES_DIR):
    """
    يحمّل صورة ويحوّلها لـWebP محسّنة (+ مصغّرة).

    ⚠️ ما نحفظوش الأصل: صور YouCan كبيرة (كثير منها 1-3 ميغا)، وحفظها كما هي
    يخلي الموقع ثقيل. نحوّلوها مباشرة وقت التحميل.

    base_name : اسم الملف بلا امتداد
    يرجع اسم الملف النهائي (مثلاً "abc.webp") أو None إذا فشل.
    """
    if not url:
        return None

    try:
        response = requests.get(url, headers=HTTP_HEADERS, timeout=20)
        response.raise_for_status()
    except Exception as e:
        print(f"   ⚠️  ما قدرتش نحمّل الصورة: {e}")
        return None

    try:
        # نعالجوها من الذاكرة مباشرة، بلا ما نكتبو الأصل على القرص
        main_filename, _ = process_image(
            io.BytesIO(response.content), target_dir, base_name
        )
        return main_filename
    except ImageProcessError as e:
        print(f"   ⚠️  ما قدرتش نعالج الصورة: {e}")
        return None


def fetch_all_products():
    """يجيب كل المنتجات من API متجر YouCan، صفحة بصفحة، حتى توصل صفحة فارغة."""
    all_products = []
    page = 1
    max_pages = 200  # سقف أمان باش ما يبقاش يدور للأبد إذا صار خطأ غير متوقع

    while page <= max_pages:
        url = f"{YOUCAN_STORE_URL}/api/products?page={page}"
        print(f"📥 نجيب صفحة {page}...")
        response = requests.get(url, headers=HTTP_HEADERS, timeout=20)

        if response.status_code != 200:
            print(f"❌ خطأ فجلب الصفحة {page}: HTTP {response.status_code}")
            break

        payload = response.json()
        items = payload.get("data", [])

        if not items:
            print(f"   (صفحة {page} فارغة — وصلنا آخر المنتجات)")
            break

        all_products.extend(items)
        print(f"   ({len(items)} منتج فهاد الصفحة، المجموع لحد دابا: {len(all_products)})")

        page += 1
        time.sleep(0.3)

    return all_products


def fetch_categories_map():
    """
    يبني خريطة {product_slug: category_dict}.

    ⚠️ السبب: رد /api/products/<slug> **ما فيهش** حقل categories إطلاقًا
    (تأكدنا من البيانات الحقيقية ديال متجرك). فالحل هو العكس: نجيبو التصنيفات
    مع منتجاتها، ونبنيو الربط بأنفسنا.

    الطريقة المستعملة: /api/categories?include=products
    هذا مسار عمومي — ما يحتاجش توكن ولا مفاتيح.
    """
    print("\n📂 نجيبو التصنيفات...")

    url = f"{YOUCAN_STORE_URL}/api/categories?include=products"

    try:
        r = requests.get(url, headers=HTTP_HEADERS, timeout=25)
    except Exception as e:
        print(f"   ⚠️  ما قدرناش نوصلو للتصنيفات: {e}")
        return {}

    if r.status_code != 200:
        print(f"   ⚠️  المسار رجع HTTP {r.status_code}")
        return _fetch_categories_fallback()

    try:
        categories = r.json().get("data", [])
    except Exception:
        print("   ⚠️  الرد ماشي JSON صالح")
        return {}

    if not isinstance(categories, list) or not categories:
        print("   ⚠️  ما لقينا حتى تصنيف فالرد")
        return {}

    print(f"   ✅ لقينا {len(categories)} تصنيف")

    mapping = {}
    for cat in categories:
        if not isinstance(cat, dict):
            continue

        # المنتجات تجي مغلّفة: {"products": {"data": [...]}} أو مباشرة كلائحة
        products_field = cat.get("products")
        if isinstance(products_field, dict):
            products = products_field.get("data", [])
        elif isinstance(products_field, list):
            products = products_field
        else:
            products = []

        linked = 0
        for prod in products:
            if isinstance(prod, dict) and prod.get("slug"):
                mapping[prod["slug"]] = cat
                linked += 1

        print(f"   • {cat.get('name')}: {linked} منتج")

    if not mapping:
        print("   ⚠️  لقينا التصنيفات لكن بلا منتجات مربوطة بيهم.")
        print("      جربو مسار بديل...")
        return _fetch_categories_fallback()

    return mapping


def _fetch_categories_fallback():
    """
    خطة بديلة: نجيبو التصنيفات، وبعدها منتجات كل تصنيف بطلب منفصل.
    أبطأ، لكن تخدم لو المسار الأول ما دعمش include=products.
    """
    try:
        r = requests.get(f"{YOUCAN_STORE_URL}/api/categories", headers=HTTP_HEADERS, timeout=20)
        if r.status_code != 200:
            return {}
        categories = r.json().get("data", [])
    except Exception:
        return {}

    if not isinstance(categories, list):
        return {}

    mapping = {}
    for cat in categories:
        if not isinstance(cat, dict) or not cat.get("slug"):
            continue

        for path in (
            f"/api/categories/{cat['slug']}/products",
            f"/api/products?category={cat['slug']}",
        ):
            try:
                r = requests.get(YOUCAN_STORE_URL + path, headers=HTTP_HEADERS, timeout=20)
                if r.status_code != 200:
                    continue
                items = r.json().get("data", [])
                if not isinstance(items, list) or not items:
                    continue

                for item in items:
                    if isinstance(item, dict) and item.get("slug"):
                        mapping[item["slug"]] = cat
                print(f"   • {cat.get('name')}: {len(items)} منتج")
                break
            except Exception:
                continue

        time.sleep(0.2)

    return mapping


def fetch_admin_api_products():
    """
    يجيب المنتجات من Admin API — يحتاج توكن، لكن يعطي بيانات أغنى بكثير:
    التصنيفات، **والتنويعات بأسعارها ومخزونها** (اللي ما تجيش فالمسار العمومي).

    التوكن يتحط فـ.env:
        YOUCAN_ACCESS_TOKEN=xxxxx

    ⚠️ التوكن سري — ما يتكتبش فالكود ولا يترفع لـGit.
    يرجع dict فارغ إذا ماكانش توكن أو فشل الطلب.
    """
    token = os.environ.get("YOUCAN_ACCESS_TOKEN", "").strip()
    if not token:
        return {}

    print("\n🔑 لقينا توكن Admin API — نجربو نجيبو بيانات أغنى...")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    all_items = {}
    page = 1

    while page <= 50:
        url = f"https://api.youcan.shop/products?include=categories,variants,images&page={page}"
        try:
            r = requests.get(url, headers=headers, timeout=25)
        except Exception as e:
            print(f"   ⚠️  خطأ فالاتصال: {e}")
            break

        if r.status_code == 401:
            print("   ❌ التوكن غير صالح أو منتهي — نكملو بالمسار العمومي.")
            return {}
        if r.status_code == 403:
            print("   ❌ التوكن ما عندوش الصلاحيات اللازمة (read-products, read-categories).")
            return {}
        if r.status_code != 200:
            print(f"   ⚠️  HTTP {r.status_code} — نكملو بالمسار العمومي.")
            break

        try:
            items = r.json().get("data", [])
        except Exception:
            break

        if not items:
            break

        for item in items:
            if isinstance(item, dict) and item.get("slug"):
                all_items[item["slug"]] = item

        page += 1
        time.sleep(0.3)

    if all_items:
        print(f"   ✅ جبنا {len(all_items)} منتج ببيانات كاملة (تصنيفات + تنويعات بأسعارها)")

    return all_items


def fetch_product_detail(slug):
    """
    يجيب التفاصيل الكاملة لمنتج وحد (صور متعددة، variants، categories، وصف كامل)
    عبر رابط المنتج الفردي فالـAPI. إذا الرابط ما خدمش، يرجع None ونكتفو بالبيانات
    المختصرة من قائمة /api/products.
    """
    url = f"{YOUCAN_STORE_URL}/api/products/{slug}"
    try:
        response = requests.get(url, headers=HTTP_HEADERS, timeout=20)
        if response.status_code != 200:
            print(f"   ⚠️  تفاصيل المنتج غير متاحة (HTTP {response.status_code})")
            return None
        payload = response.json()
        # بعض الأحيان الرد يكون {"data": {...}} وأحيانًا المنتج مباشرة
        return payload.get("data", payload)
    except Exception as e:
        print(f"   ⚠️  ما قدرتش نجيب تفاصيل المنتج '{slug}': {e}")
        return None


def get_or_create_category(raw_category):
    """
    ياخذ dict ديال تصنيف من بيانات YouCan (بأي شكل كان) ويرجع (أو يخلق) سطر
    Category فقاعدة البيانات. يرجع None إذا البيانات ماشي شكل تصنيف صحيح.
    """
    if not isinstance(raw_category, dict):
        return None

    name = raw_category.get("name")
    if not name:
        return None

    slug = raw_category.get("slug") or slugify_fallback(name, name)

    existing = Category.query.filter_by(slug=slug).first()
    if existing:
        return existing

    image_filename = None
    # الصورة ممكن تجي بأشكال مختلفة حسب الرد
    image_url = None
    images_field = raw_category.get("images")
    if isinstance(images_field, dict):
        image_url = images_field.get("original") or images_field.get("md") or images_field.get("sm")
    elif isinstance(raw_category.get("image"), str):
        image_url = raw_category.get("image")

    if image_url:
        image_filename = download_image(image_url, slug, CATEGORY_IMAGES_DIR)

    category = Category(name=name, slug=slug, image_filename=image_filename, is_active=True)
    db.session.add(category)
    db.session.flush()  # باش ناخذو الـid بلا ما نديرو commit كامل دابا
    print(f"      🏷️  تصنيف جديد: {name}")
    return category


def extract_images(raw_product, fallback_thumbnail=None):
    """
    يرجع لائحة روابط صور المنتج، من أول شكل بيانات موجود فالرد (defensive).
    """
    urls = []

    images_field = raw_product.get("images")
    if isinstance(images_field, list):
        # نرتبو حسب "order" إذا كاين
        sorted_images = sorted(images_field, key=lambda i: i.get("order", 0)) if all(
            isinstance(i, dict) for i in images_field
        ) else images_field
        for img in sorted_images:
            if isinstance(img, dict):
                variations = img.get("variations") or {}
                url = img.get("url") or variations.get("original") or variations.get("md")
                if url:
                    urls.append(url)
            elif isinstance(img, str):
                urls.append(img)

    if not urls and fallback_thumbnail:
        urls.append(fallback_thumbnail)

    return urls


def extract_variants(raw_product):
    """
    يرجع لائحة dicts منظفة للـvariants.

    ⚠️ اكتشاف مهم من البيانات الحقيقية:
    YouCan ما ترجعش مفتاح "variants" فرد المتجر العمومي. اللي ترجعو هو
    **"variant_options"** — لائحة الخيارات وقيمها الممكنة:

        [{"name": "النوع", "type": 2, "values": ["دوري", "أرجونتي"]}]

    يعني ما عندناش أسعار ولا مخزون لكل تنويعة (هاديك تحتاج API خاص بمفتاح
    المتجر). فنبنيو التنويعات من القيم، وكلها تورث سعر المنتج الأساسي.

    وإذا كان أكثر من خيار (مثلاً "العرض" × "اللون")، نولّدو كل التركيبات
    الممكنة — لأن هذا هو اللي كيصرا فعليًا عند YouCan.
    """
    # ⚠️ ترتيب الأولوية مهم:
    # Admin API يرجع "variants" (كاملة بأسعار ومخزون) **و** "variant_options"
    # (أسماء فقط). خاصنا ناخذو الأولى — وإلا نضيّعو الأسعار الحقيقية.
    full_variants = raw_product.get("variants")

    # الرد يجي مغلّف أحيانًا: {"variants": {"data": [...]}}
    if isinstance(full_variants, dict):
        full_variants = full_variants.get("data")

    if isinstance(full_variants, list) and full_variants:
        extracted = _extract_full_variants(full_variants)
        if extracted:
            return extracted

    # المسار العمومي: ما عندنا غير أسماء الخيارات
    options = raw_product.get("variant_options")
    if not isinstance(options, list) or not options:
        return []

    # نجمعو قيم كل خيار.
    # ⚠️ القيم تجي بشكلين حسب نوع الخيار:
    #   - نص عادي:  "دوري"
    #   - dict للألوان: {"value": "أبيض", "preview": "#ffffff"}
    # بلا معالجة الثانية، الاسم يطلع '{"value": "أبيض", ...}' — قبيح للزبون.
    value_groups = []
    for opt in options:
        if not isinstance(opt, dict):
            continue

        values = opt.get("values")
        if not isinstance(values, list) or not values:
            continue

        clean_values = []
        for v in values:
            if isinstance(v, dict):
                label = v.get("value") or v.get("name") or v.get("label")
                if label:
                    clean_values.append(str(label))
            elif v is not None:
                clean_values.append(str(v))

        if clean_values:
            value_groups.append(clean_values)

    if not value_groups:
        return []

    # نولّدو كل التركيبات: [["1سم","2سم"], ["دوري","فضي"]]
    #   → "1سم / دوري"، "1سم / فضي"، "2سم / دوري"، "2سم / فضي"
    from itertools import product as _cartesian

    combos = list(_cartesian(*value_groups))

    # سقف أمان: 3 خيارات × 10 قيم = 1000 تنويعة، ما عندوش معنى
    MAX_VARIANTS = 60
    if len(combos) > MAX_VARIANTS:
        print(
            f"   ⚠️  {len(combos)} تركيبة ممكنة — أخذنا أول {MAX_VARIANTS} فقط. "
            "راجعها من لوحة التحكم."
        )
        combos = combos[:MAX_VARIANTS]

    cleaned = []
    for i, combo in enumerate(combos):
        cleaned.append({
            "label": " / ".join(combo),
            "price": None,           # يرث سعر المنتج — الأسعار الفردية غير متاحة هنا
            "compare_at_price": None,
            "stock": 0,              # المخزون مفتوح فالموقع، فالرقم ما يهمش
            "sku": None,
            "image_url": None,
            "is_default": i == 0,
        })

    return cleaned


def _extract_full_variants(variants_field):
    """
    استخراج التنويعات من الشكل الكامل (لو الـAPI رجعو).
    هاد الشكل فيه أسعار ومخزون وصور لكل تنويعة.
    """
    cleaned = []

    for v in variants_field:
        if not isinstance(v, dict):
            continue

        label = None
        values = v.get("values")
        if isinstance(values, list) and values:
            label = " / ".join(str(x) for x in values)
        elif isinstance(v.get("variations"), dict) and v["variations"]:
            label = " / ".join(str(x) for x in v["variations"].values())
        if not label:
            label = v.get("sku") or "تنويعة"

        image_url = None
        image_field = v.get("image")
        if isinstance(image_field, dict):
            image_url = image_field.get("url")

        cleaned.append({
            "label": label,
            "price": v.get("price"),
            "compare_at_price": v.get("compare_at_price"),
            "stock": v.get("inventory") if v.get("inventory") is not None else 0,
            "sku": v.get("sku"),
            "image_url": image_url,
            "is_default": bool(v.get("is_default")),
        })

    return cleaned


def run():

    app = create_app()
    with app.app_context():
        db.create_all()

        raw_products = fetch_all_products()
        print(f"\n✅ لقيت {len(raw_products)} منتج فمتجر YouCan.")

        # 1) نجربو Admin API أولاً — يعطي أغنى بيانات (تنويعات بأسعارها)
        admin_products = fetch_admin_api_products()

        # 2) التصنيفات ما تجيش مع المنتج فالمسار العمومي — نبنيو الخريطة
        categories_map = {}
        if not admin_products:
            categories_map = fetch_categories_map()
        print()

        added, skipped = 0, 0

        for item in raw_products:
            slug = item.get("slug") or slugify_fallback(item["name"], item["id"])

            existing = Product.query.filter_by(slug=slug).first()
            if existing:
                skipped += 1
                continue

            print(f"   🔎 {item['name']}")

            # نفضّلو بيانات Admin API (أغنى)، وإلا نرجعو للمسار العمومي
            admin_data = admin_products.get(slug)

            if admin_data:
                source = admin_data
                detail = admin_data
            else:
                detail = fetch_product_detail(slug)
                source = detail if detail else item

            # --- التصنيف ---
            category = None

            # 1) من Admin API (يجي داخل المنتج مباشرة)
            admin_cats = source.get("categories")
            if isinstance(admin_cats, dict):
                admin_cats = admin_cats.get("data", [])
            if isinstance(admin_cats, list) and admin_cats:
                category = get_or_create_category(admin_cats[0])

            # 2) من الخريطة اللي بنيناها من مسار التصنيفات
            if not category:
                mapped = categories_map.get(slug)
                if mapped:
                    category = get_or_create_category(mapped)

            # 3) احتياط أخير
            if not category:
                categories_field = (
                    source.get("categories")
                    or source.get("category")
                    or item.get("categories")
                    or item.get("category")
                )
                if isinstance(categories_field, dict):
                    categories_field = [categories_field]
                if isinstance(categories_field, list) and categories_field:
                    category = get_or_create_category(categories_field[0])

            # --- الوصف: نحول من HTML لنص واضح ---
            raw_description_html = source.get("description") or ""
            plain_description = html_to_text(raw_description_html)

            # --- السعر والعرض ---
            raw_price = source.get("price") or 0
            price = int(round(raw_price))
            raw_compare = source.get("compare_at_price")
            compare_at_price = int(round(raw_compare)) if raw_compare else None

            product = Product(
                name=item["name"],
                slug=slug,
                description=plain_description,
                description_html=raw_description_html,
                price=price,
                compare_at_price=compare_at_price,
                category=category,
                color_variant=None,
                stock_quantity=source.get("inventory") or 0,
                image_filename=None,  # نعمرها بعد من أول صورة فالمعرض
                is_active=True,
            )
            db.session.add(product)
            db.session.flush()  # باش ناخذو product.id قبل ما نزيدو الصور/الـvariants

            # --- الصور المتعددة ---
            image_urls = extract_images(source, fallback_thumbnail=item.get("thumbnail"))
            first_image_filename = None
            for idx, img_url in enumerate(image_urls):
                image_filename = download_image(img_url, f"{slug}-{idx}")
                if image_filename:
                    db.session.add(
                        ProductImage(product_id=product.id, image_filename=image_filename, sort_order=idx)
                    )
                    if first_image_filename is None:
                        first_image_filename = image_filename

            if first_image_filename:
                product.image_filename = first_image_filename

            # --- التنويعات (variants) ---
            variants_data = extract_variants(source)
            for v in variants_data:
                variant_image_filename = None
                if v["image_url"]:
                    safe_label = re.sub(r"[^a-zA-Z0-9]", "", v["label"])[:20] or "v"
                    variant_image_filename = download_image(
                        v["image_url"], f"{slug}-variant-{safe_label}"
                    )

                db.session.add(
                    ProductVariant(
                        product_id=product.id,
                        label=v["label"],
                        price=int(round(v["price"])) if v["price"] else None,
                        compare_at_price=int(round(v["compare_at_price"])) if v["compare_at_price"] else None,
                        stock_quantity=v["stock"] or 0,
                        sku=v["sku"],
                        image_filename=variant_image_filename,
                        is_default=v["is_default"],
                    )
                )

            # --- تشخيص: نسجّلو بيانات كل منتج، ماشي غير الأول ---
            _debug_products.append({
                "name": item.get("name"),
                "slug": slug,
                "has_detail_endpoint": detail is not None,
                "raw": source,
            })

            _debug_analysis.append({
                "name": item.get("name"),
                "slug": slug,
                "detail_ok": detail is not None,
                "category": category.name if category else None,
                "categories_raw_type": type(source.get("categories")).__name__,
                "images_found": len(image_urls),
                "images_saved": len([i for i in image_urls]) if first_image_filename else 0,
                "variants_found": len(variants_data),
                "variants_raw_type": type(source.get("variant_options")).__name__,
                "variant_option_names": [
                    o.get("name") for o in (source.get("variant_options") or [])
                    if isinstance(o, dict)
                ],
                "has_description": bool(raw_description_html),
                "price": price,
                "available_keys": sorted(source.keys()),
            })

            added += 1
            print(
                f"      ✅ تصنيف: {category.name if category else 'بلا تصنيف'} | "
                f"صور: {len(image_urls)} | تنويعات: {len(variants_data)}"
            )

            db.session.commit()  # نحفظو منتج بمنتج، باش لو صار خطأ فمنتج، ما نخسروش اللي قبلو

        # ---------- حفظ ملفات التشخيص ----------
        _write_debug_files()

        # ---------- تقرير التشخيص ----------
        from models import ProductVariant as _PV

        cat_count = Category.query.count()
        var_count = _PV.query.count()
        img_count = ProductImage.query.count()

        print(f"\n{'='*54}")
        print(f"🎉 خلص! زدنا {added} منتج جديد، تخطينا {skipped} (موجودين من قبل).")
        print(f"\n📊 النتيجة:")
        print(f"   التصنيفات: {cat_count}")
        print(f"   التنويعات: {var_count}")
        print(f"   الصور:     {img_count}")

        _print_analysis()


def _write_debug_files():
    """
    يحفظ ملفين للتشخيص:
      - debug_all_products.json : البيانات الخام لكل المنتجات
      - debug_report.txt        : تقرير مقروء يحلل واش لقينا فكل منتج
    """
    if not _debug_products:
        return

    with open("debug_all_products.json", "w", encoding="utf-8") as f:
        json.dump(_debug_products, f, ensure_ascii=False, indent=2)

    lines = []
    lines.append("=" * 70)
    lines.append("تقرير تشخيص الاستيراد من YouCan")
    lines.append("=" * 70)
    lines.append(f"عدد المنتجات المفحوصة: {len(_debug_analysis)}")
    lines.append("")

    # ---- تفصيل كل منتج ----
    # ⚠️ ما نستعملوش جدول بأعمدة: عرض الحروف العربية فالـterminal غير ثابت،
    # فالمحاذاة تتكسر. سطر لكل منتج أوضح وأضمن.
    lines.append("-" * 70)
    lines.append("تفصيل كل منتج:")
    lines.append("-" * 70)

    for i, a in enumerate(_debug_analysis, 1):
        issues = []
        if not a["detail_ok"]:
            issues.append("بلا تفاصيل موسّعة")
        if not a["category"]:
            issues.append("بلا تصنيف")
        if a["images_found"] == 0:
            issues.append("بلا صور")

        status = "✓ كامل" if not issues else "⚠ " + "، ".join(issues)

        lines.append(f"{i}. {a['name']}")
        lines.append(f"   slug: {a['slug']}")
        opts = a.get("variant_option_names") or []
        opts_text = f" ({'، '.join(opts)})" if opts else ""
        lines.append(
            f"   صور: {a['images_found']} | تنويعات: {a['variants_found']}{opts_text} | "
            f"تصنيف: {a['category'] or '—'}"
        )
        lines.append(f"   الحالة: {status}")
        lines.append("")

    lines.append("")
    lines.append("=" * 70)
    lines.append("تحليل الأنماط")
    lines.append("=" * 70)

    # ---- إحصائيات ----
    total = len(_debug_analysis)
    no_detail = [a for a in _debug_analysis if not a["detail_ok"]]
    no_cat = [a for a in _debug_analysis if not a["category"]]
    no_img = [a for a in _debug_analysis if a["images_found"] == 0]
    no_var = [a for a in _debug_analysis if a["variants_found"] == 0]
    with_var = [a for a in _debug_analysis if a["variants_found"] > 0]

    lines.append(f"تفاصيل موسّعة متاحة : {total - len(no_detail)}/{total}")
    lines.append(f"عندهم تصنيف        : {total - len(no_cat)}/{total}")
    lines.append(f"عندهم صور          : {total - len(no_img)}/{total}")
    lines.append(f"عندهم تنويعات      : {len(with_var)}/{total}")
    lines.append("")

    if no_detail:
        lines.append(f"⚠️ {len(no_detail)} منتج ما رجعش تفاصيل موسّعة:")
        for a in no_detail[:10]:
            lines.append(f"   - {a['name']} (slug: {a['slug']})")
        if len(no_detail) > 10:
            lines.append(f"   ... و{len(no_detail) - 10} آخرين")
        lines.append("")

    if no_cat:
        lines.append(f"⚠️ {len(no_cat)} منتج بلا تصنيف. أشكال حقل categories اللي لقيناها:")
        types_seen = {}
        for a in no_cat:
            t = a["categories_raw_type"]
            types_seen[t] = types_seen.get(t, 0) + 1
        for t, count in types_seen.items():
            lines.append(f"   - {t}: {count} منتج")
        lines.append("")

    if no_var and with_var:
        lines.append(
            f"ℹ️ {len(no_var)} منتج بلا تنويعات و{len(with_var)} عندهم — "
            "هذا عادي إذا بعض المنتجات ماعندهاش ألوان/مقاسات."
        )
        lines.append("")
    elif no_var and not with_var:
        lines.append("⚠️ ولا منتج عندو تنويعات. أشكال حقل variant_options:")
        types_seen = {}
        for a in _debug_analysis:
            t = a["variants_raw_type"]
            types_seen[t] = types_seen.get(t, 0) + 1
        for t, count in types_seen.items():
            lines.append(f"   - {t}: {count} منتج")
        lines.append("")

    # ---- المفاتيح المتاحة (مفيدة باش نعرفو شكل البيانات) ----
    all_keys = {}
    for a in _debug_analysis:
        for k in a["available_keys"]:
            all_keys[k] = all_keys.get(k, 0) + 1

    lines.append("-" * 70)
    lines.append("المفاتيح الموجودة فبيانات YouCan (وعدد المنتجات اللي فيها):")
    lines.append("-" * 70)
    for k, count in sorted(all_keys.items(), key=lambda x: -x[1]):
        marker = "  " if count == total else "⚠ "
        lines.append(f"{marker}{k:<32} {count}/{total}")

    with open("debug_report.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _print_analysis():
    """يطبع خلاصة التشخيص فالـterminal."""
    if not _debug_analysis:
        return

    total = len(_debug_analysis)
    no_detail = sum(1 for a in _debug_analysis if not a["detail_ok"])
    no_cat = sum(1 for a in _debug_analysis if not a["category"])
    with_var = sum(1 for a in _debug_analysis if a["variants_found"] > 0)
    no_img = sum(1 for a in _debug_analysis if a["images_found"] == 0)

    print("\n" + "=" * 54)
    print("🔍 تشخيص مفصّل")
    print("=" * 54)
    print(f"   تفاصيل موسّعة : {total - no_detail}/{total}")
    print(f"   عندهم تصنيف   : {total - no_cat}/{total}")
    print(f"   عندهم صور     : {total - no_img}/{total}")
    print(f"   عندهم تنويعات : {with_var}/{total}")

    problems = []
    if no_detail == total:
        problems.append(
            "❌ ولا منتج رجع تفاصيل موسّعة — رابط /api/products/<slug> ماخدامش.\n"
            "   بلاه ما نقدروش نجيبو التصنيفات ولا التنويعات ولا الصور المتعددة."
        )
    elif no_detail:
        problems.append(f"⚠️  {no_detail} منتج ما رجعش تفاصيل موسّعة.")

    if no_cat == total:
        problems.append(
            "ℹ️  ولا منتج عندو تصنيف.\n"
            "   هذا معروف: رد YouCan العمومي ما فيهش التصنيفات مع المنتج،\n"
            "   وحاولنا نجيبوهم من مسار منفصل وما نجحش.\n"
            "   الحل: صنّفهم يدويًا من لوحة التحكم ← المنتجات ← تعديل."
        )

    if with_var == 0:
        problems.append(
            "⚠️  ولا منتج عندو تنويعات. إذا منتجاتك فـYouCan عندها ألوان/مقاسات،\n"
            "   شوف debug_report.txt وابعثهولي."
        )

    if problems:
        print()
        for p in problems:
            print(p)

    print("\n📁 ملفات التشخيص:")
    print("   debug_report.txt        ← تقرير مقروء (ابدا بيه)")
    print("   debug_all_products.json ← بيانات كل المنتجات الخام")
    print("\n   إذا كان شي مشكل، ابعثلي debug_report.txt.")


if __name__ == "__main__":
    run()
