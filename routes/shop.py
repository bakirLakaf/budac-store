from flask import (
    Blueprint, render_template, abort, request, jsonify, url_for,
    redirect, flash, Response, current_app,
)

from sqlalchemy.orm import joinedload

from sqlalchemy import func

from models import db, Product, Category, Offer, OfferItem, OrderItem, Order, Review
from services import ratelimit

# ⚠️ نفس دالة التقريب اللي يستعملها الشراء — باش الجدول والفاتورة
#    ما يختلفوش أبدًا. مصدر واحد للقاعدة، ماشي زوج.
from services.shipping import round_up_to_100

shop_bp = Blueprint("shop", __name__)


# ⚠️ 9 منتجات فالصفحة = 3 صفوف × 3 أعمدة فالحاسوب، وصف واحد
# منظّم فالهاتف. رقم يقسم على 3 مليح — بلاه الصف الأخير يبقى ناقصًا
# ويبان الشبكة مكسورة.
PRODUCTS_PER_PAGE = 9

# ⚠️ فوق هاد العدد، بعث الكتالوج كامل للمتصفح يولّي مكلفًا (كل بطاقة
# ~700 بايت HTML + صورة). 300 منتج ≈ 240 كيلوبايت HTML — مازال
# مقبول على 4G، لكنو السقف. فوقها السيرفر يرجع للترقيم تاعو تلقائيًا،
# فالموقع ما ينكسرش كي يكبر الكتالوج — يولّي غير أبطأ شوية فالتبديل.
MAX_CLIENT_PAGINATION = 300


def _category_counts():
    """
    عدد المنتجات النشطة فكل تصنيف + المجموع.

    ⚠️ استعلام GROUP BY واحد بدل ما نجيبو كل المنتجات ونعدّوهم
    فالقالب. قبل، الشريط الجانبي كان يجبر السيرفر يحمّل **كل**
    الكتالوج فكل زيارة باش يطبع أرقامًا صغيرة — وهذا هو بالضبط
    السبب اللي خلّى الصفحة الرئيسية تبطّئ مع نموّ المنتجات.

    يرجّع (قاموس {slug: عدد}, المجموع)
    """
    rows = (
        db.session.query(Category.slug, func.count(Product.id))
        .join(Product, Product.category_id == Category.id)
        .filter(Product.is_active.is_(True))
        .group_by(Category.slug)
        .all()
    )
    counts = {slug: count for slug, count in rows}

    # ⚠️ المجموع يتحسب لوحدو — منتج بلا تصنيف ما يبانش فالـJOIN فوق،
    # لكنو يبان فـ«كل المنتجات». جمع الأعداد يعطي رقمًا أقل من الحقيقة.
    total = Product.query.filter_by(is_active=True).count()

    return counts, total


@shop_bp.route("/")
def home():
    category_slug = request.args.get("category")

    # ⚠️ رقم صفحة غالط (نص، سالب، ضخم) ما يخصّوش يطيّح الصفحة.
    page_number = max(1, request.args.get("page", 1, type=int) or 1)

    # ══════════════════════════════════════════════════════════
    # الترقيم يصرا فالمتصفح، ماشي فالسيرفر
    # ══════════════════════════════════════════════════════════
    #
    # نبعثو **كل** المنتجات النشطة مرة وحدة، وJavaScript يعرض 9
    # فكل مرة. النتيجة: تبديل الصفحات فوري — بلا إعادة تحميل،
    # بلا انتظار الشبكة، وبلا قفزة تمرير. ونربحو معاها رجوع
    # الفلترة الفورية بالتصنيف.
    #
    # ⚠️ التنازل بصراحة: الصفحة تحمل كل المنتجات فالـHTML.
    # مع 45 منتج (الوضع الحالي) الفرق تافه. الحد المعقول حوالي
    # **300 منتج**؛ فوقها الصفحة تولّي تقيلة على اتصال الجوال،
    # ولازم نرجعو للترقيم من السيرفر أو نديرو AJAX.
    # `MAX_CLIENT_PAGINATION` تحت هي الحارس: كي يتعدّى العدد،
    # السيرفر يرجع للترقيم تاعو تلقائيًا.
    #
    # ⚠️ ?page= و?category= يبقاو يخدمو من السيرفر: الروابط
    # المشاركة والزواحف (جوجل) ما عندهمش JavaScript.
    query = Product.query.options(
        joinedload(Product.category), joinedload(Product.variants)
    ).filter_by(is_active=True)
    if category_slug:
        query = query.join(Category).filter(Category.slug == category_slug)

    query = query.order_by(Product.created_at.desc())

    categories = Category.query.filter_by(is_active=True).order_by(Category.name).all()
    category_counts, products_total = _category_counts()

    # الحارس: كتالوج ضخم يرجع للترقيم من السيرفر بلا ما نلمسو والو
    client_paginated = products_total <= MAX_CLIENT_PAGINATION

    if client_paginated:
        # كل المنتجات فالصفحة — JavaScript يقسّمهم
        products = query.all()
        pagination = None
    else:
        pagination = query.paginate(
            page=page_number, per_page=PRODUCTS_PER_PAGE, error_out=False
        )
        products = pagination.items

    # العروض الخاصة تبان فوق فالصفحة الرئيسية — غير اللي شغالة فعليًا:
    # مفعّلة + داخل مدتها + عندها منتجات + كل منتجاتها متوفرة.
    # نفلترو فبايثون ماشي فSQL لأن الشروط تعتمد على خصائص محسوبة (is_running/is_available).
    #
    # ⚠️ الأقسام الزائدة (عروض، جديد، الأكثر مبيعًا، تقييمات) تبان
    # **غير فالصفحة الأولى بلا تصنيف**. كي الزبون يفلتر ولا يتصفّح،
    # هو راه يدور على منتج معيّن — ما نعرضوش عليه صفحة كاملة مرة أخرى.
    is_landing_view = not category_slug and page_number == 1

    offers = []
    if is_landing_view:
        # joinedload يجيب العروض ومنتجاتها وتنويعاتها فاستعلام واحد.
        # بلاه، صفحة فيها 5 عروض × 3 منتجات = عشرات الاستعلامات (مشكلة N+1).
        candidates = (
            Offer.query
            .options(joinedload(Offer.items).joinedload(OfferItem.product).joinedload(Product.variants))
            .order_by(Offer.sort_order, Offer.created_at.desc())
            .all()
        )
        offers = [o for o in candidates if o.is_running and o.items and o.is_available]

    newest, best_sellers = [], []
    if is_landing_view:
        # ⚠️ استعلام مستقل بحدّو: قبل كان ياخذ من `products`، واللي
        # ولّات درك 9 منتجات برك — فقسم «الجديد» كان يفرغ.
        newest = [
            p for p in (
                Product.query
                .options(joinedload(Product.category), joinedload(Product.variants))
                .filter_by(is_active=True)
                .order_by(Product.created_at.desc())
                .limit(16).all()
            )
            if p.is_in_stock
        ][:8]
        best_sellers = _find_best_sellers(limit=8)

    # العرض المميّز (قسم الصورة الكبيرة) — واحد فقط، فوق أو تحت
    spotlight = _find_spotlight_offer()

    # تقييمات الزبائن — تبان فوق التذييل
    testimonials = _find_testimonials() if is_landing_view else []

    return render_template(
        "shop/home.html",
        testimonials=testimonials,
        products=products,
        pagination=pagination,
        client_paginated=client_paginated,
        per_page=PRODUCTS_PER_PAGE,
        categories=categories,
        category_counts=category_counts,
        products_total=products_total,
        active_category=category_slug,
        offers=offers,
        newest=newest,
        best_sellers=best_sellers,
        spotlight=spotlight,
        delivery_carriers=_delivery_carriers() if is_landing_view else [],
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
@ratelimit.limit("review")
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
        review = Review(
            product_id=None,          # ← تقييم للمتجر
            author_name=name[:120],
            rating=rating,
            comment=comment[:1000] or None,
            is_approved=False,
        )
        db.session.add(review)
        db.session.commit()

        try:
            from services.telegram import notify_new_review
            notify_new_review(review, base_url=request.url_root)
        except Exception:  # noqa: BLE001
            pass

        flash("شكرًا على رأيك! راح يبان بعد المراجعة.", "success")

    return redirect(url_for("shop.store_reviews") + "#form")


@shop_bp.route("/product/<slug>/review", methods=["POST"])
@ratelimit.limit("review")
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
        review = Review(
            product_id=product.id,
            author_name=name[:120],
            rating=rating,
            comment=comment[:1000] or None,
            is_approved=False,
        )
        db.session.add(review)
        db.session.commit()

        try:
            from services.telegram import notify_new_review
            notify_new_review(review, base_url=request.url_root)
        except Exception:  # noqa: BLE001
            pass

        flash("شكرًا على تقييمك! راح يبان بعد المراجعة.", "success")

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
    # ⚠️ نهرّبو الرابط قبل ما ندخّلوه فالـXML. الروابط تتولّد من
    # slug المنتج، وslug فيه & ولا < يكسر الملف كامل فعين جوجل
    # (خريطة موقع غير صالحة = صفحاتك ما تتفهرسش).
    from xml.sax.saxutils import escape as xml_escape

    for loc, freq, priority in pages:
        xml.append(
            f"  <url><loc>{xml_escape(loc)}</loc>"
            f"<changefreq>{freq}</changefreq>"
            f"<priority>{priority}</priority></url>"
        )
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
@ratelimit.limit("search")
def search():
    """بحث فالمنتجات بالاسم والوصف."""
    # ⚠️ نقصّو النص: ILIKE '%...%' على نص طويل بزاف يكلّف بلا فايدة،
    # وماكانش زبون يكتب 200 حرف فخانة البحث.
    query_text = (request.args.get("q") or "").strip()[:80]

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
    "privacy": "سياسة الخصوصية",
    "terms": "شروط الاستخدام",
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

    ⚠️ علاش API بدل ما نخزّنو فالـsession؟
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
            "letter": (p.name or "؟")[0],   # ⚠️ اسم فارغ كان يرمي IndexError ويطيّح الـAPI كامل
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
        # ⚠️ ما نبلعوش الخطأ بصمت: نسجّلوه باش نعرفو إذا الحاسبة
        # مكسورة، لكن الزبون يشوف رسالة عامة برك.
        current_app.logger.exception("فشل حساب الكمية للمنتج %s", product.id)
        return jsonify({"ok": False, "error": "صار خطأ فالحساب — راجع المدخلات"}), 400

    return jsonify({"ok": True, "result": result, "type": product.calculator_type})


# شعارات شركات التوصيل (PNG بلا خلفية) — المفتاح = كود الشركة فجدول Carrier.
# شركة بلا شعار هنا تبان باسمها كنص.
CARRIER_LOGOS = {
    "zrexpress": "img/carriers/zrexpress.png",
    "worldexpress": "img/carriers/worldexpress.png",
    "yalidine": "img/carriers/yalidine.png",
}


def _delivery_carriers():
    """شركات التوصيل المفعّلة (للشريط فالواجهة الرئيسية): [{code, name, logo}]."""
    from models import Carrier, ShippingRate

    result = []
    for c in Carrier.query.filter_by(is_active=True).order_by(Carrier.name).all():
        # «تبدأ من»: أرخص سعر فكل الولايات (منزل ولا مكتب)، مقرّب بنفس دالة
        # الجدول والشراء — باش الرقم اللي يشوفو الزبون هنا هو اللي يلقاه فالجدول.
        lows = [
            v for v in (
                db.session.query(func.min(ShippingRate.home_price))
                .filter(ShippingRate.carrier_id == c.id, ShippingRate.home_price > 0).scalar(),
                db.session.query(func.min(ShippingRate.office_price))
                .filter(ShippingRate.carrier_id == c.id, ShippingRate.office_price > 0).scalar(),
            ) if v
        ]
        result.append({
            "code": c.code, "name": c.name, "logo": CARRIER_LOGOS.get(c.code),
            "min_price": round_up_to_100(min(lows)) if lows else None,
        })
    return result


@shop_bp.route("/shipping-prices")
def shipping_prices():
    """
    صفحة عامة تعرض أسعار التوصيل لكل الولايات.

    ⚠️ علاش صفحة مستقلة؟
    «قداش التوصيل؟» هو أكثر سؤال يوصل على واتساب. صفحة واضحة تجيبو
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
            # ⚠️ نقرّبو هنا بنفس الدالة تاع الشراء (round_up_to_100).
            # قبل، الجدول كان يعرض السعر الخام (670) والشراء يحسب
            # المقرّب (700) — الزبون يشوف رقم ويدفع رقم آخر.
            # الأرقام الخام تبقى كيما هي فقاعدة البيانات وفلوحة
            # التحكم، لأنها هي اللي تتفقت عليها مع شركة التوصيل.
            entry["prices"][carrier.code] = {
                "home": round_up_to_100(rate.home_price),
                "office": round_up_to_100(rate.office_price),
            }

    wilayas = sorted(rows.values(), key=lambda r: r["code"])

    # أرخص سعر فكل ولاية (منزل / مكتب بشكل منفصل) — للتمييز فالجدول.
    # ما نعلّموش إلا كي يكون فيه شيء نقارنو: شركتين فأكثر وأسعار مختلفة.
    for w in wilayas:
        for kind in ("home", "office"):
            values = [p[kind] for p in w["prices"].values() if p.get(kind)]
            w["best_" + kind] = (
                min(values) if len(values) >= 2 and len(set(values)) > 1 else None
            )

    return render_template(
        "shop/shipping_prices.html",
        carriers=carriers,
        wilayas=wilayas,
        carrier_logos=CARRIER_LOGOS,
    )
