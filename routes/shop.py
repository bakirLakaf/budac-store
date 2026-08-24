from flask import Blueprint, render_template, abort, request, jsonify, url_for, redirect, flash, Response

from sqlalchemy.orm import joinedload

from sqlalchemy import func

from models import db, Product, Category, Offer, OfferItem, OrderItem, Order, Review

shop_bp = Blueprint("shop", __name__)


@shop_bp.route("/")
def home():
    category_slug = request.args.get("category")

    query = Product.query.options(
        joinedload(Product.category), joinedload(Product.variants)
    ).filter_by(is_active=True)
    if category_slug:
        query = query.join(Category).filter(Category.slug == category_slug)

    products = query.order_by(Product.created_at.desc()).all()

    # ⚠️ للفلترة الفورية بلا إعادة تحميل: نبعثو **كل** المنتجات للقالب،
    # والجافاسكريبت يخبّي/يبيّن حسب التصنيف. الفلترة من السيرفر (?category=)
    # تبقى تخدم للروابط المباشرة ولمحركات البحث.
    all_products = (
        Product.query
        .options(joinedload(Product.category), joinedload(Product.variants))
        .filter_by(is_active=True)
        .order_by(Product.created_at.desc())
        .all()
    )

    categories = Category.query.filter_by(is_active=True).order_by(Category.name).all()

    # العروض الخاصة تبان فوق فالصفحة الرئيسية — غير اللي شغالة فعليًا:
    # مفعّلة + داخل مدتها + عندها منتجات + كل منتجاتها متوفرة.
    # نفلترو فبايثون ماشي فSQL لأن الشروط تعتمد على خصائص محسوبة (is_running/is_available).
    offers = []
    if not category_slug:  # ما نعرضوهمش كي الزبون يفلتر بتصنيف معين
        # joinedload يجيب العروض ومنتجاتها وتنويعاتها فاستعلام واحد.
        # بلاه، صفحة فيها 5 عروض × 3 منتجات = عشرات الاستعلامات (مشكلة N+1).
        candidates = (
            Offer.query
            .options(joinedload(Offer.items).joinedload(OfferItem.product).joinedload(Product.variants))
            .order_by(Offer.sort_order, Offer.created_at.desc())
            .all()
        )
        offers = [o for o in candidates if o.is_running and o.items and o.is_available]

    # الأقسام الإضافية تبان غير فالصفحة الكاملة (ماشي كي يفلتر بتصنيف)
    newest, best_sellers = [], []
    if not category_slug:
        newest = [p for p in products if p.is_in_stock][:8]
        best_sellers = _find_best_sellers(limit=8)

    # العرض المميّز (قسم الصورة الكبيرة) — واحد فقط، فوق أو تحت
    spotlight = _find_spotlight_offer()

    # تقييمات الزبائن — تبان فوق التذييل
    testimonials = _find_testimonials() if not category_slug else []

    return render_template(
        "shop/home.html",
        testimonials=testimonials,
        products=products,
        all_products=all_products,
        categories=categories,
        active_category=category_slug,
        offers=offers,
        newest=newest,
        best_sellers=best_sellers,
        spotlight=spotlight,
    )


def _find_testimonials(limit=12):
    """
    تقييمات الزبائن للسلايدر فالصفحة الرئيسية.

    ⚠️ قواعد الاختيار:
      • **معتمدة فقط** — الأدمن يوافق قبل ما تبان. تقييم مسيء ولا
        سبام ما يوصلش للصفحة الرئيسية.
      • **فيها تعليق مكتوب** — نجمة بلا كلام ما تقنع حد.
      • **4 نجوم وفوق** — هاد القسم للثقة. التقييمات المنخفضة تبقى
        بانة فصفحة المنتج نفسو (ماشي إخفاء، غير ماشي مكانها هنا).
      • **عشوائية** فكل تحميل — الزائر اللي يرجع يشوف تقييمات أخرى.

    ترجع قائمة فارغة إذا ماكانش — والقالب ما يعرضش القسم أصلاً،
    أحسن من سلايدر فارغ.
    """
    from models import Review

    return (
        Review.query
        .options(joinedload(Review.product))
        .filter(
            Review.is_approved.is_(True),
            Review.rating >= 4,
            Review.comment.isnot(None),
            func.length(func.trim(Review.comment)) >= 10,
        )
        .order_by(func.random())
        .limit(limit)
        .all()
    )


def _find_spotlight_offer():
    """
    يجيب العرض المخصّص لقسم "عرض خاص" بالصورة الكبيرة.

    يرجع None إذا ماكانش — والقالب ما يعرضش القسم أصلاً.
    إذا الأدمن فعّل أكثر من عرض، ناخذو الأول حسب sort_order.
    """
    candidates = (
        Offer.query
        .options(joinedload(Offer.items).joinedload(OfferItem.product))
        .filter(Offer.spotlight_position.in_(["top", "bottom"]))
        .order_by(Offer.sort_order, Offer.created_at.desc())
        .all()
    )

    for offer in candidates:
        if offer.is_running and offer.items:
            return offer

    return None


def _find_best_sellers(limit=8):
    """
    الأكثر طلبًا — محسوبة من الطلبيات الحقيقية (المؤكدة والمسلّمة فقط).

    ⚠️ نستثنيو الطلبيات الجديدة والملغاة عمدًا: طلبية ما اتأكدتش مازال ممكن
    تكون وهمية، فما نبنيوش عليها "الأكثر مبيعًا".

    إذا مازال ماكانش عندنا مبيعات كافية (متجر جديد)، نرجعو قائمة فارغة
    والقالب ما يعرضش القسم — أحسن من عرض قسم فارغ أو ترتيب عشوائي كاذب.
    """
    rows = (
        db.session.query(OrderItem.product_id, func.sum(OrderItem.quantity).label("sold"))
        .join(Order, Order.id == OrderItem.order_id)
        .filter(
            OrderItem.product_id.isnot(None),
            Order.status.in_([Order.STATUS_CONFIRMED, Order.STATUS_SHIPPED, Order.STATUS_DELIVERED]),
        )
        .group_by(OrderItem.product_id)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(limit)
        .all()
    )

    # ⚠️ عتبة الثقة: أقل من 5 منتجات مباعة يعني الأرقام مازال ضعيفة
    # وما تعكسش الحقيقة. فنستعملو الاختيار اليدوي حتى نوصلو للعتبة،
    # وبعدها القسم يتحوّل أوتوماتيكيًا للمبيعات الحقيقية.
    MIN_PRODUCTS_SOLD = 5

    if len(rows) < MIN_PRODUCTS_SOLD:
        return _find_featured_products(limit)

    product_ids = [r[0] for r in rows]
    products = (
        Product.query
        .options(joinedload(Product.variants))
        .filter(Product.id.in_(product_ids), Product.is_active.is_(True))
        .all()
    )

    # نحافظو على ترتيب المبيعات (الاستعلام أعلاه يرجع الترتيب، لكن IN ما يحافظش عليه)
    order_map = {pid: i for i, pid in enumerate(product_ids)}
    products.sort(key=lambda p: order_map.get(p.id, 999))

    result = [p for p in products if p.is_in_stock]
    return result or _find_featured_products(limit)


def _find_featured_products(limit=8):
    """
    المنتجات المختارة يدويًا من لوحة التحكم (خانة "مميّز").
    تُستعمل حتى يجمع المتجر مبيعات حقيقية كافية.
    """
    return (
        Product.query
        .options(joinedload(Product.variants), joinedload(Product.category))
        .filter(Product.is_active.is_(True), Product.is_featured.is_(True))
        .order_by(Product.created_at.desc())
        .limit(limit)
        .all()
    )


@shop_bp.route("/product/<slug>")
def product_detail(slug):
    product = Product.query.filter_by(slug=slug, is_active=True).first()
    if product is None:
        abort(404)

    related = _find_related_products(product, limit=4)

    reviews = (
        Review.query
        .filter_by(product_id=product.id, is_approved=True)
        .order_by(Review.created_at.desc())
        .all()
    )

    avg_rating = round(sum(r.rating for r in reviews) / len(reviews), 1) if reviews else None

    return render_template(
        "shop/product.html",
        product=product, related=related,
        reviews=reviews, avg_rating=avg_rating,
    )


@shop_bp.route("/reviews", methods=["GET"])
def store_reviews():
    """
    صفحة تقييمات المتجر — رأي الزبائن فالخدمة كاملة، ماشي فمنتج.

    ⚠️ الفرق عن تقييمات المنتج: هاذي على التوصيل، التغليف، سرعة الرد…
    الزبون اللي يحكم على متجر جديد يقرا هاذي أكثر من تقييم منتج واحد.
    """
    reviews = (
        Review.query
        .filter(Review.product_id.is_(None), Review.is_approved.is_(True))
        .order_by(Review.created_at.desc())
        .limit(100)
        .all()
    )
    avg, count = Review.store_stats()

    # توزيع النجوم — يوري الشفافية أكثر من رقم واحد
    breakdown = {n: 0 for n in range(1, 6)}
    for r in reviews:
        breakdown[r.rating] = breakdown.get(r.rating, 0) + 1

    return render_template(
        "shop/reviews.html",
        reviews=reviews, avg=avg, count=count, breakdown=breakdown,
    )


@shop_bp.route("/reviews", methods=["POST"])
def add_store_review():
    """
    استقبال تقييم للمتجر.

    ⚠️ كيما تقييم المنتج: ما يبانش قبل موافقة الأدمن.
    """
    name = (request.form.get("author_name") or "").strip()
    rating = request.form.get("rating", type=int)
    comment = (request.form.get("comment") or "").strip()

    if not name or len(name) < 2:
        flash("اكتب اسمك", "error")
    elif not rating or not (1 <= rating <= 5):
        flash("اختر عدد النجوم", "error")
    else:
        db.session.add(Review(
            product_id=None,          # ← تقييم للمتجر
            author_name=name[:120],
            rating=rating,
            comment=comment[:1000] or None,
            is_approved=False,
        ))
        db.session.commit()
        flash("شكرًا على رأيك! غادي يبان بعد المراجعة.", "success")

    return redirect(url_for("shop.store_reviews") + "#form")


@shop_bp.route("/product/<slug>/review", methods=["POST"])
def add_review(slug):
    """
    استقبال تقييم من زبون.

    ⚠️ التقييم ما يبانش مباشرة — يحتاج موافقة الأدمن.
    بلا هذا، أي واحد يقدر يكتب سبام أو إساءة ويبان فورًا فمتجرك.
    """
    product = Product.query.filter_by(slug=slug, is_active=True).first_or_404()

    name = (request.form.get("author_name") or "").strip()
    rating = request.form.get("rating", type=int)
    comment = (request.form.get("comment") or "").strip()

    if not name or len(name) < 2:
        flash("اكتب اسمك", "error")
    elif not rating or not (1 <= rating <= 5):
        flash("اختر عدد النجوم", "error")
    else:
        db.session.add(Review(
            product_id=product.id,
            author_name=name[:120],
            rating=rating,
            comment=comment[:1000] or None,
            is_approved=False,
        ))
        db.session.commit()
        flash("شكرًا على تقييمك! غادي يبان بعد المراجعة.", "success")

    return redirect(url_for("shop.product_detail", slug=slug) + "#reviews")


@shop_bp.route("/sitemap.xml")
def sitemap():
    """
    خريطة الموقع لمحركات البحث — تساعد جوجل يلقى كل صفحاتك بسرعة.
    """
    pages = []
    base = request.url_root.rstrip("/")

    pages.append((base + url_for("shop.home"), "daily", "1.0"))

    for slug in STATIC_PAGES:
        pages.append((base + url_for("shop.page", slug=slug), "monthly", "0.4"))

    for cat in Category.query.filter_by(is_active=True).all():
        pages.append((base + url_for("shop.home", category=cat.slug), "weekly", "0.7"))

    for product in Product.query.filter_by(is_active=True).all():
        pages.append((base + url_for("shop.product_detail", slug=product.slug), "weekly", "0.9"))

    for offer in Offer.query.filter_by(is_active=True).all():
        if offer.is_running:
            pages.append((base + url_for("shop.offer_detail", slug=offer.slug), "daily", "0.8"))

    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, freq, priority in pages:
        xml.append(f"  <url><loc>{loc}</loc><changefreq>{freq}</changefreq><priority>{priority}</priority></url>")
    xml.append("</urlset>")

    return Response("\n".join(xml), mimetype="application/xml")


@shop_bp.route("/robots.txt")
def robots():
    """
    يقول لمحركات البحث واش تفهرس وواش لا.
    نمنعو لوحة التحكم والسلة — ما عندهمش فايدة فنتائج البحث.
    """
    base = request.url_root.rstrip("/")
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /cart",
        "Disallow: /checkout",
        "Disallow: /api/",
        "",
        f"Sitemap: {base}/sitemap.xml",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


def _find_related_products(product, limit=4):
    """
    منتجات مشابهة. الأولوية:
      1. نفس التصنيف
      2. إذا ما كفاوش (أو المنتج بلا تصنيف)، نكمّلو بأحدث المنتجات

    نستثنيو المنتج نفسو دائمًا، وناخذو غير المتوفرين فالمخزون
    (ما فايدة نقترحو حاجة ما نقدروش نبيعوها).
    """
    related = []

    if product.category_id:
        related = (
            Product.query.filter(
                Product.category_id == product.category_id,
                Product.id != product.id,
                Product.is_active.is_(True),
                Product.stock_quantity > 0,
            )
            .order_by(Product.created_at.desc())
            .limit(limit)
            .all()
        )

    if len(related) < limit:
        exclude_ids = [product.id] + [p.id for p in related]
        filler = (
            Product.query.filter(
                Product.id.notin_(exclude_ids),
                Product.is_active.is_(True),
                Product.stock_quantity > 0,
            )
            .order_by(Product.created_at.desc())
            .limit(limit - len(related))
            .all()
        )
        related.extend(filler)

    return related


@shop_bp.route("/offer/<slug>")
def offer_detail(slug):
    """صفحة تفاصيل عرض خاص."""
    offer = (
        Offer.query
        .options(joinedload(Offer.items).joinedload(OfferItem.product))
        .filter_by(slug=slug)
        .first()
    )
    if offer is None or not offer.is_running:
        abort(404)
    return render_template("shop/offer.html", offer=offer)


@shop_bp.route("/api/search-suggest")
def api_search_suggest():
    """
    اقتراحات البحث الفورية — تُستدعى وقت الكتابة.

    ⚠️ خفيفة عمدًا: 8 نتائج فقط، وما نجيبوش الوصف ولا التنويعات.
    الهدف السرعة، ماشي صفحة نتائج كاملة.
    """
    query_text = (request.args.get("q") or "").strip()

    # أقل من حرفين = نتائج بزاف وبلا فايدة
    if len(query_text) < 2:
        return jsonify({"results": []})

    pattern = f"%{query_text}%"
    products = (
        Product.query
        .options(joinedload(Product.category))
        .filter(
            Product.is_active.is_(True),
            db.or_(
                Product.name.ilike(pattern),
                Product.description.ilike(pattern),
            ),
        )
        .order_by(Product.stock_quantity.desc(), Product.created_at.desc())
        .limit(8)
        .all()
    )

    results = []
    for p in products:
        results.append({
            "name": p.name,
            "url": url_for("shop.product_detail", slug=p.slug),
            "image": (
                url_for("static", filename="img/products/" + p.image_filename)
                if p.image_filename else None
            ),
            "price": p.price_display,
            "category": p.category.name if p.category else "",
            "in_stock": p.stock_quantity > 0,
        })

    return jsonify({"results": results, "query": query_text})


@shop_bp.route("/search")
def search():
    """بحث فالمنتجات بالاسم والوصف."""
    query_text = (request.args.get("q") or "").strip()

    results = []
    if query_text:
        pattern = f"%{query_text}%"
        results = (
            Product.query
            .options(joinedload(Product.category), joinedload(Product.variants))
            .filter(
                Product.is_active.is_(True),
                db.or_(
                    Product.name.ilike(pattern),
                    Product.description.ilike(pattern),
                ),
            )
            .order_by(Product.stock_quantity.desc(), Product.created_at.desc())
            .limit(60)
            .all()
        )

    return render_template("shop/search.html", query_text=query_text, results=results)


# صفحات ثابتة — محتواها فالقالب مباشرة، ما تحتاجش قاعدة بيانات
STATIC_PAGES = {
    "about": "من نحن",
    "shipping": "التوصيل والأسعار",
    "returns": "الإرجاع والاستبدال",
}


@shop_bp.route("/page/<slug>")
def page(slug):
    if slug not in STATIC_PAGES:
        abort(404)
    return render_template(f"shop/pages/{slug}.html", page_title=STATIC_PAGES[slug])


@shop_bp.route("/api/products-by-slug")
def api_products_by_slug():
    """
    يرجع بيانات منتجات حسب قائمة slugs — يستعملها قسم "شفتها مؤخرًا".

    ⚠️ ليش API بدل ما نخزّنو فالـsession؟
    لأن "شفتها مؤخرًا" بيانات خاصة بالمتصفح، ما تستاهلش تكبير الكوكي ولا
    استعلامات إضافية فكل صفحة. المتصفح يحفظ الـslugs، ويطلب التفاصيل غير
    كي يحتاجها.
    """
    slugs = (request.args.get("slugs") or "").split(",")
    slugs = [s.strip() for s in slugs if s.strip()][:8]  # سقف أمان

    if not slugs:
        return jsonify([])

    products = (
        Product.query
        .options(joinedload(Product.category))
        .filter(Product.slug.in_(slugs), Product.is_active.is_(True))
        .all()
    )

    # نحافظو على ترتيب الزيارة (الأحدث أولاً)
    by_slug = {p.slug: p for p in products}
    ordered = [by_slug[s] for s in slugs if s in by_slug]

    return jsonify([
        {
            "name": p.name,
            "price": p.price_display,
            "url": url_for("shop.product_detail", slug=p.slug),
            "image": (
                url_for("static", filename="img/products/" + _thumb(p.image_filename))
                if p.image_filename else None
            ),
            "letter": p.name[0],
        }
        for p in ordered
    ])


def _thumb(filename):
    from services.images import thumb_name
    return thumb_name(filename)


@shop_bp.route("/api/calculate/<slug>", methods=["POST"])
def api_calculate(slug):
    """
    يحسب الكمية اللي يحتاجها الزبون.

    ⚠️ الحساب فالسيرفر ماشي فالمتصفح فقط — باش نضمنو نفس النتيجة دائمًا،
    وباش نقدرو نبدّلو المنطق بلا ما نطلبو من الزبون يفرّغ الكاش.
    """
    from services.calculators import (
        calculate_perimeter, calculate_area, CalculationError,
        CALC_PERIMETER, CALC_AREA,
    )

    product = Product.query.filter_by(slug=slug, is_active=True).first_or_404()

    if not product.calculator_type:
        return jsonify({"ok": False, "error": "هذا المنتج ما عندوش حاسبة"}), 400

    data = request.get_json(silent=True) or {}

    try:
        if product.calculator_type == CALC_PERIMETER:
            result = calculate_perimeter(
                shape=data.get("shape"),
                dimensions=data,
                roll_length_m=product.roll_length_m,
                margin_percent=product.calculator_margin,
            )
        elif product.calculator_type == CALC_AREA:
            result = calculate_area(
                surfaces=data.get("surfaces") or [],
                roll_width_m=product.roll_width_m,
                margin_percent=product.calculator_margin,
            )
        else:
            return jsonify({"ok": False, "error": "نوع حاسبة غير معروف"}), 400

    except CalculationError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception:
        return jsonify({"ok": False, "error": "صار خطأ فالحساب — راجع المدخلات"}), 400

    return jsonify({"ok": True, "result": result, "type": product.calculator_type})


@shop_bp.route("/shipping-prices")
def shipping_prices():
    """
    صفحة عامة تعرض أسعار التوصيل لكل الولايات.

    ⚠️ ليش صفحة مستقلة؟
    «شحال التوصيل؟» هو أكثر سؤال يوصل على واتساب. صفحة واضحة تجيبو
    مرة وحدة وتوفّر وقت الرد، وتزيد ثقة الزبون قبل ما يطلب.

    نعرضو فقط الشركات **المفعّلة** من لوحة التحكم، والسعر الفارغ يعني
    الخدمة غير متوفرة (ماشي مجاني).
    """
    from models import Carrier, ShippingRate

    carriers = (
        Carrier.query.filter_by(is_active=True)
        .order_by(Carrier.name)
        .all()
    )

    # نبنيو جدول: ولاية → {رمز الشركة: (منزل، مكتب)}
    rows = {}
    for carrier in carriers:
        for rate in ShippingRate.query.filter_by(carrier_id=carrier.id).all():
            entry = rows.setdefault(
                rate.wilaya_code,
                {"code": rate.wilaya_code, "name": rate.wilaya_name, "prices": {}},
            )
            entry["prices"][carrier.code] = {
                "home": rate.home_price,
                "office": rate.office_price,
            }

    wilayas = sorted(rows.values(), key=lambda r: r["code"])

    return render_template(
        "shop/shipping_prices.html",
        carriers=carriers,
        wilayas=wilayas,
    )
