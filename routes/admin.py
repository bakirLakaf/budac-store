"""
لوحة تحكم الأدمن — كاملة.

الأمان:
- كل الصفحات محمية بـ@login_required ما عدا صفحة الدخول
- كلمة السر مهشّرة (hash)، أبدًا نص صريح
- رفع الصور يمر على فحص أمني (services/uploads.py)
- كل عمليات التعديل/الحذف عبر POST فقط (ما يتنفذوش برابط GET عادي)
"""

from datetime import datetime, timedelta
from time import time

from models import utcnow   # ⚠️ بديل datetime.utcnow المهجورة
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, abort, jsonify, current_app,
)
from sqlalchemy import func
from werkzeug.datastructures import MultiDict   # ⚠️ باش نبنيو args من حقول الفورم

import os

from models import (
    db, Product, Category, ProductImage, ProductVariant,
    Offer, OfferItem, Carrier, ShippingRate, AdminUser, Coupon, Order, OrderItem,
    AbandonedCart, Review, UpsellRule, LandingPage, StoreSetting,
    VariantPack, VariantPriceTier, AdminAction,
)
from services import audit, ratelimit
from services.cart import CartLine
from services.orders import restore_stock, reserve_stock, OrderError
from services.order_status import (   # v181: انتقلت لخدمة مشتركة مع المزامنة الخلفية
    change_order_status as _change_order_status,
    normalize_carrier_state as _normalize_zr_state,
    decide_status_change as _decide_status_change,
)
from services.uploads import save_image, delete_image, UploadError
from utils import html_to_text, slugify

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.app_context_processor
def inject_admin_counters():
    """
    عدّادات التنبيه فالقائمة الجانبية.

    ⚠️ نحسبوهم غير للأدمن الداخل — باش ما نديروش استعلامات زايدة
    فصفحات المتجر العامة.
    """
    if not session.get("admin_id"):
        return {}

    try:
        return {
            # ⚠️ عدّادات **تنبيه**: تبان غير كي يكون شي حاجة تستنى منك.
            "pending_abandoned": AbandonedCart.query.filter_by(
                status=AbandonedCart.STATUS_NEW
            ).count(),
            "pending_reviews": Review.query.filter_by(is_approved=False).count(),
            # ⚠️ الطلبية الجديدة حالتها `pending` ماشي `new` —
            # `STATUS_NEW` تخص السلات المتروكة، ماشي الطلبيات.
            "new_orders": Order.query.filter_by(
                status=Order.STATUS_PENDING
            ).count(),

            # عدّادات **جرد**: كم عندك من كل حاجة. تبان ديما بلون هادي
            # باش ما تتخلطش مع التنبيهات اللي فوق.
            "total_orders": Order.query.count(),
            "total_products": Product.query.count(),
            "total_categories": Category.query.count(),
        }
    except Exception:
        return {}  # قاعدة البيانات مازال ما اتعمّرتش


# ==================== المصادقة ====================

def login_required(view):
    """يحمي أي صفحة: إذا ماشي داخل، يوجهو لصفحة الدخول."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            flash("لازمك تسجل الدخول باش تدخل للوحة التحكم", "warning")
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


# ⚠️ حماية من تخمين كلمة السر (brute force).
# بلاها، أي واحد يقدر يجرّب آلاف كلمات السر فدقائق ويدخل للوحة التحكم.
# نخزّنو المحاولات فالذاكرة: بسيط وكافي لمتجر بسيرفر واحد.
# (لو كبر المشروع لعدة سيرفرات، ينتقل لـRedis.)
_LOGIN_ATTEMPTS = {}
MAX_LOGIN_ATTEMPTS = 6
LOCKOUT_SECONDS = 900  # 15 دقيقة


def _login_blocked(ip):
    """واش هاد الـIP محظور مؤقتًا؟ يرجع عدد الثواني الباقية، أو 0."""
    record = _LOGIN_ATTEMPTS.get(ip)
    if not record:
        return 0

    count, first_try = record

    # المهلة فاتت → ننساو المحاولات القديمة
    if time() - first_try > LOCKOUT_SECONDS:
        _LOGIN_ATTEMPTS.pop(ip, None)
        return 0

    if count >= MAX_LOGIN_ATTEMPTS:
        return int(LOCKOUT_SECONDS - (time() - first_try))

    return 0


def _record_failed_login(ip):
    count, first_try = _LOGIN_ATTEMPTS.get(ip, (0, time()))
    _LOGIN_ATTEMPTS[ip] = (count + 1, first_try)

    # تنظيف: ما نخليوش القاموس يكبر بلا حدود
    if len(_LOGIN_ATTEMPTS) > 500:
        now = time()
        for key in [k for k, v in _LOGIN_ATTEMPTS.items() if now - v[1] > LOCKOUT_SECONDS]:
            _LOGIN_ATTEMPTS.pop(key, None)


def _is_safe_redirect(target):
    r"""
    واش هاد الرابط داخلي وآمن للتوجيه؟

    نقبلو غير مسارًا داخليًا يبدا بـ«/» واحدة. كلش آخر مرفوض:
      /admin/orders     ✅
      /                 ✅
      //evil.com        ❌ (المتصفح يقراها https://evil.com)
      /\evil.com        ❌ (نفس الشي فبعض المتصفحات)
      https://evil.com  ❌
      javascript:...    ❌

    ⚠️ نستعملو urlparse ماشي فحص نصوص برك: تحليل الرابط الحقيقي
    يمسك الحالات اللي ينساها الفحص اليدوي (netloc، scheme، مسافات
    فالبداية).
    """
    from urllib.parse import urlparse

    if not target:
        return False

    target = target.strip()
    if not target.startswith("/"):
        return False
    if target.startswith("//") or target.startswith("/\\"):
        return False

    parsed = urlparse(target)
    # رابط داخلي حقيقي: بلا نطاق وبلا بروتوكول
    return not parsed.netloc and not parsed.scheme


@admin_bp.route("/login", methods=["GET", "POST"])
@ratelimit.limit("admin_login")
def login():
    if session.get("admin_id"):
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        ip = request.remote_addr or "unknown"

        blocked_for = _login_blocked(ip)
        if blocked_for:
            minutes = max(1, blocked_for // 60)
            flash(
                f"محاولات كثيرة بزاف. عاود جرّب بعد {minutes} دقيقة.",
                "error",
            )
            return render_template("admin/login.html")

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        user = AdminUser.query.filter_by(username=username).first()

        # ⚠️ نعرضو نفس الرسالة سواء المستخدم ماكانش موجود ولا كلمة السر غالطة،
        # باش ما نساعدوش أي واحد يخمن أسماء المستخدمين الموجودة
        if user and user.check_password(password):
            # ⚠️ نظّفو الجلسة القديمة قبل ما نحطّو الهوية —
            # حماية من تثبيت الجلسة (session fixation).
            session.clear()
            # permanent = True باش PERMANENT_SESSION_LIFETIME (12 ساعة)
            # يطبّق فعلاً. بلاها الجلسة كوكي متصفح ما ينتهي أبدًا.
            session.permanent = True
            session["admin_id"] = user.id
            session["admin_username"] = user.username
            user.last_login_at = utcnow()
            db.session.commit()

            _LOGIN_ATTEMPTS.pop(ip, None)  # دخول ناجح → نصفّرو العداد
            audit.record_and_commit(AdminAction.LOGIN, resource="admin",
                                    resource_id=user.id)

            next_page = request.args.get("next")
            # ⚠️ الفحص القديم `startswith("/")` كان مثقوبًا:
            # "//evil.com" و"/\\evil.com" يبداو بـ/ لكن المتصفح
            # يفسّرهم كروابط خارجية كاملة. مهاجم يبعث للأدمن رابط
            # /admin/login?next=//evil.com فيلقى روحو عند موقعو
            # (صفحة دخول مزيفة، مثلاً).
            if _is_safe_redirect(next_page):
                return redirect(next_page)
            return redirect(url_for("admin.dashboard"))

        _record_failed_login(ip)
        # ⚠️ نسجّلو اسم المستخدم المُجرَّب برك — أبدًا كلمة السر.
        # مفيد باش تعرف واش راك تحت هجوم تخمين.
        audit.record_and_commit(
            AdminAction.LOGIN_FAILED, resource="admin",
            details=f"اسم مُجرَّب: {username[:40]}", success=False,
        )
        flash("اسم المستخدم أو كلمة السر غير صحيحة", "error")

    return render_template("admin/login.html")


@admin_bp.route("/logout", methods=["POST"])
def logout():
    audit.record_and_commit(AdminAction.LOGOUT, resource="admin")
    session.clear()
    flash("تم تسجيل الخروج", "success")
    return redirect(url_for("admin.login"))


# ==================== الصفحة الرئيسية للوحة ====================

@admin_bp.route("/")
@login_required
def dashboard():
    """نظرة عامة: إحصائيات سريعة + تنبيهات تحتاج انتباه."""

    total_products = Product.query.count()
    active_products = Product.query.filter_by(is_active=True).count()
    total_categories = Category.query.count()
    active_offers = Offer.query.filter_by(is_active=True).count()

    # منتجات نفذت كميتها — تحتاج انتباه
    out_of_stock = (
        Product.query.filter_by(is_active=True, stock_quantity=0)
        .order_by(Product.name)
        .limit(10)
        .all()
    )
    out_of_stock_count = Product.query.filter_by(is_active=True, stock_quantity=0).count()

    # منتجات بلا صور — تبان خايبة فالمتجر
    products_without_images = (
        Product.query.filter(
            (Product.image_filename.is_(None)) | (Product.image_filename == "")
        )
        .filter_by(is_active=True)
        .count()
    )

    # ⚠️ موديلات بلا صورة — الزبون يختار الموديل ويشوف نفس الصورة
    # تاع الموديل السابق، فيظن أنه غالط ويخرج. زيادة على هذا، ترتيب
    # الموديلات مربوط بترتيب الصور، فالموديل بلا صورة يوقع فالآخر.
    unlinked_variants = (
        ProductVariant.query
        .join(Product)
        .filter(Product.is_active.is_(True))
        .filter((ProductVariant.image_filename.is_(None))
                | (ProductVariant.image_filename == ""))
        .count()
    )

    # منتجات بلا تصنيف — ما يبانوش فالفلترة
    uncategorized = Product.query.filter_by(category_id=None, is_active=True).count()

    # قيمة المخزون الإجمالية
    stock_value = (
        db.session.query(func.sum(Product.price * Product.stock_quantity))
        .filter_by(is_active=True)
        .scalar()
    ) or 0

    # ---- الطلبيات ----
    pending_orders = Order.query.filter_by(status=Order.STATUS_PENDING).count()
    open_orders = Order.query.filter(
        Order.status.notin_([Order.STATUS_DELIVERED, Order.STATUS_CANCELLED,
                             Order.STATUS_RETURNED])
    ).count()
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(6).all()

    # إيرادات الطلبيات المسلّمة فقط — هي الوحيدة المؤكدة فعليًا
    delivered_revenue = (
        db.session.query(func.sum(Order.total))
        .filter_by(status=Order.STATUS_DELIVERED)
        .scalar()
    ) or 0

    abandoned_new = AbandonedCart.query.filter_by(status=AbandonedCart.STATUS_NEW).count()
    abandoned_value = (
        db.session.query(func.sum(AbandonedCart.cart_total))
        .filter_by(status=AbandonedCart.STATUS_NEW)
        .scalar()
    ) or 0
    pending_reviews_count = Review.query.filter_by(is_approved=False).count()

    return render_template(
        "admin/dashboard.html",
        abandoned_new=abandoned_new,
        abandoned_value=abandoned_value,
        pending_reviews_count=pending_reviews_count,
        pending_orders=pending_orders,
        open_orders=open_orders,
        recent_orders=recent_orders,
        delivered_revenue=delivered_revenue,
        total_products=total_products,
        active_products=active_products,
        total_categories=total_categories,
        active_offers=active_offers,
        out_of_stock=out_of_stock,
        out_of_stock_count=out_of_stock_count,
        products_without_images=products_without_images,
        unlinked_variants=unlinked_variants,
        uncategorized=uncategorized,
        stock_value=stock_value,
    )


# ==================== المنتجات ====================

def _product_filters(args):
    """
    يبني استعلام المنتجات من فلاتر الرابط.

    ⚠️ دالة مشتركة بين قائمة المنتجات وأزرار «السابق/التالي» فالفورم.
    لازم الزوج يستعملو **نفس** الفلترة ونفس الترتيب، وإلا «التالي»
    يوديك لمنتج ماشي فالقائمة اللي قدامك — وتحس روحك ضيّعت البلاصة.

    يرجع: (query, filters_dict)
    """
    search = (args.get("q") or "").strip()
    category_id = args.get("category", type=int)
    stock_filter = args.get("stock")
    status_filter = args.get("status")
    price_filter = args.get("price")
    image_filter = args.get("image")
    sort = args.get("sort") or "newest"

    query = Product.query

    if search:
        query = query.filter(Product.name.contains(search))
    if category_id:
        query = query.filter_by(category_id=category_id)

    # ---- المخزون ----
    if stock_filter == "out":
        query = query.filter_by(stock_quantity=0)
    elif stock_filter == "low":
        query = query.filter(Product.stock_quantity > 0, Product.stock_quantity <= 5)

    # ---- الحالة ----
    if status_filter == "active":
        query = query.filter_by(is_active=True)
    elif status_filter == "inactive":
        query = query.filter_by(is_active=False)
    elif status_filter == "featured":
        query = query.filter_by(is_featured=True)

    # ---- السعر ----
    # ⚠️ «بلا سعر» = السعر 0. المنتجات المستوردة تجي هكذا، وهاد
    #    الفلتر هو اللي يوريك واش باقي تعمّرو.
    if price_filter == "zero":
        query = query.filter(Product.price == 0)
    elif price_filter == "discount":
        query = query.filter(
            Product.compare_at_price.isnot(None),
            Product.compare_at_price > Product.price,
        )

    # ---- الصورة ----
    if image_filter == "none":
        query = query.filter(
            (Product.image_filename.is_(None)) | (Product.image_filename == "")
        )

    # ---- الترتيب ----
    if sort == "name":
        query = query.order_by(Product.name)
    elif sort == "price_asc":
        query = query.order_by(Product.price.asc(), Product.id)
    elif sort == "price_desc":
        query = query.order_by(Product.price.desc(), Product.id)
    elif sort == "oldest":
        query = query.order_by(Product.created_at.asc(), Product.id)
    else:
        sort = "newest"
        query = query.order_by(Product.created_at.desc(), Product.id.desc())

    filters = {
        "q": search,
        "category": category_id,
        "stock": stock_filter,
        "status": status_filter,
        "price": price_filter,
        "image": image_filter,
        "sort": sort,
    }

    return query, filters


def _clean_filters(filters):
    """يحيّد الفلاتر الفارغة — باش الرابط يبقى قصير ومقروء."""
    return {k: v for k, v in filters.items() if v not in (None, "", "newest")}


@admin_bp.route("/products")
@login_required
def products_list():
    """قائمة المنتجات مع بحث وفلترة."""

    query, filters = _product_filters(request.args)

    products = query.all()
    categories = Category.query.order_by(Category.name).all()

    # عدّادات سريعة — تخلّي الأدمن يعرف واش ناقصو بلا ما يفلتر وحدة بوحدة
    counters = {
        "total": Product.query.count(),
        "inactive": Product.query.filter_by(is_active=False).count(),
        "zero_price": Product.query.filter(Product.price == 0).count(),
        "no_image": Product.query.filter(
            (Product.image_filename.is_(None)) | (Product.image_filename == "")
        ).count(),
    }

    return render_template(
        "admin/products_list.html",
        products=products,
        categories=categories,
        search=filters["q"],
        active_category=filters["category"],
        stock_filter=filters["stock"],
        status_filter=filters["status"],
        price_filter=filters["price"],
        image_filter=filters["image"],
        sort=filters["sort"],
        counters=counters,
        query_args=_clean_filters(filters),
    )


@admin_bp.route("/products/unlinked-variants")
@login_required
def unlinked_variants():
    """
    الموديلات اللي ماكانش عندها صورة مربوطة.

    ⚠️ علاش صفحة مستقلة ماشي فلتر فقائمة المنتجات؟
    القائمة تعرض **منتجات**، وهنا نحتاجو نشوفو **موديلات** — منتج
    واحد ممكن يكون فيه 10 موديلات وواحد برك ناقصو صورة. الفلتر
    كان يوري المنتج كامل ويخلّيك تقلّب بيدك على الموديل الناقص.

    نعرضو حتى المنتجات المعطّلة، مع علامة — الأدمن يقدر يكون معطّلو
    مؤقتًا وهو ينظّف صورو.
    """
    rows = (
        ProductVariant.query
        .join(Product)
        .filter((ProductVariant.image_filename.is_(None))
                | (ProductVariant.image_filename == ""))
        .order_by(Product.is_active.desc(), Product.name, ProductVariant.id)
        .all()
    )

    # نجمّعوهم حسب المنتج — أسرع للعين، وضغطة وحدة تصلّح عدة موديلات
    grouped = []
    for v in rows:
        if grouped and grouped[-1]["product"].id == v.product_id:
            grouped[-1]["variants"].append(v)
        else:
            grouped.append({"product": v.product, "variants": [v]})

    return render_template(
        "admin/unlinked_variants.html",
        grouped=grouped,
        total=len(rows),
    )


def _product_neighbours(product, args):
    """
    يلقى المنتج السابق والتالي **داخل نفس الفلترة** اللي جاي منها الأدمن.

    ⚠️ علاه نعاودو نبنيو القائمة كاملة بدل ما نديرو استعلام ذكي؟
    الترتيب يقدر يكون على created_at ولا الاسم ولا السعر، وكل واحد
    يحتاج شرط مقارنة مختلف — والقيم المتساوية (سعر 0 عند 26 منتج)
    تكسر المنطق. القائمة عندك مئات المنتجات ماشي ملايين، فالبساطة
    تربح على التحسين هنا.

    يرجع: (previous, next_, position, total)
    """
    if product is None:
        return None, None, 0, 0

    query, _ = _product_filters(args)
    ids = [row.id for row in query.with_entities(Product.id).all()]

    if product.id not in ids:
        # المنتج برّا الفلترة (مثلاً فلترت «بلا سعر» وعمّرت السعر
        # وحفظت). ما نوريوش تنقّل غالط — نوريو والو.
        return None, None, 0, len(ids)

    index = ids.index(product.id)

    previous = Product.query.get(ids[index - 1]) if index > 0 else None
    next_ = Product.query.get(ids[index + 1]) if index < len(ids) - 1 else None

    return previous, next_, index + 1, len(ids)


@admin_bp.route("/products/new", methods=["GET", "POST"])
@admin_bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def product_form(product_id=None):
    """
    نموذج واحد للإضافة والتعديل — أبسط من ملوين منفصلين فيهم نفس الكود مكرر.
    """
    product = Product.query.get_or_404(product_id) if product_id else None
    categories = Category.query.order_by(Category.name).all()

    # ⚠️ «التالي» يتحسب **قبل** أي تعديل، ماشي بعدو.
    #
    # علاه؟ الفلترة الأكثر استعمالاً هي «بلا سعر» ولا «معطّلة»، والحفظ
    # هو بالضبط اللي يخرّج المنتج منها (عمّرت السعر / فعّلت المنتج).
    # لو حسبنا بعد الحفظ، المنتج ما يبقاش فالقائمة، و«التالي» يرجع
    # فارغ — فتنرمى للقائمة من أول منتج. نحسبو قبل، والتنقّل يكمّل
    # عادي حتى للأخير.
    pending_next_id = None

    if request.method == "POST" and product and request.form.get("save_and_next"):
        pre_args = MultiDict({
            key: request.form.get(f"nav_{key}")
            for key in ("q", "category", "stock", "status", "price", "image", "sort")
            if request.form.get(f"nav_{key}")
        })
        _prev, pre_next, _pos, _total = _product_neighbours(product, pre_args)
        pending_next_id = pre_next.id if pre_next else None

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("اسم المنتج مطلوب", "error")
            return render_template(
                "admin/product_form.html", product=product, categories=categories
            )

        slug = (request.form.get("slug") or "").strip() or slugify(name)

        # نتأكد أن الـslug ما مستعملش عند منتج آخر
        clash = Product.query.filter(Product.slug == slug)
        if product:
            clash = clash.filter(Product.id != product.id)
        if clash.first():
            flash(f"الرابط '{slug}' مستعمل عند منتج آخر — بدلو", "error")
            return render_template(
                "admin/product_form.html", product=product, categories=categories
            )

        if product is None:
            product = Product(name=name, slug=slug, price=0)
            db.session.add(product)

        product.name = name
        product.slug = slug
        product.price = request.form.get("price", type=int) or 0
        product.compare_at_price = request.form.get("compare_at_price", type=int) or None

        # ⚠️ وحدة البيع: «حبة» ولا «متر» ولا نص مخصّص. إذا اختار
        # «مخصّص» ونسا يكتب الاسم، نرجعو للحبة بدل ما نخزّنو فراغًا.
        unit_choice = (request.form.get("unit") or "piece").strip()
        if unit_choice == "custom":
            custom = (request.form.get("unit_custom") or "").strip()
            product.unit = custom[:40] if custom else Product.UNIT_PIECE
        else:
            product.unit = unit_choice[:40] or Product.UNIT_PIECE
        product.stock_quantity = request.form.get("stock_quantity", type=int) or 0
        product.category_id = request.form.get("category_id", type=int) or None
        product.is_active = bool(request.form.get("is_active"))
        product.is_featured = bool(request.form.get("is_featured"))

        # إعدادات حاسبة الكمية
        calc_type = request.form.get("calculator_type") or None
        product.calculator_type = calc_type if calc_type in ("perimeter", "area") else None
        product.roll_width_m = request.form.get("roll_width_m", type=float) or None
        product.calculator_margin = request.form.get("calculator_margin", type=int) or 10

        # الوصف: نقبل HTML ونخزّن النسختين (الأصلية + نص واضح للعرض)
        raw_description = request.form.get("description") or ""
        product.description_html = raw_description
        product.description = html_to_text(raw_description)

        db.session.flush()  # باش ناخذو product.id إذا كان جديد

        # رفع صور جديدة (متعددة)
        #
        # ⚠️ المسار الأساسي درك هو `/images/upload` — صورة بصورة عبر
        # المتصفح. هاد الكود يبقى كاحتياط: إذا JavaScript معطّل ولا
        # قالب قديم مخبّأ فالمتصفح، الصور مازال تتقبل بدل ما تضيع.
        uploaded_files = request.files.getlist("images")
        max_order = (
            db.session.query(func.max(ProductImage.sort_order))
            .filter_by(product_id=product.id)
            .scalar()
        ) or 0

        for idx, file in enumerate(uploaded_files, start=1):
            if not file or not file.filename:
                continue
            try:
                filename = save_image(file, "products", base_name=slug)
            except UploadError as e:
                flash(f"صورة مرفوضة: {e}", "error")
                continue

            db.session.add(
                ProductImage(
                    product_id=product.id,
                    image_filename=filename,
                    sort_order=max_order + idx,
                )
            )
            if not product.image_filename:
                product.image_filename = filename

        db.session.commit()

        # ---------- قرارات الصور (ترتيب / رئيسية / حذف) ----------
        # ⚠️ الأدمن يرتّب ويحذف فالمتصفح بلا إعادة تحميل، والقرارات
        # توصل هنا كقوائم معرّفات وتتطبّق مرة وحدة عند الحفظ.
        _apply_image_decisions(product)

        db.session.commit()
        flash(f"تم حفظ المنتج «{product.name}»", "success")

        # ⚠️ الفلاتر تجي من الحقول المخفية فالفورم، ماشي من request.args:
        #    الـPOST يمشي لنفس المسار بلا query string، فلو قرينا args
        #    نضيّعو الفلترة وأزرار التنقّل تولّي على القائمة الكاملة.
        nav_args = {
            key: request.form.get(f"nav_{key}")
            for key in ("q", "category", "stock", "status", "price", "image", "sort")
            if request.form.get(f"nav_{key}")
        }

        # «حفظ والتالي» — الزر اللي يخلّيك تعمّر 26 منتج بلا ما ترجع للقائمة
        if request.form.get("save_and_next"):
            if pending_next_id:
                return redirect(
                    url_for("admin.product_form", product_id=pending_next_id, **nav_args)
                )

            flash("وصلت لآخر منتج فالقائمة", "success")
            return redirect(url_for("admin.products_list", **nav_args))

        return redirect(
            url_for("admin.product_form", product_id=product.id, **nav_args)
        )

    previous, next_, position, total = _product_neighbours(product, request.args)

    _query, nav_filters = _product_filters(request.args)
    nav_args = _clean_filters(nav_filters)

    return render_template(
        "admin/product_form.html",
        product=product,
        categories=categories,
        prev_product=previous,
        next_product=next_,
        nav_position=position,
        nav_total=total,
        nav_args=nav_args,
    )


def purge_unused_image_files(filenames):
    """
    يمسح من القرص **غير** الصور اللي ما بقاش حتى سطر فقاعدة البيانات يستعملها.

    🔴 علاش هاد الدالة موجودة؟
    من ملّي ولّى «زر نسخة» ينسخ المنتج بلا ما ينسخ الملفات، ولّى نفس
    الملف مشتركًا بين منتجين ولا أكثر. حذف واحد منهم كان يمحي الملف
    من القرص، والمنتج الأصلي يبقى يشير لملف ماكانش → صور مكسورة.

    ⚠️ لازم تتنادى **بعد** db.session.flush() تاع الحذف — باش الاستعلام
    يشوف الوضع الجديد ماشي القديم.

    نفحصو التلاتة اللي يقدرو يشيرو لملف:
      - ProductImage.image_filename  (معرض أي منتج)
      - Product.image_filename       (الصورة الرئيسية)
      - ProductVariant.image_filename(صورة الموديل)
    """
    for filename in {f for f in filenames if f}:
        still_used = (
            db.session.query(ProductImage.id)
            .filter(ProductImage.image_filename == filename).first()
            or db.session.query(Product.id)
            .filter(Product.image_filename == filename).first()
            or db.session.query(ProductVariant.id)
            .filter(ProductVariant.image_filename == filename).first()
        )

        if still_used:
            # مشتركة مع منتج آخر — نخليو الملف فبلاصتو
            current_app.logger.info("🖼️ ملف مشترك، ما تمسحش: %s", filename)
            continue

        try:
            delete_image(filename, "products")
        except Exception:
            # ⚠️ ما نبلعوهش بصمت: إذا تكرر، معناها ثمة خلل فالتخزين.
            current_app.logger.warning("🖼️ ما قدرناش نمسحو الملف: %s", filename)


def _apply_image_decisions(product):
    """
    يطبّق ترتيب الصور والصورة الرئيسية والحذف — كلهم مرة وحدة.

    ⚠️ علاش هكذا وماشي مسار لكل عملية؟
    قبل، كل تحريك صورة كان يعيد تحميل الصفحة ويرجّع الأدمن لفوق.
    درك يرتّب ويحذف براحتو فالمتصفح، ويضغط «حفظ» مرة وحدة.

    نقرا المعرّفات من حقول مخفية. أي معرّف ماشي من صور هاد المنتج
    يتجاهل — حماية من تعديل الحقول من أدوات المطوّر.
    """
    own_ids = {img.id for img in product.images}

    def parse(field):
        raw = (request.form.get(field) or "").strip()
        if not raw:
            return []
        out = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit() and int(part) in own_ids:
                out.append(int(part))
        return out

    # ---------- 1) الحذف ----------
    deleted = set(parse("image_deleted"))
    orphan_candidates = []   # أسماء ملفات نفحصوهم فالآخر قبل ما نمسحوهم
    for image in list(product.images):
        if image.id not in deleted:
            continue

        # نفكّو الموديلات اللي تستعمل هاد الصورة
        ProductVariant.query.filter_by(
            product_id=product.id, image_filename=image.image_filename
        ).update({"image_filename": None}, synchronize_session=False)

        # ⚠️ ما نمسحوش الملف درك. نجمعو الأسماء ونمسحو من القرص فالآخر،
        # بعد ما نكونو مسحنا السطور فعلاً — باش الفحص يشوف الحقيقة.
        # (القديم كان ينادي delete_image بوسيط ناقص، والخطأ يتبلع.)
        orphan_candidates.append(image.image_filename)

        db.session.delete(image)

    db.session.flush()

    # ---------- 2) الترتيب ----------
    order = [i for i in parse("image_order") if i not in deleted]
    if order:
        position = {image_id: idx for idx, image_id in enumerate(order)}
        for image in product.images:
            image.sort_order = position.get(image.id, len(order))

    # ---------- 3) الصورة الرئيسية ----------
    raw_main = (request.form.get("image_main") or "").strip()
    if raw_main.isdigit() and int(raw_main) in own_ids and int(raw_main) not in deleted:
        main = next((i for i in product.images if i.id == int(raw_main)), None)
        if main:
            product.image_filename = main.image_filename

    # ⚠️ لو الصورة الرئيسية تمسحت، ناخذو أول وحدة باقية — وإلا المنتج
    # يبقى يشير لملف محذوف ويبان مكسورًا فالمتجر.
    remaining = [i for i in product.images if i.id not in deleted]
    if remaining and product.image_filename not in {i.image_filename for i in remaining}:
        product.image_filename = sorted(
            remaining, key=lambda i: (i.sort_order, i.id)
        )[0].image_filename
    elif not remaining:
        product.image_filename = None

    # ---------- 4) تنظيف القرص ----------
    # فالآخر، بعد ما تحدّدت الصورة الرئيسية — وإلا نحسبو مرجعًا لصورة محذوفة.
    purge_unused_image_files(orphan_candidates)


@admin_bp.route("/products/<int:product_id>/images/upload", methods=["POST"])
@login_required
def product_image_upload(product_id):
    """
    يرفع **صورة وحدة** ويرجّع JSON.

    🔴 علاش مسار مستقل؟
    قبل، كل الصور كانت تتبعث مع فورم المنتج كامل فطلب واحد. الحد
    `MAX_CONTENT_LENGTH` هو 6 ميغا للطلب **كامل**، والحد لكل صورة 5
    ميغا. يعني 3 صور × 3 ميغا = 9 ميغا → السيرفر يقطع الاتصال فوسط
    الرفع (`ERR_HTTP2_PROTOCOL_ERROR`) ويضيع معاه كل التعديلات.

    درك كل صورة طلب مستقل صغير. المتصفح يرفعهم واحدة بوحدة ويوري
    شريط تقدّم، وزر الحفظ يبقى مقفولاً حتى يكملو.
    """
    product = Product.query.get_or_404(product_id)

    file = request.files.get("image")
    if not file or not file.filename:
        return jsonify({"ok": False, "message": "ما وصلتش حتى صورة"}), 400

    try:
        filename = save_image(file, "products", base_name=product.slug)
    except UploadError as e:
        return jsonify({"ok": False, "message": str(e)}), 400

    # ⚠️ نوعين: صورة منتج حقيقية (تتربط بموديل) ولا صورة توضيحية.
    # الفرق مهم — التوضيحية ما تدخلش فترتيب الموديلات.
    is_extra = request.form.get("kind") == "extra"

    # ترتيب مستقل لكل نوع باش ما يتداخلوش
    same_kind = [i for i in product.images if i.is_extra == is_extra]
    last = max((i.sort_order for i in same_kind), default=-1)

    image = ProductImage(
        product_id=product.id,
        image_filename=filename,
        sort_order=last + 1,
        is_extra=is_extra,
    )
    db.session.add(image)

    # أول **صورة منتج** تولّي الرئيسية تلقائيًا.
    # الصور التوضيحية ما تقدرش تكون رئيسية.
    if not is_extra and not product.image_filename:
        product.image_filename = filename

    db.session.commit()

    return jsonify({
        "ok": True,
        "id": image.id,
        "filename": filename,
        "url": url_for("static", filename=f"img/products/{filename}"),
        "is_main": product.image_filename == filename,
        "is_extra": is_extra,
        "message": "تمت إضافة الصورة التوضيحية" if is_extra else "تمت إضافة الصورة",
    })


@admin_bp.route("/products/bulk-status", methods=["POST"])
@login_required
def products_bulk_status():
    """
    تفعيل ولا تعطيل عدة منتجات دفعة وحدة (من صفحة «المنتجات»).

    قواعد الأمان:
      • «بلا سعر» (السعر 0) ما نفعّلوهش: الزبون يقدر يطلبو بدراهم 0.
        نقولو للأدمن يحط السعر أولاً.
      • منتج بلا صورة نفعّلوه (قرارك)، بصح نحذّرو باش ما يبانش فارغ فالمتجر.
      • نفس المنتج يتبعث مرتين (checkbox مزدوجة) ← نحيّدو التكرار.
    """
    action = request.form.get("action")
    ids = list(dict.fromkeys(request.form.getlist("product_ids", type=int)))[:500]

    # نرجعو للصفحة بنفس الفلاتر (فقط مسارات لوحة المنتجات — ماكاين redirect مفتوح)
    back = request.form.get("back") or ""
    if not back.startswith("/admin/products"):
        back = url_for("admin.products_list")
    go_back = redirect(back)

    if action not in ("activate", "deactivate"):
        flash("إجراء غير معروف", "error")
        return go_back
    if not ids:
        flash("ما اخترت حتى منتج", "warning")
        return go_back

    products = Product.query.filter(Product.id.in_(ids)).all()
    activate = action == "activate"

    changed = already = 0
    unpriced, no_image = [], []
    for product in products:
        if product.is_active == activate:
            already += 1
            continue
        if activate and not (product.price and product.price > 0):
            unpriced.append(product.name)
            continue
        product.is_active = activate
        changed += 1
        if activate and not product.image_filename:
            no_image.append(product.name)

    if changed:
        audit.record(
            AdminAction.PRODUCT_BULK, "product", None,
            f"{'تفعيل' if activate else 'تعطيل'} {changed} منتج",
        )
    db.session.commit()

    verb = "تفعّل" if activate else "تعطّل"
    if changed:
        flash(f"{verb} {changed} منتج", "success")
    if already:
        flash(f"{already} كانو {'مفعّلين' if activate else 'معطّلين'} من قبل", "info")
    if unpriced:
        flash("ما تفعّلوش (بلا سعر — حط السعر أولاً): " + " | ".join(unpriced[:10]), "warning")
    if no_image:
        flash("تفعّلو بصح بلا صورة: " + " | ".join(no_image[:10]), "warning")
    return go_back


@admin_bp.route("/products/<int:product_id>/images/<int:image_id>/replace", methods=["POST"])
@login_required
def product_image_replace(product_id, image_id):
    """
    يعوّض صورة موجودة بنسخة معدّلة (قص/تدوير/قلب من المحرّر) فنفس البلاصة.

    تبقى: الترتيب، «رئيسية»، الوصف، وربط الموديلات (نعيدو توجيهها للملف الجديد).
    ⚠️ الملف القديم ما نمسحوهش إلا إذا ما بقى حتى منتج (ولا موديل) يستعملو —
       المنتج المنسوخ (product_duplicate) يشارك نفس الملفات.
    """
    image = ProductImage.query.filter_by(id=image_id, product_id=product_id).first_or_404()
    product = image.product

    upload = request.files.get("image")
    if not upload or not upload.filename:
        return jsonify({"ok": False, "message": "ما وصلتش حتى صورة"}), 400

    old = image.image_filename
    try:
        new = save_image(upload, "products", base_name=product.slug)
    except UploadError as e:
        return jsonify({"ok": False, "message": str(e)}), 400

    image.image_filename = new
    if product.image_filename == old:
        product.image_filename = new
    relinked = (
        ProductVariant.query
        .filter_by(product_id=product.id, image_filename=old)
        .update({"image_filename": new}, synchronize_session=False)
    )
    db.session.flush()
    purge_unused_image_files([old])
    db.session.commit()

    return jsonify({
        "ok": True,
        "filename": new,
        "url": url_for("static", filename=f"img/products/{new}"),
        "relinked_variants": relinked,
        "message": "تبدّلت الصورة",
    })


@admin_bp.route("/products/<int:product_id>/images/<int:image_id>/caption",
                methods=["POST"])
@login_required
def product_image_caption(product_id, image_id):
    """
    يحفظ وصفًا قصيرًا للصورة التوضيحية («كيفاش تركّبها»).

    ⚠️ نتأكدو أن الصورة تخص هاد المنتج — باش حد ما يعدّلش صورة
    منتج آخر ببعث id غالط.
    """
    image = ProductImage.query.filter_by(
        id=image_id, product_id=product_id
    ).first_or_404()

    caption = (request.form.get("caption") or "").strip()
    if len(caption) > 200:
        return jsonify({"ok": False, "message": "الوصف طويل بزاف (200 حرف)"}), 400

    image.caption = caption or None
    db.session.commit()

    return jsonify({"ok": True, "caption": image.caption or "",
                    "message": "انحفظ الوصف"})


@admin_bp.route("/products/<int:product_id>/duplicate", methods=["POST"])
@login_required
def product_duplicate(product_id):
    """
    ينسخ منتجًا كامل: المعلومات، الصور، الموديلات، أسعار الجملة،
    والعروض الجاهزة.

    ⚠️ علاش الميزة؟ منتجاتك متشابهة (نفس الموديلات بألوان مختلفة).
    بناء منتج جديد من الصفر = إعادة كتابة 7 موديلات وأسعارهم. النسخة
    توفّر الوقت كامل.

    قرارات مقصودة:

    1. **النسخة معطّلة** (`is_active = False`) — ما تبانش للزبون حتى
       تراجعها وتفعّلها. بلا هذا، منتج نصف جاهز يبان فالمتجر.

    2. **الصور تتشارك، ما تتنسخش على القرص.** نعملو سجلات جديدة تشير
       لنفس الملفات. علاش؟ نسخ 20 صورة = 20 ميغا زايدة، والغالب أنك
       راح تبدّلهم على كل حال.

       ⚠️ النتيجة المهمة: حذف صورة من **النسخة** ما يمحيش الملف —
       الأصل مازال يستعملو. الكود تاع الحذف يمسح السجل برك (من v80).

    3. **الـslug يتولّد فريدًا** بزيادة رقم: `pieds-fer-2`، `-3`…
    """
    original = Product.query.get_or_404(product_id)

    # ---------- slug فريد ----------
    base = original.slug
    index = 2
    while Product.query.filter_by(slug=f"{base}-{index}").first():
        index += 1
        if index > 200:      # حارس: ما نلفّوش للأبد
            flash("بزاف نسخ من هاد المنتج — بدّل الرابط يدويًا", "error")
            return redirect(url_for("admin.products_list"))
    new_slug = f"{base}-{index}"

    # ---------- المنتج ----------
    copy = Product(
        name=f"{original.name} (نسخة)",
        slug=new_slug,
        description=original.description,
        description_html=original.description_html,
        price=original.price,
        compare_at_price=original.compare_at_price,
        unit=original.unit,
        category_id=original.category_id,
        color_variant=original.color_variant,
        calculator_type=original.calculator_type,
        roll_width_m=original.roll_width_m,
        calculator_margin=original.calculator_margin,
        stock_quantity=original.stock_quantity,
        image_filename=original.image_filename,
        is_active=False,          # ⚠️ معطّلة حتى تراجعها
        is_featured=False,
    )
    db.session.add(copy)
    db.session.flush()            # باش ياخذ id

    # ---------- الصور ----------
    for img in original.images:
        db.session.add(ProductImage(
            product_id=copy.id,
            image_filename=img.image_filename,   # نفس الملف
            sort_order=img.sort_order,
            is_extra=img.is_extra,
            caption=img.caption,
        ))

    # ---------- الموديلات وأسعارهم ----------
    for variant in original.variants:
        new_variant = ProductVariant(
            product_id=copy.id,
            label=variant.label,
            price=variant.price,
            compare_at_price=variant.compare_at_price,
            stock_quantity=variant.stock_quantity,
            sku=None,                 # ⚠️ SKU لازم يبقى فريدًا
            image_filename=variant.image_filename,
            is_default=variant.is_default,
            is_available=variant.is_available,
        )
        db.session.add(new_variant)
        db.session.flush()

        for tier in variant.price_tiers:
            db.session.add(VariantPriceTier(
                variant_id=new_variant.id,
                min_quantity=tier.min_quantity,
                max_quantity=tier.max_quantity,
                unit_price=tier.unit_price,
            ))

        for pack in variant.packs:
            db.session.add(VariantPack(
                variant_id=new_variant.id,
                quantity=pack.quantity,
                label=pack.label,
                price=pack.price,
                sort_order=pack.sort_order,
            ))

    db.session.commit()

    flash(
        f"اتنسخ «{original.name}» — النسخة **معطّلة**، عدّلها وفعّلها.",
        "success",
    )
    return redirect(url_for("admin.product_form", product_id=copy.id))


@admin_bp.route("/products/<int:product_id>/delete", methods=["POST"])
@login_required
def product_delete(product_id):
    product = Product.query.get_or_404(product_id)

    # نمنع الحذف إذا المنتج داخل فعرض شغال — باش ما نكسروش العرض بصمت
    linked_offers = [item.offer.title for item in
                     OfferItem.query.filter_by(product_id=product.id).all()]
    if linked_offers:
        flash(
            f"ما نقدروش نمسحو «{product.name}» — داخل فالعروض: {'، '.join(linked_offers)}. "
            "حيدو من العروض أولاً، أو عطّلو بدل ما تمسحو.",
            "error",
        )
        return redirect(url_for("admin.products_list"))

    # 🔴 كان هنا `delete_image(...)` مباشرة — وهاد الشي كان يمحي صور
    # المنتج الأصلي كي تمسح نسخة منو (الملفات مشتركة بين النسخ).
    # درك نجمعو الأسماء، نمسحو المنتج، ومن بعد نمسحو غير الملفات
    # اللي ما بقاش حتى منتج آخر يستعملها.
    candidates = [img.image_filename for img in product.images]
    candidates.append(product.image_filename)

    name = product.name
    audit.record(AdminAction.DELETE, "product", product.id, name)
    db.session.delete(product)
    db.session.flush()

    purge_unused_image_files(candidates)

    db.session.commit()
    flash(f"تم حذف «{name}»", "success")
    return redirect(url_for("admin.products_list"))


@admin_bp.route("/products/<int:product_id>/images/<int:image_id>/delete", methods=["POST"])
@login_required
def product_image_delete(product_id, image_id):
    image = ProductImage.query.filter_by(id=image_id, product_id=product_id).first_or_404()
    product = image.product

    # 🔴 ما نمسحوش الملف درك — يمكن يكون مشتركًا مع منتج منسوخ.
    # نمسحو السطر أولاً، ونصفّيو القرص فالآخر بعد ما نتأكدو.
    filename = image.image_filename
    was_main = product.image_filename == filename
    db.session.delete(image)
    db.session.flush()

    # إذا مسحنا الصورة الرئيسية، نرقّيو الصورة الموالية مكانها
    if was_main:
        remaining = (
            ProductImage.query.filter_by(product_id=product.id)
            .order_by(ProductImage.sort_order)
            .first()
        )
        product.image_filename = remaining.image_filename if remaining else None

    # ⚠️ الموديلات تشير للصورة بالاسم (ماشي بمفتاح أجنبي)، فحذف الصورة
    # يخلّي موديلات تشير لملف ماكانش → صورة مكسورة عند الزبون.
    # نفكّو الربط تلقائيًا: الأدمن يشوف «بلا صورة» ويختار وحدة أخرى.
    cleared = (
        ProductVariant.query
        .filter_by(product_id=product.id, image_filename=filename)
        .update({"image_filename": None}, synchronize_session=False)
    )
    db.session.flush()

    purge_unused_image_files([filename])

    db.session.commit()

    if cleared:
        flash(f"تم حذف الصورة — تحيّدت من {cleared} موديل", "success")
    else:
        flash("تم حذف الصورة", "success")

    # طلب AJAX → نرجّعو JSON بدل إعادة تحميل الصفحة
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({
            "ok": True,
            "cleared_variants": cleared,
            "main_image": product.image_filename,
        })

    return redirect(url_for("admin.product_form", product_id=product_id))


# ==================== التنويعات (Variants) ====================

@admin_bp.route("/products/<int:product_id>/variants/add", methods=["POST"])
@login_required
def variant_add(product_id):
    product = Product.query.get_or_404(product_id)

    label = (request.form.get("label") or "").strip()
    if not label:
        flash("اسم التنويعة مطلوب", "error")
        return redirect(url_for("admin.product_form", product_id=product_id))

    variant = ProductVariant(
        product_id=product.id,
        label=label,
        price=request.form.get("price", type=int) or None,
        compare_at_price=request.form.get("compare_at_price", type=int) or None,
        stock_quantity=request.form.get("stock_quantity", type=int) or 0,
        sku=(request.form.get("sku") or "").strip() or None,
        is_default=not product.variants,  # أول تنويعة تولي الافتراضية تلقائيًا
    )

    # الصورة تُختار من صور المنتج الموجودة (ماشي رفع جديد).
    # ⚠️ هكذا ما نكرّروش نفس الصورة مرتين على القرص، والأدمن يرفع
    # صور المنتج مرة وحدة ويوزّعها على الموديلات.
    chosen = (request.form.get("image_filename") or "").strip()
    if chosen and any(img.image_filename == chosen for img in product.images):
        variant.image_filename = chosen

    db.session.add(variant)
    db.session.commit()

    flash(f"تمت إضافة التنويعة «{label}»", "success")
    return redirect(url_for("admin.product_form", product_id=product_id))


@admin_bp.route("/variants/<int:variant_id>/image", methods=["POST"])
@login_required
def variant_image(variant_id):
    """
    يربط موديل بصورة من **صور المنتج الموجودة**.

    ⚠️ علاش ماشي رفع جديد؟ الصور نفسها: الأدمن يرفع صور المنتج مرة وحدة،
    وبعدها يقول "هاد الموديل = هاد الصورة". بلا تكرار على القرص، وبلا
    ما يعاود يرفع نفس الصورة لكل موديل.
    """
    variant = ProductVariant.query.get_or_404(variant_id)
    product = variant.product

    filename = (request.form.get("image_filename") or "").strip()
    is_ajax = request.headers.get("X-Requested-With") == "fetch"

    def reply(ok, message, category="success"):
        """رد موحّد: JSON للـAJAX، وإعادة توجيه للفورم العادي."""
        if is_ajax:
            return jsonify({
                "ok": ok,
                "message": message,
                "image": variant.image_filename,
            }), (200 if ok else 400)

        flash(message, category)
        return redirect(url_for("admin.product_form", product_id=product.id))

    # فراغ = نحيّدو الربط
    if not filename:
        variant.image_filename = None
        db.session.commit()
        return reply(True, f"تحيّدت صورة «{variant.label}»")

    # نتحققو أن الصورة فعلاً من **صور المنتج** (أمان: ما نقبلوش أي اسم).
    # ⚠️ الصور التوضيحية مستثناة — هي توري كيفاش يتستعمل المنتج، ماشي
    # موديلاً يقدر الزبون يختارو.
    valid = any(img.image_filename == filename for img in product.main_images)
    if not valid and filename != product.image_filename:
        return reply(False, "لازم تختار صورة من صور المنتج (ماشي التوضيحية)",
                     "error")

    variant.image_filename = filename
    db.session.commit()
    return reply(True, f"تربطت صورة «{variant.label}»")


@admin_bp.route("/variants/<int:variant_id>/tiers", methods=["POST"])
@login_required
def variant_tiers(variant_id):
    """
    يحفظ مجالات سعر الجملة لموديل.

    النموذج يبعث صفوف موازية: min_quantity[]، max_quantity[]، unit_price[].
    نمسحو القديم ونكتبو الجديد — أبسط وأأمن من التعديل صف بصف.
    """
    from models import VariantPriceTier

    variant = ProductVariant.query.get_or_404(variant_id)

    mins = request.form.getlist("min_quantity")
    maxs = request.form.getlist("max_quantity")
    prices = request.form.getlist("unit_price")

    new_tiers = []
    for i in range(len(mins)):
        try:
            min_q = int(mins[i]) if mins[i].strip() else None
            price = int(prices[i]) if i < len(prices) and prices[i].strip() else None
            max_q = int(maxs[i]) if i < len(maxs) and maxs[i].strip() else None
        except ValueError:
            continue

        # صف ناقص = نتجاوزوه (الأدمن يمكن خلّى سطر فارغ)
        if min_q is None or price is None:
            continue
        if min_q < 1 or price < 0:
            continue
        if max_q is not None and max_q < min_q:
            flash(f"المجال «من {min_q}» فيه «إلى» أصغر من «من» — تجاوزناه", "warning")
            continue

        new_tiers.append((min_q, max_q, price))

    # نتحققو من التداخل: مجالان يغطّيو نفس الكمية = سعر غامض
    new_tiers.sort(key=lambda t: t[0])
    for i in range(len(new_tiers) - 1):
        cur_max = new_tiers[i][1]
        next_min = new_tiers[i + 1][0]
        if cur_max is None or cur_max >= next_min:
            flash(
                f"المجالات متداخلة عند {next_min}. صحّحها باش السعر يكون واضح.",
                "warning",
            )
            break

    VariantPriceTier.query.filter_by(variant_id=variant.id).delete()
    for min_q, max_q, price in new_tiers:
        db.session.add(VariantPriceTier(
            variant_id=variant.id, min_quantity=min_q,
            max_quantity=max_q, unit_price=price,
        ))

    db.session.commit()
    flash(f"تحفظو {len(new_tiers)} مجال سعر لـ«{variant.label}»", "success")
    return redirect(url_for("admin.product_form", product_id=variant.product_id))


@admin_bp.route("/variants/<int:variant_id>/delete", methods=["POST"])
@login_required
def variant_delete(variant_id):
    variant = ProductVariant.query.get_or_404(variant_id)
    product_id = variant.product_id
    was_default = variant.is_default

    # 🔴 كان هنا `delete_image(variant.image_filename)` — يمحي الملف من
    # القرص. كان صحيحًا كي كل موديل يرفع صورتو الخاصة، لكن درك صور
    # الموديلات **مختارة من صور المنتج**، فحذف موديل كان يمحي صورة
    # مشتركة ويخلّي خانة فارغة فالمعرض (وحتى الصورة الرئيسية).
    #
    # القاعدة الجديدة: حذف الموديل يقطع الربط برك. الصور تتحذف من
    # قسم «الصور» وحدو.

    db.session.delete(variant)
    db.session.flush()

    # إذا مسحنا الافتراضية، نرقّيو وحدة أخرى باش المنتج ما يبقاش بلا افتراضية
    if was_default:
        remaining = ProductVariant.query.filter_by(product_id=product_id).first()
        if remaining:
            remaining.is_default = True

    db.session.commit()
    flash("تم حذف التنويعة", "success")
    return redirect(url_for("admin.product_form", product_id=product_id))


# ==================== التصنيفات ====================

@admin_bp.route("/categories", methods=["GET", "POST"])
@login_required
def categories_list():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("اسم التصنيف مطلوب", "error")
            return redirect(url_for("admin.categories_list"))

        slug = (request.form.get("slug") or "").strip() or slugify(name)

        if Category.query.filter_by(slug=slug).first():
            flash(f"التصنيف '{slug}' موجود من قبل", "error")
            return redirect(url_for("admin.categories_list"))

        image_filename = None
        file = request.files.get("image")
        if file and file.filename:
            try:
                image_filename = save_image(file, "categories", base_name=slug)
            except UploadError as e:
                flash(f"صورة مرفوضة: {e}", "error")

        db.session.add(
            Category(name=name, slug=slug, image_filename=image_filename, is_active=True)
        )
        db.session.commit()
        flash(f"تم إنشاء التصنيف «{name}»", "success")
        return redirect(url_for("admin.categories_list"))

    categories = Category.query.order_by(Category.name).all()

    # عدد المنتجات فكل تصنيف — باش الأدمن يعرف واش خاوي
    counts = dict(
        db.session.query(Product.category_id, func.count(Product.id))
        .group_by(Product.category_id)
        .all()
    )

    return render_template(
        "admin/categories.html", categories=categories, counts=counts
    )


@admin_bp.route("/categories/<int:category_id>/edit", methods=["POST"])
@login_required
def category_edit(category_id):
    """
    يعدّل تصنيفًا موجودًا: الاسم، الصورة، والتفعيل.

    ⚠️ الـ`slug` **ما يتبدّلش**. هو اللي فالروابط (`/category/handles-knobs`)
    وفجوجل. تبديلو يكسر كل الروابط القديمة والسيو. إذا لازم يتبدّل،
    يتدار يدويًا مع إعادة توجيه — وهذا خارج نطاق هاد الفورم.
    """
    category = Category.query.get_or_404(category_id)

    name = (request.form.get("name") or "").strip()
    if not name:
        flash("اسم التصنيف مطلوب", "error")
        return redirect(url_for("admin.categories_list"))

    if len(name) > 120:
        flash("الاسم طويل بزاف (120 حرف كحد أقصى)", "error")
        return redirect(url_for("admin.categories_list"))

    # اسمان متشابهان يحيّرو الزبون
    twin = Category.query.filter(
        Category.id != category.id, Category.name == name
    ).first()
    if twin:
        flash(f"«{name}» موجود من قبل", "error")
        return redirect(url_for("admin.categories_list"))

    old_name = category.name
    category.name = name
    category.is_active = bool(request.form.get("is_active"))

    file = request.files.get("image")
    if file and file.filename:
        try:
            new_image = save_image(file, "categories", base_name=category.slug)
            # الصورة القديمة تتمسح غير بعد ما تنجح الجديدة
            if category.image_filename:
                delete_image(category.image_filename, "categories")
            category.image_filename = new_image
        except UploadError as e:
            flash(f"صورة مرفوضة: {e}", "error")

    db.session.commit()

    if old_name != name:
        flash(f"«{old_name}» ولّى «{name}»", "success")
    else:
        flash(f"تم تحديث «{name}»", "success")

    return redirect(url_for("admin.categories_list"))


@admin_bp.route("/categories/<int:category_id>/delete", methods=["POST"])
@login_required
def category_delete(category_id):
    category = Category.query.get_or_404(category_id)
    product_count = Product.query.filter_by(category_id=category.id).count()

    if product_count:
        flash(
            f"ما نقدروش نمسحو «{category.name}» — فيه {product_count} منتج. "
            "بدّل تصنيفهم أولاً.",
            "error",
        )
        return redirect(url_for("admin.categories_list"))

    delete_image(category.image_filename, "categories")
    name = category.name
    db.session.delete(category)
    db.session.commit()
    flash(f"تم حذف التصنيف «{name}»", "success")
    return redirect(url_for("admin.categories_list"))


# ==================== العروض الخاصة ====================

@admin_bp.route("/offers")
@login_required
def offers_list():
    offers = Offer.query.order_by(Offer.sort_order, Offer.created_at.desc()).all()
    return render_template("admin/offers_list.html", offers=offers)


@admin_bp.route("/offers/new", methods=["GET", "POST"])
@admin_bp.route("/offers/<int:offer_id>/edit", methods=["GET", "POST"])
@login_required
def offer_form(offer_id=None):
    offer = Offer.query.get_or_404(offer_id) if offer_id else None
    all_products = Product.query.filter_by(is_active=True).order_by(Product.name).all()

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("عنوان العرض مطلوب", "error")
            return render_template(
                "admin/offer_form.html", offer=offer, all_products=all_products,
                Offer_HERO_STYLES=Offer.HERO_STYLES, Offer_HERO_LAYOUTS=Offer.HERO_LAYOUTS,
                landing_pages=LandingPage.query.order_by(LandingPage.internal_name).all(),
            )

        slug = (request.form.get("slug") or "").strip() or slugify(title)

        clash = Offer.query.filter(Offer.slug == slug)
        if offer:
            clash = clash.filter(Offer.id != offer.id)
        if clash.first():
            flash(f"الرابط '{slug}' مستعمل عند عرض آخر", "error")
            return render_template(
                "admin/offer_form.html", offer=offer, all_products=all_products,
                Offer_HERO_STYLES=Offer.HERO_STYLES, Offer_HERO_LAYOUTS=Offer.HERO_LAYOUTS,
                landing_pages=LandingPage.query.order_by(LandingPage.internal_name).all(),
            )

        if offer is None:
            offer = Offer(title=title, slug=slug, bundle_price=0)
            db.session.add(offer)

        offer.title = title
        offer.slug = slug
        offer.subtitle = (request.form.get("subtitle") or "").strip() or None
        offer.description = (request.form.get("description") or "").strip() or None
        offer.bundle_price = request.form.get("bundle_price", type=int) or 0
        offer.sort_order = request.form.get("sort_order", type=int) or 0
        offer.is_active = bool(request.form.get("is_active"))

        # قسم "عرض خاص" — نتحققو من القيمة باش ما تدخلش قيمة غريبة
        position = request.form.get("spotlight_position", "none")
        offer.spotlight_position = position if position in ("none", "top", "bottom") else "none"
        offer.spotlight_text = (request.form.get("spotlight_text") or "").strip() or None

        # ربط العرض بصفحة هبوط (v184) — فارغ = صفحة العرض العادية
        lp_id = request.form.get("landing_page_id", type=int)
        offer.landing_page_id = lp_id if lp_id and db.session.get(LandingPage, lp_id) else None

        # أماكن الظهور (v183): checkbox غير معلّم = ما يبانش هناك
        offer.show_in_hero = bool(request.form.get("show_in_hero"))
        offer.show_in_grid = bool(request.form.get("show_in_grid"))

        # تصميم الشريحة فالصفحة الرئيسية (v182) — قيم مضبوطة بالقائمة
        style = request.form.get("hero_style", "gold")
        offer.hero_style = style if style in {s[0] for s in Offer.HERO_STYLES} else "gold"
        layout = request.form.get("hero_layout", "split")
        offer.hero_layout = layout if layout in {l[0] for l in Offer.HERO_LAYOUTS} else "split"

        # التواريخ اختيارية
        offer.starts_at = _parse_date(request.form.get("starts_at"))
        offer.ends_at = _parse_end_of_day(request.form.get("ends_at"))

        # صورة البانر
        file = request.files.get("banner")
        if file and file.filename:
            try:
                new_banner = save_image(file, "offers", base_name=slug)
                if offer.banner_filename:
                    delete_image(offer.banner_filename, "offers")
                offer.banner_filename = new_banner
            except UploadError as e:
                flash(f"صورة مرفوضة: {e}", "error")

        db.session.flush()

        # نعاودو بناء قائمة المنتجات من الصفر — أبسط وأأمن من محاولة المطابقة
        OfferItem.query.filter_by(offer_id=offer.id).delete()

        product_ids = request.form.getlist("product_ids", type=int)
        quantities = request.form.getlist("quantities", type=int)

        for idx, pid in enumerate(product_ids):
            if not pid:
                continue
            qty = quantities[idx] if idx < len(quantities) else 1
            db.session.add(
                OfferItem(offer_id=offer.id, product_id=pid, quantity=max(1, qty or 1))
            )

        db.session.commit()

        if not offer.items:
            flash("تنبيه: العرض محفوظ لكن بلا منتجات — ما يبانش فالمتجر", "warning")
        else:
            flash(f"تم حفظ العرض «{offer.title}»", "success")

        return redirect(url_for("admin.offer_form", offer_id=offer.id))

    return render_template(
        "admin/offer_form.html", offer=offer, all_products=all_products,
                Offer_HERO_STYLES=Offer.HERO_STYLES, Offer_HERO_LAYOUTS=Offer.HERO_LAYOUTS,
                landing_pages=LandingPage.query.order_by(LandingPage.internal_name).all(),
    )


@admin_bp.route("/offers/<int:offer_id>/delete", methods=["POST"])
@login_required
def offer_delete(offer_id):
    offer = Offer.query.get_or_404(offer_id)
    delete_image(offer.banner_filename, "offers")
    title = offer.title
    db.session.delete(offer)
    db.session.commit()
    flash(f"تم حذف العرض «{title}»", "success")
    return redirect(url_for("admin.offers_list"))


# ==================== أسعار التوصيل ====================

@admin_bp.route("/shipping")
@login_required
def shipping_list():
    """جدول أسعار التوصيل — عرض حسب شركة مختارة."""

    # نعرضو كل الشركات (حتى المعطّلة) — الأدمن لازمو يشوفهم باش يفعّلهم
    carriers = Carrier.query.order_by(Carrier.name).all()
    if not carriers:
        flash("ماكاين حتى شركة توصيل — شغّل seed_shipping.py أولاً", "warning")
        return render_template("admin/shipping.html", carriers=[], rates=[], current=None)

    carrier_code = request.args.get("carrier") or carriers[0].code
    current = Carrier.query.filter_by(code=carrier_code).first() or carriers[0]

    rates = (
        ShippingRate.query.filter_by(carrier_id=current.id)
        .order_by(ShippingRate.wilaya_code)
        .all()
    )

    return render_template(
        "admin/shipping.html", carriers=carriers, rates=rates, current=current
    )


@admin_bp.route("/shipping/carrier/<int:carrier_id>/toggle", methods=["POST"])
@login_required
def carrier_toggle(carrier_id):
    """
    تفعيل/إلغاء شركة توصيل.

    ⚠️ الشركة المعطّلة ما تبانش للزبون فالـcheckout، لكن **بياناتها تبقى محفوظة**
    (الأسعار والولايات) — فإذا رجعت تخدم معاك، تفعّلها بنقرة وتلقى كلشي كيما كان.

    نمنعو تعطيل آخر شركة نشطة: بلاها ما يقدرش حتى زبون يكمّل طلبية.
    """
    carrier = db.session.get(Carrier, carrier_id) or abort(404)

    if carrier.is_active:
        active_count = Carrier.query.filter_by(is_active=True).count()
        if active_count <= 1:
            flash(
                "ما نقدروش نعطّلو آخر شركة توصيل — بلاها الزبائن ما يقدروش يطلبو. "
                "فعّل وحدة أخرى أولاً.",
                "error",
            )
            return redirect(url_for("admin.shipping_list", carrier=carrier.code))

    carrier.is_active = not carrier.is_active
    db.session.commit()

    state = "مفعّلة" if carrier.is_active else "معطّلة"
    flash(f"{carrier.name} ولّات {state}", "success")
    return redirect(url_for("admin.shipping_list", carrier=carrier.code))


@admin_bp.route("/shipping/update", methods=["POST"])
@login_required
def shipping_update():
    """
    تحديث جماعي لأسعار شركة وحدة.
    خانة فارغة = الخدمة غير متوفرة (None) — ماشي صفر.
    هاد الفرق مهم: صفر معناه "توصيل مجاني"، وفارغ معناه "ما نوصلوش لهنا".
    """
    carrier_id = request.form.get("carrier_id", type=int)
    carrier = Carrier.query.get_or_404(carrier_id)

    updated = 0
    for rate in ShippingRate.query.filter_by(carrier_id=carrier.id).all():
        home_raw = request.form.get(f"home_{rate.id}")
        office_raw = request.form.get(f"office_{rate.id}")

        new_home = int(home_raw) if home_raw and home_raw.strip() else None
        new_office = int(office_raw) if office_raw and office_raw.strip() else None

        if new_home != rate.home_price or new_office != rate.office_price:
            rate.home_price = new_home
            rate.office_price = new_office
            updated += 1

    db.session.commit()
    flash(f"تم تحديث {updated} سعر عند {carrier.name}", "success")
    return redirect(url_for("admin.shipping_list", carrier=carrier.code))


# ==================== أدوات مساعدة داخلية ====================

def _parse_end_of_day(value):
    """
    «ينتهي فـ 31/10» = آخر لحظة من 31 أكتوبر بتوقيت الجزائر (UTC+1)،
    مخزّنة بالـUTC (22:59:59). قبل كانت تتحفظ 00:00 يعني العرض يوقف
    فبداية اليوم المذكور ماشي فنهايتو.
    """
    d = _parse_date(value)
    return d.replace(hour=22, minute=59, second=59) if d else None


def _parse_date(value):
    """يحول نص تاريخ من الفورم لـdatetime. يرجع None إذا فارغ أو غير صالح."""
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        return None


# ==================== الطلبيات ====================



# ⚠️ «الكل» ما يوريش الطلبيات المنتهية (تم التسليم + راجعة) باش تبقى القائمة
# فيها غير اللي تحتاج متابعة. تبانو كي تختار تبويبهم، ولا كي تبحث.
HIDDEN_FROM_ALL = (Order.STATUS_DELIVERED, Order.STATUS_RETURNED)


def _order_neighbours(order, args):
    """
    يلقى الطلبية السابقة والتالية داخل نفس الفلترة.

    ⚠️ نفس منطق _product_neighbours: نبنيو قائمة IDs بالكامل بدل
    استعلام ذكي، لأن الترتيب على created_at قد يشارك قيمًا متساوية.
    عندك مئات الطلبيات ماشي ملايين، فالبساطة تربح.
    """
    if order is None:
        return None, None, 0, 0

    status = args.get("status")
    search = (args.get("q") or "").strip()

    query = Order.query
    if status and status in Order.STATUS_LABELS:
        query = query.filter_by(status=status)
    elif not search:
        query = query.filter(Order.status.notin_(HIDDEN_FROM_ALL))
    if search:
        query = query.filter(
            db.or_(
                Order.order_number.contains(search.upper()),
                Order.customer_phone.contains(search),
                Order.customer_name.contains(search),
            )
        )

    ids = [row.id for row in
           query.order_by(Order.created_at.desc())
                .with_entities(Order.id).all()]

    if order.id not in ids:
        return None, None, 0, len(ids)

    index = ids.index(order.id)
    previous = Order.query.get(ids[index - 1]) if index > 0 else None
    next_ = Order.query.get(ids[index + 1]) if index < len(ids) - 1 else None

    return previous, next_, index + 1, len(ids)

@admin_bp.route("/orders")
@login_required
def orders_list():
    """قائمة الطلبيات مع فلترة بالحالة والبحث."""
    status = request.args.get("status")
    search = (request.args.get("q") or "").strip()

    query = Order.query

    if status and status in Order.STATUS_LABELS:
        query = query.filter_by(status=status)
    elif not search:
        # «الكل» بلا بحث: نخبّيو تم التسليم والراجعة
        query = query.filter(Order.status.notin_(HIDDEN_FROM_ALL))

    if search:
        query = query.filter(
            db.or_(
                Order.order_number.contains(search.upper()),
                Order.customer_phone.contains(search),
                Order.customer_name.contains(search),
            )
        )

    orders = query.order_by(Order.created_at.desc()).limit(200).all()

    # عدّاد لكل حالة — باش الأدمن يشوف بسرعة قداش عندو جديد
    counts = dict(
        db.session.query(Order.status, func.count(Order.id))
        .group_by(Order.status)
        .all()
    )

    return render_template(
        "admin/orders_list.html",
        orders=orders, counts=counts, current_status=status,
        all_count=sum(n for st, n in counts.items() if st not in HIDDEN_FROM_ALL),
        search=search, status_labels=Order.STATUS_LABELS,
    )


@admin_bp.route("/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)

    # ⚠️ كل المنتجات وموديلاتهم — باش الأدمن يزيد حاجة للطلبية بلا ما
    # يخرج من الصفحة. نجيبوهم مرة وحدة هنا بدل ما القالب يسقسي
    # قاعدة البيانات فكل دورة.
    products = (
        Product.query.filter_by(is_active=True)
        .order_by(Product.name)
        .all()
    )

    prev_o, next_o, pos, total = _order_neighbours(order, request.args)

    if not order.track_token:           # طلبية قديمة: نعمّرو رابط التتبّع
        order.ensure_track_token()
        db.session.commit()
    return render_template(
        "admin/order_detail.html",
        order=order,
        status_labels=Order.STATUS_LABELS,
        products=products,
        prev_order=prev_o,
        next_order=next_o,
        order_pos=pos,
        order_total=total,
    )


# ══════════════════════════════════════════════════════════════
# تعديل الطلبية بعد ما تدخل
#
# ⚠️ علاش هاد الميزة موجودة؟
# الزبون يتصل ويقول «زيدلي حاجة» ولا «بدّلي الموديل» ولا منتج ما بقاش
# فالمخزن. قبل، كان لازم تلغي الطلبية وتعاود تديرها من الصفر.
#
# ⚠️ قاعدة مهمة: كل تعديل يستدعي `order.recalculate()`. بلاها، المجاميع
# تبقى قديمة والفاتورة تولّي غالطة — والزبون يخلّص مبلغًا ماشي هو.
# ══════════════════════════════════════════════════════════════

def _guard_editable(order):
    """يرجّع رسالة خطأ إذا الطلبية ما تتعدّلش، ولا None إذا مليح."""
    if not order.is_editable:
        if order.tracking_number:
            return ("الطلبية اتبعثت لشركة التوصيل — التعديل عندنا "
                    "ما يوصلهمش. اتصل بيهم أول.")
        return f"الطلبية «{order.status_label}» ما تتعدّلش."
    return None


@admin_bp.route("/orders/<int:order_id>/items/<int:item_id>/update",
                methods=["POST"])
@login_required
def order_item_update(order_id, item_id):
    """يبدّل كمية ولا سعر وحدة لعنصر فالطلبية."""
    order = Order.query.get_or_404(order_id)
    item = OrderItem.query.filter_by(id=item_id, order_id=order_id).first_or_404()

    blocked = _guard_editable(order)
    if blocked:
        flash(blocked, "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    quantity = request.form.get("quantity", type=int)
    unit_price = request.form.get("unit_price", type=int)

    if quantity is not None:
        if quantity < 1:
            flash("الكمية لازم تكون 1 على الأقل — استعمل «حذف» باش تحيّدو",
                  "error")
            return redirect(url_for("admin.order_detail", order_id=order_id))
        item.quantity = min(quantity, 5000)

    if unit_price is not None:
        if unit_price < 0:
            flash("السعر ما يقدرش يكون سالبًا", "error")
            return redirect(url_for("admin.order_detail", order_id=order_id))
        item.unit_price = unit_price

    order.recalculate()
    db.session.commit()

    flash(f"تعدّل «{item.item_name}» — المجموع الجديد {order.total_display}",
          "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@admin_bp.route("/orders/<int:order_id>/items/<int:item_id>/delete",
                methods=["POST"])
@login_required
def order_item_delete(order_id, item_id):
    """يحيّد عنصرًا من الطلبية."""
    order = Order.query.get_or_404(order_id)
    item = OrderItem.query.filter_by(id=item_id, order_id=order_id).first_or_404()

    blocked = _guard_editable(order)
    if blocked:
        flash(blocked, "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    # ⚠️ طلبية بلا منتجات ما عندهاش معنى — إذا حاب يلغي كلش، يبدّل
    # الحالة لـ«ملغاة» بدل ما يمسح آخر عنصر.
    if len(order.items) <= 1:
        flash("ما تقدرش تحيّد آخر منتج. إذا حبيت تلغي، بدّل الحالة "
              "لـ«ملغاة».", "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    name = item.item_name
    db.session.delete(item)
    db.session.flush()

    # 🔴 خلل كان هنا: `flush()` وحدو ما يكفيش. علاقة `order.items` تبقى
    # محمّلة فالذاكرة بالعنصر المحذوف، فـ`recalculate()` يحسب بيه
    # ويخرج بمجموع قديم — فاتورة غالطة والزبون يخلّص أكثر.
    #
    # `expire()` يجبر SQLAlchemy يعاود يقرا العلاقة من قاعدة البيانات.
    db.session.expire(order, ["items"])

    order.recalculate()
    db.session.commit()

    flash(f"اتحيّد «{name}» — المجموع الجديد {order.total_display}", "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@admin_bp.route("/orders/<int:order_id>/items/add", methods=["POST"])
@login_required
def order_item_add(order_id):
    """يزيد منتجًا للطلبية."""
    order = Order.query.get_or_404(order_id)

    blocked = _guard_editable(order)
    if blocked:
        flash(blocked, "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    variant_id = request.form.get("variant_id", type=int)
    product_id = request.form.get("product_id", type=int)
    quantity = request.form.get("quantity", type=int) or 1
    quantity = max(1, min(quantity, 5000))

    variant = None
    if variant_id:
        variant = ProductVariant.query.get(variant_id)
        if variant is None:
            flash("الموديل ما بقاش موجود", "error")
            return redirect(url_for("admin.order_detail", order_id=order_id))
        product = variant.product
    else:
        product = Product.query.get(product_id) if product_id else None

    if product is None:
        flash("اختار منتجًا أول", "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    # ⚠️ السعر: نستعملو سعر الجملة حسب الكمية إذا كان الموديل عندو
    # مجالات — نفس المنطق اللي يشوفو الزبون فالموقع.
    if variant is not None:
        unit_price = variant.price_for_quantity(quantity)
    else:
        unit_price = product.display_price

    # سعر يدوي يغلب كلش (تسوية مع الزبون)
    manual = request.form.get("unit_price", type=int)
    if manual is not None and manual >= 0:
        unit_price = manual

    image = (variant.image_filename if variant and variant.image_filename
             else product.image_filename)

    db.session.add(OrderItem(
        order_id=order.id,
        product_id=product.id,
        variant_id=variant.id if variant else None,
        item_name=product.name,
        variant_label=variant.label if variant else None,
        unit_price=unit_price,
        quantity=quantity,
        image_filename=image,
    ))
    db.session.flush()

    # ⚠️ نفس السبب: العلاقة لازم تتحدّث قبل إعادة الحساب
    db.session.expire(order, ["items"])

    order.recalculate()
    db.session.commit()

    label = f"{product.name}" + (f" ({variant.label})" if variant else "")
    flash(f"اتزاد «{label}» × {quantity} — المجموع الجديد {order.total_display}",
          "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@admin_bp.route("/orders/<int:order_id>/shipping", methods=["POST"])
@login_required
def order_update_shipping(order_id):
    """يبدّل سعر التوصيل يدويًا (تسوية مع الزبون)."""
    order = Order.query.get_or_404(order_id)

    blocked = _guard_editable(order)
    if blocked:
        flash(blocked, "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    price = request.form.get("shipping_price", type=int)
    if price is None or price < 0:
        flash("سعر التوصيل ما يقدرش يكون سالبًا", "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    order.shipping_price = price
    order.recalculate()
    db.session.commit()

    flash(f"تبدّل سعر التوصيل — المجموع الجديد {order.total_display}",
          "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@admin_bp.route("/orders/<int:order_id>/status", methods=["POST"])
@login_required
def order_update_status(order_id):
    """تبديل حالة الطلبية يدويًا."""

    order = Order.query.get_or_404(order_id)
    new_status = request.form.get("status")

    if new_status not in Order.STATUS_LABELS:
        flash("حالة غير معروفة", "error")
        return redirect(url_for("admin.order_detail", order_id=order.id))

    try:
        messages = _change_order_status(order, new_status)
    except OrderError as e:
        flash(f"ما قدرناش نرجعو الطلبية: {e}", "error")
        return redirect(url_for("admin.order_detail", order_id=order.id))

    for text, category in messages:
        flash(text, category)

    admin_note = request.form.get("admin_note")
    if admin_note is not None:
        order.admin_note = admin_note.strip() or None

    db.session.commit()
    flash(f"الطلبية {order.order_number} ولات: {order.status_label}", "success")
    return redirect(url_for("admin.order_detail", order_id=order.id))


# ==================== الكوبونات ====================

@admin_bp.route("/coupons", methods=["GET", "POST"])
@login_required
def coupons_list():
    if request.method == "POST":
        code = (request.form.get("code") or "").strip().upper()

        if not code:
            flash("كود الكوبون مطلوب", "error")
            return redirect(url_for("admin.coupons_list"))

        if Coupon.query.filter_by(code=code).first():
            flash(f"الكود «{code}» مستعمل من قبل", "error")
            return redirect(url_for("admin.coupons_list"))

        discount_type = request.form.get("discount_type") or Coupon.TYPE_PERCENT
        value = request.form.get("value", type=int) or 0

        # التحقق حسب النوع — نسبة فوق 100% ما عندهاش معنى
        if discount_type == Coupon.TYPE_PERCENT and not (1 <= value <= 100):
            flash("النسبة لازمها تكون بين 1 و100", "error")
            return redirect(url_for("admin.coupons_list"))

        if discount_type == Coupon.TYPE_FIXED and value <= 0:
            flash("مبلغ الخصم لازمو يكون أكبر من صفر", "error")
            return redirect(url_for("admin.coupons_list"))

        coupon = Coupon(
            code=code,
            discount_type=discount_type,
            value=value,
            min_order_amount=request.form.get("min_order_amount", type=int) or 0,
            max_discount=request.form.get("max_discount", type=int) or None,
            usage_limit=request.form.get("usage_limit", type=int) or None,
            starts_at=_parse_date(request.form.get("starts_at")),
            ends_at=_parse_date(request.form.get("ends_at")),
            is_active=True,
        )
        db.session.add(coupon)
        db.session.commit()
        flash(f"تم إنشاء الكوبون «{code}»", "success")
        return redirect(url_for("admin.coupons_list"))

    coupons = Coupon.query.order_by(Coupon.created_at.desc()).all()
    return render_template("admin/coupons.html", coupons=coupons, Coupon=Coupon)


def _generate_reward_code(customer_name):
    """
    كود تلقائي سهل الكتابة: أول حرفين من اسم الزبون + 5 رموز عشوائية.
    «Rania» → RA-7K2QX · «كمال» → KM-X4P9D (الاسم العربي يتحوّل للاتينية).
    الرموز العشوائية بلا حروف تتشابه (0/O و1/I) ويتولّدو بـsecrets.
    """
    import secrets
    from utils import latin_initials
    prefix = latin_initials(customer_name)
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    for _ in range(20):
        code = prefix + "-" + "".join(secrets.choice(alphabet) for _ in range(5))
        if not Coupon.query.filter_by(code=code).first():
            return code
    raise RuntimeError("ما قدرناش نولّدو كود فريد")


class _RewardError(Exception):
    """خطأ فمعطيات كوبون المكافأة (النسبة ولا المدة) — رسالتو تتعرض للأدمن."""


def _reward_params_from_form():
    """
    يقرا نسبة الخصم ومدة الصلاحية من الفورم ويتحقق منهم.
    يرجع (percent, ends_at) ولا يرفع _RewardError برسالة عربية.
    """
    percent = request.form.get("percent", type=int)
    if not percent or not (1 <= percent <= 100):
        raise _RewardError("اكتب نسبة الخصم من 1 لـ100")

    # مدة الصلاحية: فارغ = بلا انتهاء، وإلا من 1 لـ365 يوم
    days_raw = (request.form.get("days") or "").strip()
    ends_at = None
    if days_raw:
        days = request.form.get("days", type=int)
        if not days or not (1 <= days <= 365):
            raise _RewardError("مدة الصلاحية من 1 لـ365 يوم (ولا خلّيها فارغة)")
        ends_at = utcnow() + timedelta(days=days)
    # الحد الأدنى لقيمة المنتجات (بلا التوصيل): فارغ ولا 0 = بلا شرط
    min_raw = (request.form.get("min_amount") or "").strip()
    min_amount = 0
    if min_raw:
        min_amount = request.form.get("min_amount", type=int)
        if min_amount is None or not (0 <= min_amount <= 1_000_000):
            raise _RewardError("الحد الأدنى بالدينار (رقم من 0 لـ1,000,000) — ولا خلّيه فارغ")
    return percent, ends_at, min_amount


def _make_reward_coupon(order, percent, ends_at, min_amount=0):
    """
    يصنع كوبون مكافأة شخصي لطلبية (بلا commit — المستدعي هو اللي يحفظ).

    يرجع (coupon, status):
      "created"        → كوبون جديد
      "existing"       → فيه كوبون صالح من قبل لهاد الطلبية (نرجعوه هو)
      "not_delivered"  → الطلبية ماشي «تم التسليم»
      "bad_phone"      → رقم الزبون ماشي صالح

    القواعد: تتفرض فالسيرفر، سواء الزر الفردي ولا الجماعي.
    """
    from utils import normalize_algerian_phone

    if order.status != Order.STATUS_DELIVERED:
        return None, "not_delivered"

    existing = order.reward_coupon
    if existing and existing.is_active and not existing.is_exhausted:
        return existing, "existing"

    phone = normalize_algerian_phone(order.customer_phone)
    if not phone:
        return None, "bad_phone"

    coupon = Coupon(
        code=_generate_reward_code(order.customer_name),
        discount_type=Coupon.TYPE_PERCENT,
        value=percent,
        usage_limit=1,
        ends_at=ends_at,
        min_order_amount=min_amount or 0,
        is_active=True,
        customer_phone=phone,
        source_order_id=order.id,
    )
    db.session.add(coupon)
    db.session.flush()      # باش الكود الفريد الجاي ما يتكرّرش فنفس الدفعة
    return coupon, "created"


@admin_bp.route("/orders/<int:order_id>/reward-coupon", methods=["POST"])
@login_required
def order_create_reward_coupon(order_id):
    """
    ينشئ كوبون مكافأة شخصي للزبون اللي وصلتو طلبيتو.

    القواعد: الطلبية «تم التسليم» برك · النسبة من 1 لـ100 (الأدمن يحدّدها) ·
    تاع رقم الزبون + مرة وحدة · كوبون واحد صالح لكل طلبية.
    """
    order = db.session.get(Order, order_id) or abort(404)
    back = redirect(url_for("admin.order_detail", order_id=order.id))

    try:
        percent, ends_at, min_amount = _reward_params_from_form()
    except _RewardError as e:
        flash(str(e), "error")
        return back

    try:
        coupon, status = _make_reward_coupon(order, percent, ends_at, min_amount)
        if status == "created":
            audit.record(
                AdminAction.REWARD_COUPON, "order", order.order_number,
                f"{coupon.code} — {percent}%" + (f" (من {min_amount:,} دج)" if min_amount else ""),
            )
            db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("reward coupon: فشل الإنشاء %s", order.order_number)
        flash("ما قدرناش ننشئو الكوبون — جرّب مرة أخرى", "error")
        return back

    if status == "not_delivered":
        flash("الكوبون يتصنع للطلبية اللي وصلت للزبون برك (تم التسليم)", "error")
    elif status == "bad_phone":
        flash("رقم الزبون ماشي صالح — ما نقدروش نربطو الكوبون بيه", "error")
    elif status == "existing":
        flash(f"فيه كوبون صالح لهاد الطلبية: {coupon.code}", "warning")
    else:
        flash(f"تصنع الكوبون {coupon.code} — درك ابعثو للزبون عبر واتساب", "success")
    return back


@admin_bp.route("/orders/bulk-reward", methods=["POST"])
@login_required
def orders_bulk_reward():
    """
    يصنع كوبونات مكافأة لعدة طلبيات دفعة وحدة (من صفحة «الطلبيات»).

    • يتجاهل الطلبيات اللي ماشي «تم التسليم»
    • كوبون واحد لكل زبون: إذا نفس الرقم فأكثر من طلبية مختارة، نصنعو
      لواحدة برك (الأقدم) — ما يتعطاش نفس الزبون عدة مكافآت فنفس الدفعة
    • الطلبية اللي عندها كوبون صالح من قبل تدخل فالقائمة (باش تبعثو) بلا تكرار
    ثم يحوّلك لصفحة فيها زر واتساب لكل زبون.
    """
    from utils import normalize_algerian_phone

    # ⚠️ نفس الطلبية تتبعث مرتين (checkbox فالجدول وفالبطاقة) — نحيّدو التكرار
    ids = list(dict.fromkeys(request.form.getlist("order_ids", type=int)))[:200]
    if not ids:
        flash("ما اخترت حتى طلبية", "warning")
        return redirect(url_for("admin.orders_list"))

    try:
        percent, ends_at, min_amount = _reward_params_from_form()
    except _RewardError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.orders_list"))

    orders = (
        Order.query.filter(Order.id.in_(ids))
        .order_by(Order.created_at.asc())
        .all()
    )

    created = existing = 0
    skipped_status = skipped_phone = skipped_dup = 0
    ready_ids = []
    seen_phones = set()

    try:
        for order in orders:
            phone = normalize_algerian_phone(order.customer_phone)

            if order.status != Order.STATUS_DELIVERED:
                skipped_status += 1
                continue
            if phone and phone in seen_phones:
                skipped_dup += 1
                continue

            coupon, status = _make_reward_coupon(order, percent, ends_at, min_amount)
            if status == "bad_phone":
                skipped_phone += 1
                continue

            seen_phones.add(phone)
            ready_ids.append(order.id)
            if status == "created":
                created += 1
            else:
                existing += 1

        if created:
            audit.record(
                AdminAction.REWARD_COUPON, "order", None,
                f"{created} كوبون مكافأة — {percent}%" + (f" (من {min_amount:,} دج)" if min_amount else ""),
            )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("bulk reward coupons: فشل")
        flash("ما قدرناش ننشئو الكوبونات — جرّب مرة أخرى", "error")
        return redirect(url_for("admin.orders_list"))

    if not ready_ids:
        flash("ما تصنع حتى كوبون: الطلبيات المختارة لازم تكون «تم التسليم»", "warning")
        return redirect(url_for("admin.orders_list"))

    parts = [f"تصنعو {created} كوبون"]
    if existing:
        parts.append(f"{existing} كان عندهم كوبون صالح")
    flash("، ".join(parts), "success")
    skipped = []
    if skipped_status:
        skipped.append(f"{skipped_status} ماشي «تم التسليم»")
    if skipped_dup:
        skipped.append(f"{skipped_dup} نفس الزبون (كوبون واحد برك)")
    if skipped_phone:
        skipped.append(f"{skipped_phone} رقمهم ماشي صالح")
    if skipped:
        flash("تجاهلنا: " + " | ".join(skipped), "warning")

    return redirect(url_for(
        "admin.orders_reward_batch", ids=",".join(str(i) for i in ready_ids)))


@admin_bp.route("/orders/reward-batch")
@login_required
def orders_reward_batch():
    """
    صفحة إرسال كوبونات المكافأة: زبون فكل سطر + زر واتساب برسالتو.

    ⚠️ واتساب ما يسمحش نبعثو رسائل آليًا من الموقع — الأدمن يضغط «ابعث»
    على كل سطر (ولا «افتح التالي»). الصفحة تقرا من القاعدة، ماشي من
    الجلسة، يعني تقدر ترجعلها فأي وقت من نفس الرابط.
    """
    ids = []
    for chunk in (request.args.get("ids") or "").split(","):
        if chunk.strip().isdigit():
            ids.append(int(chunk))
    ids = ids[:200]

    orders = (
        Order.query.filter(Order.id.in_(ids))
        .order_by(Order.created_at.asc())
        .all()
        if ids else []
    )

    rows = []
    for order in orders:
        coupon = order.reward_coupon
        if not coupon or not order.whatsapp_phone:
            continue
        rows.append({"order": order, "coupon": coupon,
                     "sendable": coupon.is_active and not coupon.is_exhausted})

    return render_template("admin/reward_batch.html", rows=rows)


@admin_bp.route("/coupons/<int:coupon_id>/toggle", methods=["POST"])
@login_required
def coupon_toggle(coupon_id):
    coupon = Coupon.query.get_or_404(coupon_id)
    coupon.is_active = not coupon.is_active
    db.session.commit()
    state = "مفعّل" if coupon.is_active else "معطّل"
    flash(f"الكوبون «{coupon.code}» ولا {state}", "success")
    return redirect(url_for("admin.coupons_list"))


@admin_bp.route("/coupons/<int:coupon_id>/delete", methods=["POST"])
@login_required
def coupon_delete(coupon_id):
    coupon = Coupon.query.get_or_404(coupon_id)
    code = coupon.code
    db.session.delete(coupon)
    db.session.commit()
    flash(f"تم حذف الكوبون «{code}»", "success")
    return redirect(url_for("admin.coupons_list"))


# ==================== السلات المتروكة ====================

@admin_bp.route("/abandoned")
@login_required
def abandoned_list():
    """
    السلات المتروكة — زبائن بداو الطلب وما كملوش.

    ⚠️ هذي من أهم صفحات لوحة التحكم: كل سطر هنا = بيعة ضائعة تقدر ترجعها
    بمكالمة أو رسالة واتساب.
    """
    status = request.args.get("status", AbandonedCart.STATUS_NEW)

    query = AbandonedCart.query
    if status and status in AbandonedCart.STATUS_LABELS:
        query = query.filter_by(status=status)

    carts = query.order_by(AbandonedCart.updated_at.desc()).limit(200).all()

    counts = dict(
        db.session.query(AbandonedCart.status, func.count(AbandonedCart.id))
        .group_by(AbandonedCart.status)
        .all()
    )

    # القيمة الضائعة المحتملة — يوري للأدمن قداش يستاهل الوقت
    potential = (
        db.session.query(func.sum(AbandonedCart.cart_total))
        .filter(AbandonedCart.status.in_([AbandonedCart.STATUS_NEW, AbandonedCart.STATUS_CONTACTED]))
        .scalar()
    ) or 0

    return render_template(
        "admin/abandoned.html",
        carts=carts, counts=counts, current_status=status,
        status_labels=AbandonedCart.STATUS_LABELS, potential=potential,
    )


@admin_bp.route("/abandoned/<int:cart_id>/status", methods=["POST"])
@login_required
def abandoned_update(cart_id):
    cart = db.session.get(AbandonedCart, cart_id) or abort(404)

    new_status = request.form.get("status")
    if new_status in AbandonedCart.STATUS_LABELS:
        cart.status = new_status

    note = request.form.get("admin_note")
    if note is not None:
        cart.admin_note = note.strip() or None

    db.session.commit()
    flash("تم التحديث", "success")
    return redirect(url_for("admin.abandoned_list", status=request.args.get("back")))


# ==================== التقييمات ====================

@admin_bp.route("/reviews")
@login_required
def reviews_list():
    """
    التقييمات — الجديدة تحتاج موافقتك قبل ما تبان فالمتجر.
    """
    show = request.args.get("show", "pending")

    query = Review.query
    if show == "pending":
        query = query.filter_by(is_approved=False)
    elif show == "approved":
        query = query.filter_by(is_approved=True)
    elif show == "store":
        # تقييمات المتجر وحدها — مفيدة كي تحب تراجعهم دفعة
        query = query.filter(Review.product_id.is_(None))

    reviews = query.order_by(Review.created_at.desc()).limit(200).all()

    pending_count = Review.query.filter_by(is_approved=False).count()
    approved_count = Review.query.filter_by(is_approved=True).count()
    store_count = Review.query.filter(Review.product_id.is_(None)).count()

    return render_template(
        "admin/reviews.html",
        reviews=reviews, show=show,
        pending_count=pending_count, approved_count=approved_count,
        store_count=store_count,
    )


@admin_bp.route("/reviews/<int:review_id>/approve", methods=["POST"])
@login_required
def review_approve(review_id):
    review = db.session.get(Review, review_id) or abort(404)
    review.is_approved = True
    db.session.commit()
    flash("تمت الموافقة — التقييم يبان درك فالمتجر", "success")
    return redirect(request.referrer or url_for("admin.reviews_list"))


@admin_bp.route("/reviews/<int:review_id>/rating", methods=["POST"])
@login_required
def review_update_rating(review_id):
    """
    تعديل عدد نجوم تقييم (بعد ما تتفاهم مع الزبون).

    ⚠️ نسجّلو التعديل فسجل الأدمن (القيمة القديمة ← الجديدة) باش يبقى
    أثر واضح وما يولّيش تلاعب بلا علم.
    """
    review = db.session.get(Review, review_id) or abort(404)
    rating = request.form.get("rating", type=int)

    if not rating or not (1 <= rating <= 5):
        flash("اختر عدد نجوم من 1 لـ5", "error")
        return redirect(request.referrer or url_for("admin.reviews_list"))

    if rating != review.rating:
        old = review.rating
        review.rating = rating
        audit.record(
            AdminAction.REVIEW_EDIT, "review", str(review.id),
            f"نجوم {old} ← {rating} ({review.author_name})",
        )
        db.session.commit()
        flash(f"تبدّلت النجوم من {old} لـ{rating}", "success")

    return redirect(request.referrer or url_for("admin.reviews_list"))


@admin_bp.route("/reviews/<int:review_id>/delete", methods=["POST"])
@login_required
def review_delete(review_id):
    review = db.session.get(Review, review_id) or abort(404)
    db.session.delete(review)
    db.session.commit()
    flash("تم حذف التقييم", "success")
    return redirect(request.referrer or url_for("admin.reviews_list"))


# ==================== الاقتراحات (Upsell) ====================

@admin_bp.route("/upsells", methods=["GET", "POST"])
@login_required
def upsells_list():
    if request.method == "POST":
        suggested_id = request.form.get("suggested_product_id", type=int)

        if not suggested_id:
            flash("اختر المنتج المقترح", "error")
            return redirect(url_for("admin.upsells_list"))

        trigger_id = request.form.get("trigger_product_id", type=int) or None

        # ⚠️ نمنعو اقتراح منتج على نفسو — ما عندوش معنى
        if trigger_id and trigger_id == suggested_id:
            flash("ما يمكنش تقترح المنتج على نفسو", "error")
            return redirect(url_for("admin.upsells_list"))

        db.session.add(UpsellRule(
            trigger_product_id=trigger_id,
            suggested_product_id=suggested_id,
            discount_percent=request.form.get("discount_percent", type=int) or 0,
            sort_order=request.form.get("sort_order", type=int) or 0,
            is_active=True,
        ))
        db.session.commit()
        flash("تمت إضافة الاقتراح", "success")
        return redirect(url_for("admin.upsells_list"))

    rules = UpsellRule.query.order_by(UpsellRule.sort_order).all()
    products = Product.query.filter_by(is_active=True).order_by(Product.name).all()

    return render_template("admin/upsells.html", rules=rules, products=products)


@admin_bp.route("/upsells/<int:rule_id>/delete", methods=["POST"])
@login_required
def upsell_delete(rule_id):
    rule = db.session.get(UpsellRule, rule_id) or abort(404)
    db.session.delete(rule)
    db.session.commit()
    flash("تم حذف الاقتراح", "success")
    return redirect(url_for("admin.upsells_list"))


# ==================== تكامل ZR Express ====================

def _claim_order_for_zr(order_id):
    """
    يحجز الطلبية للإرسال لـZR — عملية ذرية.

    يرجّع (True, None) إذا نجح الحجز، ولا (False, "رسالة") إذا لا.

    ⚠️ الحجز يمرّ من قاعدة البيانات (UPDATE ... WHERE) ماشي من بايثون،
    باش خيطان/عمّال متوازيين ما يقدروش يحجزو نفس الطلبية بجوج.

    الحالات المقبولة للحجز:
      • idle / failed  → إرسال أول ولا إعادة محاولة
      • sending قديمة  → الحجز السابق مات (العامل طاح، الشبكة قطعت)
    """
    from sqlalchemy import or_, update as sa_update

    now = utcnow()
    stale_before = now - timedelta(seconds=Order.ZR_CLAIM_TIMEOUT_SECONDS)

    stmt = (
        sa_update(Order)
        .where(
            Order.id == order_id,
            Order.tracking_number.is_(None),
            or_(
                Order.send_state.in_([Order.ZR_IDLE, Order.ZR_FAILED]),
                Order.send_state.is_(None),
                # حجز قديم = ميت، نسمحو بإعادة المحاولة
                db.and_(
                    Order.send_state == Order.ZR_SENDING,
                    Order.send_started_at < stale_before,
                ),
            ),
        )
        .values(send_state=Order.ZR_SENDING, send_started_at=now, send_error=None)
    )

    rowcount = db.session.execute(stmt).rowcount
    db.session.commit()

    if rowcount:
        return True, None

    # ما نجحش الحجز — نفهمو علاش باش الرسالة تكون مفيدة
    order = db.session.get(Order, order_id)
    if order is None:
        return False, "الطلبية ما بقاتش موجودة"
    if order.tracking_number:
        return False, f"هاد الطلبية تبعثت من قبل — رقم التتبّع: {order.tracking_number}"
    return False, "الطلبية راهي فطريق الإرسال درك. استنى شوية وشوف من جديد."


def _release_zr_claim(order_id, reason):
    """
    يحرّر الحجز بعد فشل، باش إعادة المحاولة تخدم فورًا.
    نسجّلو السبب باش يبان للأدمن فصفحة الطلبية.
    """
    from sqlalchemy import update as sa_update

    db.session.rollback()   # ننظّفو أي جلسة معطّلة من الفشل
    db.session.execute(
        sa_update(Order)
        .where(Order.id == order_id, Order.tracking_number.is_(None))
        .values(send_state=Order.ZR_FAILED, send_error=(reason or "")[:300])
    )
    db.session.commit()


@admin_bp.route("/orders/<int:order_id>/send-to-carrier", methods=["POST"])
@login_required
def order_send_to_carrier(order_id):
    """
    يبعث الطلبية لـZR Express ويسجّل رقم التتبّع.

    ⚠️ الفشل ما يكسرش الطلبية: إذا ZR ما ردّش، الطلبية تبقى كيما هي والأدمن
    يقدر يبعثها يدويًا أو يعاود يجرّب. ما نخسروش بيعة بسبب مشكل شبكة.
    """
    from services.zrexpress import (
        create_parcel, ZRError, resolve_territory_id, resolve_hub_id,
        resolve_city_id, is_configured,
    )

    order = db.session.get(Order, order_id) or abort(404)

    # World Express تخدم بمنصة ECOTRACK — منطق مختلف تمامًا عن ZR
    if order.carrier_code == "worldexpress":
        return _send_order_to_world(order_id)
    if order.carrier_code == "yalidine":
        return _send_order_to_yalidine(order_id)

    if not is_configured():
        flash(
            "مفاتيح ZR Express غير معمّرة. حط ZR_TENANT_ID و ZR_API_KEY فملف .env",
            "error",
        )
        return redirect(url_for("admin.order_detail", order_id=order.id))

    # 🔴 حماية من الإرسال المزدوج (كان يخلق طردين حقيقيين عند ZR).
    #
    # الفحص القديم `if order.tracking_number` كان نمط «افحص ثم نفّذ»:
    # بين الفحص وحفظ الرقم كاين نداء API يدوم ثواني. gunicorn يخدم
    # بـ4 خيوط، فنقرة مزدوجة تعطي طلبين يعدّيو الفحص بجوج.
    #
    # درك نحجزو الطلبية بـUPDATE مشروط عند قاعدة البيانات: طلب واحد
    # برك ينجح (rowcount=1)، والثاني يلقى rowcount=0 ويتوقف.
    claimed, claim_message = _claim_order_for_zr(order_id)
    if not claimed:
        flash(claim_message, "warning")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    # نعاودو نقراو الطلبية بعد الحجز — الحالة تبدّلت فقاعدة البيانات
    db.session.expire(order)

    # ⚠️ معرّف البلدية عند ZR (territoryId) يسهّل الأمور، لكن **ماشي إجباري**:
    # ZR يقبل الطرد بالاسم كذلك. قبل، كنّا نمنعو الإرسال بلا معرّف — وهذا
    # كان يحبس طلبيات صحيحة بلا سبب. درك نبعثو ونخلّيو ZR يقرّر.
    # 🔴 كان هنا `if ... order.delivery_type != "office"` — يعني طلبيات
    # المكتب ما كانت تدوّر أبدًا على المعرّف، وتتبعث بلا `territoryId`،
    # وZR يرد 400 بلا رسالة. **كل** طلبيات المكتب كانت تفشل.
    #
    # درك `resolve_territory_id` تتكفّل بالنوعين: المنزل من البلدية،
    # والمكتب من بلدية المكتب نفسو.
    territory_id, why = resolve_territory_id(order)

    # ⚠️ للمكتب: نجيبو UUID تاع الـhub. ZR يستنّى المعرّف ماشي الاسم —
    # هاد الشي هو أرجح سبب الـ400 الفارغ اللي دام 5 جولات.
    city_id, city_why = resolve_city_id(order)
    if city_id and not order.wilaya_external_id:
        order.wilaya_external_id = city_id
        db.session.commit()
    elif not city_id:
        flash(f"⚠️ ما لقيناش معرّف الولاية عند ZR ({city_why})", "warning")

    hub_id, hub_why = resolve_hub_id(order)
    if hub_id and not order.hub_external_id:
        order.hub_external_id = hub_id
        db.session.commit()
    elif order.delivery_type == "office" and not hub_id:
        flash(f"⚠️ ما لقيناش معرّف المكتب عند ZR ({hub_why})", "warning")

    if territory_id and not order.commune_external_id:
        order.commune_external_id = territory_id
        db.session.commit()

    if not territory_id:
        # ⚠️ ما نبلعوش الفشل بصمت — الأدمن لازم يعرف قبل ما يشوف 400
        flash(
            f"ما لقيناش معرّف الوجهة عند ZR ({why}). "
            "اضغط «افحص علاش ما لقيناش المعرّف» فصفحة التشخيص.",
            "warning",
        )

    try:
        result = create_parcel(order, territory_id=territory_id, hub_id=hub_id)
    except ZRError as e:
        # ⚠️ نحرّرو الحجز باش الأدمن يقدر يعاود المحاولة فورًا،
        # بدل ما تبقى الطلبية عالقة فحالة "sending" للأبد.
        _release_zr_claim(order_id, str(e))
        flash(f"ما قدرناش نبعثو الطلبية: {e}", "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))
    except Exception:
        _release_zr_claim(order_id, "خطأ غير متوقّع")
        current_app.logger.exception("فشل غير متوقّع فإرسال الطلبية %s لـZR", order_id)
        flash("صرا خطأ غير متوقّع فالإرسال. شوف السجلات وعاود جرّب.", "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    order.tracking_number = result["tracking_number"]
    order.carrier_parcel_id = result.get("parcel_id")
    order.send_state = Order.ZR_SENT
    audit.record(AdminAction.SHIPMENT_SENT, "order", order.order_number,
                 f"تتبّع: {result['tracking_number']}")
    order.send_error = None

    # نرقّيو الحالة تلقائيًا — الطلبية ولّات فعلاً عند الشركة
    if order.status == Order.STATUS_PENDING:
        order.status = Order.STATUS_CONFIRMED

    db.session.commit()

    flash(f"✓ تبعثت لـZR Express — رقم التتبّع: {order.tracking_number}", "success")
    return redirect(url_for("admin.order_detail", order_id=order.id))


def _send_order_to_world(order_id):
    """
    يبعث الطلبية لـWorld Express (ECOTRACK) ويسجّل رقم التتبّع.

    نفس ضمانات ZR: حجز ذرّي ضد النقرة المزدوجة، والفشل ما يكسرش الطلبية.
    """
    from services import worldexpress as world

    order = db.session.get(Order, order_id) or abort(404)
    back = redirect(url_for("admin.order_detail", order_id=order_id))

    if not world.is_configured():
        flash("توكن World Express غير معمّر. حط WORLD_API_TOKEN فملف .env", "error")
        return back

    claimed, claim_message = _claim_order_for_zr(order_id)
    if not claimed:
        flash(claim_message, "warning")
        return back
    db.session.expire(order)

    try:
        result = world.create_order(order)
    except world.WorldError as e:
        if e.parcel_may_exist:
            # 🔴 ممكن الطرد اتنشأ عند World. نخلّيو الحجز «sending» (يتحرّر
            # وحدو بعد 3 دقائق) ونسجّلو السبب — باش ما يتخلقش طرد مضاعف.
            from sqlalchemy import update as sa_update
            db.session.rollback()
            db.session.execute(
                sa_update(Order).where(Order.id == order_id,
                                       Order.tracking_number.is_(None))
                .values(send_error=str(e)[:300]))
            db.session.commit()
            flash(f"⚠️ {e}", "warning")
        else:
            _release_zr_claim(order_id, str(e))
            flash(f"ما قدرناش نبعثو الطلبية لـWorld Express: {e}", "error")
        return back
    except Exception:
        _release_zr_claim(order_id, "خطأ غير متوقّع")
        current_app.logger.exception(
            "فشل غير متوقّع فإرسال الطلبية %s لـWorld Express", order_id)
        flash("صرا خطأ غير متوقّع فالإرسال. شوف السجلات وعاود جرّب.", "error")
        return back

    order.tracking_number = result["tracking_number"]
    order.send_state = Order.ZR_SENT
    order.send_error = None
    order.carrier_state = "prete_a_expedier"
    audit.record(AdminAction.SHIPMENT_SENT, "order", order.order_number,
                 f"World Express — تتبّع: {order.tracking_number}")
    if order.status == Order.STATUS_PENDING:
        order.status = Order.STATUS_CONFIRMED
    db.session.commit()

    flash(f"✓ تبعثت لـWorld Express — رقم التتبّع: {order.tracking_number}", "success")

    if world.auto_validate_enabled():
        _validate_world_order(order)
    else:
        flash("الطرد مازال «prête à expédier» عند World. اضغط «تأكيد الإرسال» "
              "باش تسلّموه للشركة (بعدها ما يتبدّلش).", "info")
    return back


def _validate_world_order(order, ask_collection=None):
    """يأكّد الإرسال (Expédier). يرجّع True/False ويعرض الرسالة."""
    from services import worldexpress as world

    try:
        message = world.validate_order(order.tracking_number, ask_collection)
    except world.WorldError as e:
        flash(f"ما قدرناش نأكّدو الإرسال عند World Express: {e}", "error")
        return False
    order.carrier_state = "expediee"
    audit.record(AdminAction.SHIPMENT_SENT, "order", order.order_number,
                 f"World Express — تأكيد الإرسال: {order.tracking_number}")
    db.session.commit()
    flash(f"✓ تأكّد الإرسال عند World Express ({message})", "success")
    return True


@admin_bp.route("/orders/<int:order_id>/world-validate", methods=["POST"])
@login_required
def order_world_validate(order_id):
    """يأكّد إرسال الطرد (Expédier) عند World Express — ما يتبدّلش بعدها."""
    order = db.get_or_404(Order, order_id)
    if order.carrier_code != "worldexpress" or not order.tracking_number:
        flash("هاد الطلبية ما تبعثتش لـWorld Express", "warning")
    else:
        _validate_world_order(
            order, ask_collection=(request.form.get("ask_collection") == "1") or None)
    return redirect(url_for("admin.order_detail", order_id=order.id))


@admin_bp.route("/orders/<int:order_id>/world-label")
@login_required
def order_world_label(order_id):
    """البوردرو **الرسمي** تاع World Express (PDF)."""
    from flask import Response
    from services import worldexpress as world

    order = db.get_or_404(Order, order_id)
    back = redirect(url_for("admin.order_detail", order_id=order.id))
    if order.carrier_code != "worldexpress" or not order.tracking_number:
        flash("هاد الطلبية ما تبعثتش لـWorld Express — ما كاينش بوردرو رسمي", "warning")
        return back
    try:
        pdf = world.get_label_pdf(order.tracking_number)
    except world.WorldError as e:
        flash(str(e), "error")
        return back
    except Exception:
        current_app.logger.exception("order_world_label: خطأ غير متوقع %s", order.order_number)
        flash("صار خطأ غير متوقع فجلب البوردرو. جرّب مرة أخرى.", "error")
        return back

    safe = "".join(c for c in order.tracking_number if c.isalnum())[:40] or "label"
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="world-{safe}.pdf"'})


def _send_order_to_yalidine(order_id):
    """
    يبعث الطلبية لـYalidine Express ويسجّل رقم التتبّع.

    نفس ضمانات ZR/World: حجز ذرّي ضد النقرة المزدوجة، والفشل ما يكسرش الطلبية.
    ما كاينش «تأكيد الإرسال» منفصل: الطرد يتنشأ «en préparation» وYalidine تكمل.
    """
    from services import yalidine as yal

    order = db.session.get(Order, order_id) or abort(404)
    back = redirect(url_for("admin.order_detail", order_id=order_id))

    if not yal.is_configured():
        flash("مفاتيح Yalidine غير معمّرة. حط YALIDINE_API_ID و YALIDINE_API_TOKEN فملف .env",
              "error")
        return back

    claimed, claim_message = _claim_order_for_zr(order_id)
    if not claimed:
        flash(claim_message, "warning")
        return back
    db.session.expire(order)

    try:
        result = yal.create_order(order)
    except yal.YalidineError as e:
        if e.parcel_may_exist:
            # 🔴 ممكن الطرد اتنشأ عند Yalidine. نخلّيو الحجز «sending» (يتحرّر
            # وحدو بعد 3 دقائق) ونسجّلو السبب — باش ما يتخلقش طرد مضاعف.
            from sqlalchemy import update as sa_update
            db.session.rollback()
            db.session.execute(
                sa_update(Order).where(Order.id == order_id,
                                       Order.tracking_number.is_(None))
                .values(send_error=str(e)[:300]))
            db.session.commit()
            flash(f"⚠️ {e}", "warning")
        else:
            _release_zr_claim(order_id, str(e))
            flash(f"ما قدرناش نبعثو الطلبية لـYalidine: {e}", "error")
        return back
    except Exception:
        _release_zr_claim(order_id, "خطأ غير متوقّع")
        current_app.logger.exception(
            "فشل غير متوقّع فإرسال الطلبية %s لـYalidine", order_id)
        flash("صرا خطأ غير متوقّع فالإرسال. شوف السجلات وعاود جرّب.", "error")
        return back

    order.tracking_number = result["tracking_number"]
    order.send_state = Order.ZR_SENT
    order.send_error = None
    order.carrier_state = "en_preparation"
    audit.record(AdminAction.SHIPMENT_SENT, "order", order.order_number,
                 f"Yalidine — تتبّع: {order.tracking_number}")
    if order.status == Order.STATUS_PENDING:
        order.status = Order.STATUS_CONFIRMED
    db.session.commit()

    flash(f"✓ تبعثت لـYalidine — رقم التتبّع: {order.tracking_number}", "success")
    return back


@admin_bp.route("/orders/<int:order_id>/yalidine-label")
@login_required
def order_yalidine_label(order_id):
    """البوردرو **الرسمي** تاع Yalidine — نحوّلو لرابطهم (فيه توكن، ما نخزّنوهش)."""
    from services import yalidine as yal

    order = db.get_or_404(Order, order_id)
    back = redirect(url_for("admin.order_detail", order_id=order.id))
    if order.carrier_code != "yalidine" or not order.tracking_number:
        flash("هاد الطلبية ما تبعثتش لـYalidine — ما كاينش بوردرو رسمي", "warning")
        return back
    try:
        url = yal.get_label_url(order.tracking_number)
    except yal.YalidineError as e:
        flash(str(e), "error")
        return back
    except Exception:
        current_app.logger.exception("order_yalidine_label: خطأ غير متوقع %s", order.order_number)
        flash("صار خطأ غير متوقع فجلب البوردرو. جرّب مرة أخرى.", "error")
        return back
    return redirect(url)


@admin_bp.route("/orders/<int:order_id>/refresh-tracking", methods=["POST"])
@login_required
def order_refresh_tracking(order_id):
    """يحدّث حالة الطرد من ZR Express ويبدّل حالة الطلبية إذا لازم."""
    from services.zrexpress import get_parcel_status, ZRError

    order = db.session.get(Order, order_id) or abort(404)

    if not order.tracking_number:
        flash("هاد الطلبية ما تبعثتش لشركة التوصيل بعد", "warning")
        return redirect(url_for("admin.order_detail", order_id=order.id))

    # World Express: نفس المنطق بعميل ECOTRACK (الاسم المستعار يخلّي الباقي يخدم كيما هو)
    is_world = order.carrier_code == "worldexpress"
    is_yalidine = order.carrier_code == "yalidine"
    carrier_label = "World Express" if is_world else ("Yalidine" if is_yalidine else "ZR")
    if is_world:
        from services.worldexpress import (
            get_order_status as get_parcel_status, WorldError as ZRError)
    elif is_yalidine:
        from services.yalidine import (
            get_order_status as get_parcel_status, YalidineError as ZRError)

    try:
        status = get_parcel_status(order.tracking_number)
    except ZRError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.order_detail", order_id=order.id))
    except Exception:
        # ⚠️ صفحة 500 عامة ما تفيدش الأدمن — نمسكو أي استثناء غير متوقع
        # (رد ZR بشكل غريب، قطع اتصال...) ونرجّعوه كرسالة واضحة.
        current_app.logger.exception(
            "order_refresh_tracking: خطأ غير متوقع للطلبية %s", order.order_number
        )
        flash(
            "صار خطأ غير متوقع فتحديث حالة التتبّع. جرّب مرة أخرى، "
            "وإذا تكرر المشكل راسلنا.",
            "error",
        )
        return redirect(url_for("admin.order_detail", order_id=order.id))

    # ⚠️ بلاغ حقيقي (v148): ZR بدّلت شكل الرد (state ولّى كائن). نفرض هنا
    # أن new_state نص دايمًا، مهما كان شكل status.
    new_state = _normalize_zr_state(status)
    if not new_state:
        flash("ZR رجع رد ما فيهش حالة واضحة — جرّب مرة أخرى بعد شوية", "warning")
        return redirect(url_for("admin.order_detail", order_id=order.id))

    # ── نترجمو حالة ZR لحالة الطلبية فالموقع (القواعد فـ_decide_status_change) ──
    status_msg = None      # (نص, نوع) نعرضوه للأدمن
    kind, target = _decide_status_change(order, new_state)

    try:
        order.carrier_state = new_state

        if kind == "unknown":
            status_msg = (
                "حالة الطلبية فالموقع ما تبدّلتش (حالة ZR هذي ما نعرفوهاش — "
                "بدّلها يدويًا إذا لازم)", "warning")
        elif kind == "locked":
            status_msg = (
                f"الطلبية فحالة «{order.status_label}» — ما بدّلناهاش تلقائيًا",
                "warning")
        elif kind == "change":
            msgs = _change_order_status(
                order, target, note=f" (تلقائي من {carrier_label}: {new_state})")
            status_msg = (
                f"حالة الطلبية ولات: {Order.STATUS_LABELS[target]}", "success")
            for text, category in msgs:
                flash(text, category)
        # "same" / "back" → ما نقولو والو

        db.session.commit()
    except OrderError as e:
        db.session.rollback()
        current_app.logger.warning(
            "order_refresh_tracking: تعذّر تبديل الحالة %s: %s",
            order.order_number, e)
        # نحفظو على الأقل حالة ZR
        try:
            order.carrier_state = new_state
            db.session.commit()
        except Exception:
            db.session.rollback()
        status_msg = (f"حالة ZR تحدّثت بصح ما قدرناش نبدّلو حالة الطلبية: {e}",
                      "warning")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "order_refresh_tracking: فشل الحفظ للطلبية %s", order.order_number
        )
        flash("جبنا الحالة من ZR بصح ما قدرناش نحفظوها — جرّب مرة أخرى", "error")
        return redirect(url_for("admin.order_detail", order_id=order.id))

    flash(f"الحالة عند الشركة: {new_state}", "success")
    if status_msg:
        flash(status_msg[0], status_msg[1])
    return redirect(url_for("admin.order_detail", order_id=order.id))


# أقصى عدد طلبيات فضغطة وحدة + أقصى وقت — باش الصفحة ما تعلّقش
# (gunicorn timeout = 120 ثانية، وكل اتصال بـZR يقدر ياخذ حتى 25 ثانية)
BULK_ZR_MAX_ORDERS = 40
BULK_ZR_MAX_SECONDS = 80


@admin_bp.route("/orders/refresh-all-tracking", methods=["POST"])
@login_required
def orders_refresh_all_tracking():
    """
    يحدّث حالة كل الطلبيات الممكنة من ZR دفعة وحدة.

    «الممكنة» = عندها رقم تتبّع + مازالت ما وصلتش (ماشي مسلّمة/ملغاة/راجعة).
    نبدؤو بالأقدم تحديثًا. إذا بقاو طلبيات، تضغط الزر مرة أخرى.
    """
    from services.zrexpress import get_parcel_status, ZRError, is_configured

    if not is_configured():
        flash("ZR Express ماهيش مضبوطة — شوف صفحة التكاملات", "error")
        return redirect(url_for("admin.orders_list"))

    candidates = (
        Order.query
        .filter(
            Order.tracking_number.isnot(None),
            Order.tracking_number != "",
            Order.status.notin_([Order.STATUS_DELIVERED, *Order.STATUS_TERMINAL]),
        )
        .order_by(Order.created_at.asc())
        .all()
    )

    total_candidates = len(candidates)
    started = time()
    checked = changed = unchanged = unknown = failed = 0
    unknown_orders = []      # "BD-.. (شركة): الحالة"
    carrier_mismatch = []    # طلبيات زبونها اختار World/Yalidine ومبعوثة لـZR
    failed_numbers = []
    stopped_early = False

    for order in candidates:
        if checked + failed >= BULK_ZR_MAX_ORDERS or time() - started > BULK_ZR_MAX_SECONDS:
            stopped_early = True
            break

        try:
            # كل طلبية تتفحص عند شركتها (World/Yalidine ماشي ZR)
            fetch_status, label = get_parcel_status, "ZR"
            if order.carrier_code == "worldexpress":
                from services.worldexpress import get_order_status as fetch_status
                label = "World Express"
            elif order.carrier_code == "yalidine":
                from services.yalidine import get_order_status as fetch_status
                label = "Yalidine"

            status = fetch_status(order.tracking_number)
            new_state = _normalize_zr_state(status)
            if not new_state:
                raise ZRError("رد بلا حالة")

            kind, target = _decide_status_change(order, new_state)
            order.carrier_state = new_state

            if kind == "change":
                _change_order_status(
                    order, target, note=f" (تلقائي من {label}: {new_state})")
                changed += 1
            elif kind == "unknown":
                unknown += 1
                unknown_orders.append(
                    f"{order.order_number} ({order.carrier_name or order.carrier_code or '؟'}): {new_state}")
            else:
                unchanged += 1

            db.session.commit()
            checked += 1
            if order.carrier_code not in (None, "", "zrexpress", "worldexpress", "yalidine"):
                carrier_mismatch.append(
                    f"{order.order_number} ({order.carrier_name or order.carrier_code})")
        except Exception as e:  # noqa: BLE001 — طلبية وحدة ما توقفش الباقي
            db.session.rollback()
            failed += 1
            failed_numbers.append(f"{order.order_number} ({str(e)[:60]})")
            current_app.logger.warning(
                "refresh_all_tracking: فشلت %s: %s", order.order_number, e)

    if not total_candidates:
        flash("ماكاين حتى طلبية تستنى تحديث (كلها مسلّمة أو ما تبعثتش لـZR)", "info")
        return redirect(url_for("admin.orders_list"))

    flash(
        f"تفحّصنا {checked} طلبية: {changed} تبدّلت حالتها، "
        f"{unchanged} بلا تغيير"
        + (f"، {unknown} بحالة ZR مجهولة" if unknown else "")
        + (f"، {failed} فشلت" if failed else ""),
        "success" if not failed else "warning",
    )
    if unknown_orders:
        flash("حالات ZR ما نعرفوهاش: " + " | ".join(unknown_orders[:10]), "warning")
    if carrier_mismatch:
        flash(
            "⚠️ هاد الطلبيات الزبون اختار فيها شركة أخرى، بصح مبعوثة لـZR "
            "(الحالة جات من ZR): " + " | ".join(carrier_mismatch[:10]),
            "warning")
    if failed_numbers:
        flash("فشلت: " + "، ".join(failed_numbers[:10]), "error")
    if stopped_early:
        remaining = total_candidates - checked - failed
        flash(f"بقاو {remaining} طلبية — اضغط الزر مرة أخرى باش نكملو", "info")

    return redirect(url_for("admin.orders_list"))


@admin_bp.route("/whatsapp-templates")
@login_required
def wa_templates_page():
    """تبويب «رسائل واتساب»: نموذج لكل نوع رسالة، والأدمن يعدّلو."""
    from services import wa_templates as wa

    kinds = [
        {**spec, "text": wa.get_template(spec["key"]),
         "customized": wa.is_customized(spec["key"])}
        for spec in wa.KINDS
    ]
    symbols = wa._symbols()
    return render_template(
        "admin/wa_templates.html",
        kinds=kinds,
        variables=wa.VARIABLES,
        symbol_help=wa.SYMBOL_HELP,
        preview_ctx={**symbols, **wa.sample_context()},
        max_len=wa.MAX_LEN,
    )


@admin_bp.route("/whatsapp-templates/<kind>", methods=["POST"])
@login_required
def wa_template_save(kind):
    """يحفظ تعديل الأدمن لنموذج رسالة (يرفض الإيموجي الملوّن والنص الناقص)."""
    from services import wa_templates as wa

    if kind not in wa.KIND_BY_KEY:
        abort(404)
    back = redirect(url_for("admin.wa_templates_page") + f"#kind-{kind}")
    try:
        wa.save_template(kind, request.form.get("text", ""))
        audit.record(AdminAction.WA_TEMPLATE, "wa_template", kind, "تعديل")
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        flash(str(e), "error")
        return back
    flash(f"تحفظ نموذج «{wa.KIND_BY_KEY[kind]['label']}»", "success")
    return back


@admin_bp.route("/whatsapp-templates/<kind>/reset", methods=["POST"])
@login_required
def wa_template_reset(kind):
    """يرجّع النص الأصلي لنوع رسالة (يمسح تعديل الأدمن)."""
    from services import wa_templates as wa

    if kind not in wa.KIND_BY_KEY:
        abort(404)
    wa.reset_template(kind)
    audit.record(AdminAction.WA_TEMPLATE, "wa_template", kind, "رجوع للأصلي")
    db.session.commit()
    flash(f"رجع النص الأصلي لـ«{wa.KIND_BY_KEY[kind]['label']}»", "success")
    return redirect(url_for("admin.wa_templates_page") + f"#kind-{kind}")


@admin_bp.route("/integrations", methods=["GET", "POST"])
@login_required
def integrations():
    """صفحة التكاملات — اختبار الاتصال بشركات التوصيل."""
    from services.zrexpress import test_connection, is_configured

    from services.zrexpress import sync_communes, ZRError
    from models import Carrier, Commune, CarrierCommune
    from services.worldexpress import is_configured as _world_is_configured
    from services.yalidine import is_configured as _yalidine_is_configured

    zr_result = None
    world_result = None
    yalidine_result = None
    sync_result = None
    inspect_data = None

    if request.method == "POST":
        action = request.form.get("test") or request.form.get("action")

        if action == "zr":
            ok, message = test_connection()
            zr_result = {"ok": ok, "message": message}

        elif action == "yalidine":
            from services import yalidine as _yal
            ok, message = _yal.test_connection()
            yalidine_result = {"ok": ok, "message": message}

        elif action == "world":
            from services import worldexpress as _world
            ok, message = _world.test_connection()
            world_result = {"ok": ok, "message": message}

        elif action == "inspect":
            # تشخيص: نوريّو البيانات الخام باش نعرفو بنيتها الحقيقية
            from services.zrexpress import inspect_territories
            import json as _json
            try:
                info = inspect_territories()
                inspect_data = {
                    "count": info["count"],
                    "fields": ", ".join(info["fields"]),
                    "sample": _json.dumps(info["sample"], ensure_ascii=False, indent=2),
                }
            except ZRError as e:
                zr_result = {"ok": False, "message": str(e)}

        elif action == "sync_territories":
            try:
                sync_result = sync_communes()
            except ZRError as e:
                zr_result = {"ok": False, "message": str(e)}

    # كم بلدية محفوظة درك — نعرضوها للأدمن باش يعرف واش لازم يزامن
    linked = 0
    wilayas_covered = 0
    office_wilayas = 0
    carrier = Carrier.query.filter_by(code="zrexpress").first()
    if carrier:
        # بلديات التوصيل للمنزل
        linked = CarrierCommune.query.filter_by(
            carrier_id=carrier.id, has_home_delivery=True
        ).count()
        wilayas_covered = (
            db.session.query(Commune.wilaya_code)
            .join(CarrierCommune, CarrierCommune.commune_id == Commune.id)
            .filter(CarrierCommune.carrier_id == carrier.id,
                    CarrierCommune.has_home_delivery.is_(True))
            .distinct().count()
        )
        # ولايات فيها مكتب استلام
        office_wilayas = (
            db.session.query(Commune.wilaya_code)
            .join(CarrierCommune, CarrierCommune.commune_id == Commune.id)
            .filter(CarrierCommune.carrier_id == carrier.id,
                    CarrierCommune.has_pickup_point.is_(True))
            .distinct().count()
        )

    return render_template(
        "admin/integrations.html",
        zr_configured=is_configured(),
        zr_result=zr_result,
        world_configured=_world_is_configured(),
        world_result=world_result,
        yalidine_configured=_yalidine_is_configured(),
        yalidine_result=yalidine_result,
        sync_result=sync_result,
        inspect_data=inspect_data,
        linked_communes=linked,
        wilayas_covered=wilayas_covered,
        office_wilayas=office_wilayas,
    )


@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    """
    إعدادات المتجر — خيارات صغيرة يتحكم فيها الأدمن بلا ما يلمس الكود.
    """
    from models import StoreSetting

    if request.method == "POST":
        # الخانات المربّعة (checkbox) ما تتبعثش كي تكون مطفية،
        # فنعتمدو على وجودها فالنموذج.
        StoreSetting.set("show_coupon_field", "1" if request.form.get("show_coupon_field") else "0")
        db.session.commit()

        flash("تحفظو الإعدادات", "success")
        return redirect(url_for("admin.settings"))

    return render_template(
        "admin/settings.html",
        show_coupon_field=StoreSetting.get_bool("show_coupon_field"),
    )


# ==================== صفحات الهبوط ====================

def _read_landing_form(data):
    """يقرا فورم صفحة الهبوط — مشترك بين الإنشاء والتعديل."""
    return {
        "internal_name": (data.get("internal_name") or "").strip(),
        "slug": slugify(data.get("slug") or ""),
        "product_id": data.get("product_id", type=int),
        "variant_id": data.get("variant_id", type=int) or None,
        "headline": (data.get("headline") or "").strip(),
        "subheadline": (data.get("subheadline") or "").strip() or None,
        "body": (data.get("body") or "").strip() or None,
        "benefits": (data.get("benefits") or "").strip() or None,
        "cta_text": (data.get("cta_text") or "").strip() or "اطلب درك",
        "campaign_price": data.get("campaign_price", type=int) or None,
        "compare_at_price": data.get("compare_at_price", type=int) or None,
        "show_countdown": bool(data.get("show_countdown")),
        "countdown_hours": data.get("countdown_hours", type=int) or 24,
        "stock_warning": (data.get("stock_warning") or "").strip() or None,
        "free_shipping": bool(data.get("free_shipping")),
        "max_quantity": data.get("max_quantity", type=int) or 10,
        # ---------- المظهر والباقة (v179) ----------
        "template": _pick(data.get("template"), LandingPage.THEMES, "gold"),
        "image_fit": _pick(data.get("image_fit"), LandingPage.FITS, "contain"),
        "bundle_product_id": data.get("bundle_product_id", type=int) or None,
        "show_variant_picker": bool(data.get("show_variant_picker")),
        # ---------- العرض والتخطيط (v180) — كلها اختيارية ----------
        "offer_ends_on": _parse_iso_date(data.get("offer_ends_on")),
        "stock_left": data.get("stock_left", type=int) or None,
        "layout": _pick(data.get("layout"), LandingPage.LAYOUTS, "split"),
    }


def _parse_iso_date(value):
    """«2026-10-31» ← date. فارغ أو غلط ← None (التاريخ اختياري)."""
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _pick(value, options, default):
    """يرجّع القيمة إذا كانت من الخيارات المسموحة، وإلا الافتراضي."""
    return value if value in {o[0] for o in options} else default


def _landing_form_ctx():
    """قوائم القوالب وعرض الصورة للفورم."""
    return {
        "LandingPage_THEMES": LandingPage.THEMES,
        "LandingPage_FITS": LandingPage.FITS,
        "LandingPage_LAYOUTS": LandingPage.LAYOUTS,
    }


def _landing_extra_errors(form):
    """تحققات خاصة بالباقة — مشتركة بين الإنشاء والتعديل."""
    errors = []
    if form["bundle_product_id"] and form["bundle_product_id"] == form["product_id"]:
        errors.append("المنتج الثابت فالباقة لازم يكون غير المنتج الأساسي")
    if form["bundle_product_id"] and not form["campaign_price"]:
        errors.append("الباقة تحتاج «سعر الحملة» (سعر الباقة كاملة)")
    return errors


@admin_bp.route("/landing-pages")
@login_required
def landing_list():
    pages = LandingPage.query.order_by(LandingPage.created_at.desc()).all()
    return render_template("admin/landing_list.html", pages=pages)


@admin_bp.route("/landing-pages/new", methods=["GET", "POST"])
@login_required
def landing_new():
    products = Product.query.filter_by(is_active=True).order_by(Product.name).all()

    if request.method == "POST":
        form = _read_landing_form(request.form)

        errors = []
        if not form["internal_name"]:
            errors.append("اكتب اسم داخلي للصفحة")
        if not form["slug"]:
            errors.append("الرابط مطلوب")
        elif LandingPage.query.filter_by(slug=form["slug"]).first():
            errors.append(f"الرابط «{form['slug']}» مستعمل من قبل")
        if not form["product_id"]:
            errors.append("اختر المنتج")
        if not form["headline"]:
            errors.append("اكتب العنوان الرئيسي")
        errors += _landing_extra_errors(form)

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/landing_form.html",
                                   page=None, products=products, form=form,
                                   **_landing_form_ctx())

        page = LandingPage(**form)
        db.session.add(page)
        db.session.commit()

        flash(f"تم إنشاء صفحة الهبوط — الرابط: /lp/{page.slug}", "success")
        return redirect(url_for("admin.landing_list"))

    return render_template("admin/landing_form.html",
                           page=None, products=products, form={},
                           **_landing_form_ctx())


@admin_bp.route("/landing-pages/<int:page_id>/edit", methods=["GET", "POST"])
@login_required
def landing_edit(page_id):
    page = LandingPage.query.get_or_404(page_id)
    products = Product.query.filter_by(is_active=True).order_by(Product.name).all()

    if request.method == "POST":
        form = _read_landing_form(request.form)

        errors = []
        if not form["slug"]:
            errors.append("الرابط مطلوب")
        else:
            clash = LandingPage.query.filter(
                LandingPage.slug == form["slug"],
                LandingPage.id != page.id,
            ).first()
            if clash:
                errors.append(f"الرابط «{form['slug']}» مستعمل فصفحة أخرى")
        if not form["headline"]:
            errors.append("اكتب العنوان الرئيسي")
        errors += _landing_extra_errors(form)

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/landing_form.html",
                                   page=page, products=products, form=form,
                                   **_landing_form_ctx())

        for key, value in form.items():
            setattr(page, key, value)
        db.session.commit()

        flash("تم حفظ التعديلات", "success")
        return redirect(url_for("admin.landing_list"))

    return render_template("admin/landing_form.html",
                           page=page, products=products, form={},
                           **_landing_form_ctx())


@admin_bp.route("/landing-pages/<int:page_id>/toggle", methods=["POST"])
@login_required
def landing_toggle(page_id):
    page = LandingPage.query.get_or_404(page_id)
    page.is_active = not page.is_active
    db.session.commit()
    state = "مفعّلة" if page.is_active else "معطّلة"
    flash(f"صفحة «{page.internal_name}» ولات {state}", "success")
    return redirect(url_for("admin.landing_list"))


@admin_bp.route("/landing-pages/<int:page_id>/delete", methods=["POST"])
@login_required
def landing_delete(page_id):
    page = LandingPage.query.get_or_404(page_id)
    name = page.internal_name
    db.session.delete(page)
    db.session.commit()
    flash(f"تم حذف صفحة «{name}»", "success")
    return redirect(url_for("admin.landing_list"))


def _safe(fn, *args):
    """
    ينفّذ نداء تشخيصي ويرجّع None إذا طاح، مع تسجيل السبب.

    ⚠️ صفحة التشخيص لازم تحلّ **دايمًا**. إذا انهارت كي ZR يطيح،
    الأدمن يبقى بلا أي معلومة — وهي أسوأ حالة ممكنة.
    """
    try:
        return fn(*args)
    except Exception as e:
        current_app.logger.warning("🔍 نداء تشخيصي طاح: %s", e)
        session["zr_diag_error"] = str(e)
        return None


def _zr_report(order, ctx):
    """
    يبني تقرير التشخيص كنص عادي — باش الأدمن ينسخو ويبعثو.

    ⚠️ علاه نبنيوه فالسيرفر ماشي بالجافاسكريبت من الصفحة؟
    نسخ من الصفحة يجيب النص المعروض برك: الجداول تولّي مشوّهة،
    والأقسام المطوية ما تتنسخش. هنا عندنا البيانات الخام كاملة.

    ⚠️ **ماكانش مفاتيح فالتقرير** — نقولو «معمّرة/ناقصة» برك.
    التقرير راح يتبعث فواتساب، وأي مفتاح فيه يولّي مكشوف.
    """
    lines = []

    def head(title):
        lines.append("")
        lines.append("=" * 46)
        lines.append(title)
        lines.append("=" * 46)

    def row(label, value):
        lines.append(f"{label}: {value}")

    head(f"تشخيص ZR — {order.order_number}")
    row("النسخة", f"{ctx['app_version']} — {ctx['build_note']}")
    row("المفاتيح", "معمّرة" if ctx["config_ok"] else "ناقصة")
    row("رابط الـAPI", ctx["base_url"])
    row("نوع التوصيل", order.delivery_type or "—")
    row("الولاية", f"{order.wilaya_code} {order.wilaya_name}")
    row("البلدية", order.commune or "—")
    row("المكتب", order.pickup_point_name or "—")
    row("المبلغ", order.total)
    row("رقم التتبّع", order.tracking_number or "—")

    head("الفحص المحلي")
    if ctx["problems"]:
        for problem in ctx["problems"]:
            lines.append(f"  - {problem}")
    else:
        lines.append("سليم من جهتنا")

    head("الـpayload اللي نبعثوه")
    lines.append(ctx["preview"] or f"(فشل البناء) {ctx['build_error']}")

    last = ctx.get("last") or {}
    head("آخر رد من ZR")
    if last.get("status"):
        row("المسار", f"{last.get('method')} {last.get('path')}")
        row("كود الحالة", last.get("status"))
        row("حجم المحتوى", last.get("content_length"))
        lines.append("الرد الخام:")
        # ⚠️ نقصّو على 1500 حرف: رد /hubs/search وحدو 78 كيلو، وكان
        # يغرق التقرير كامل — الأدمن ينسخ 80 ألف حرف وما يلقى فيهم
        # المعلومة المهمة. أول 1500 فيهم الخطأ إذا كان.
        raw = str(last.get("response") or "(فارغ)")
        if len(raw) > 1500:
            raw = raw[:1500] + f"\n... [اتقصّ — الطول الكامل {len(raw)} حرف]"
        lines.append(raw)
        if ctx.get("last_payload"):
            lines.append("اللي بعثناه وقتها:")
            lines.append(ctx["last_payload"])
    else:
        lines.append("مازال ما بعثنا والو من آخر إعادة تشغيل")

    # ---------- الأقسام اللي تبان غير بعد «شخّص كل شي» ----------
    diag = ctx.get("territory_diag")
    if diag:
        head("بحث المنطقة")
        row("الولاية", diag.get("wilaya_code"))
        row("البلدية المطلوبة", diag.get("commune_name") or "—")
        row("بعد التطبيع", diag.get("normalized") or "—")
        row("الولاية موجودة عند ZR", "نعم" if diag.get("wilaya_found") else "لا")
        row("المطابقة", diag.get("match") or diag.get("search_match") or "ما لقيناهاش")
        row("مناطق ZR", diag.get("total_territories"))
        if diag.get("error"):
            row("خطأ", diag["error"])
        candidates = diag.get("wilaya_communes") or []
        if candidates:
            lines.append(f"بلديات هاد الولاية عند ZR ({len(candidates)}):")
            for name in candidates[:20]:
                lines.append(f"  - {name}")

    if ctx.get("hub_match"):
        head("مطابقة المكتب")
        lines.append(str(ctx["hub_match"]))

    if ctx.get("endpoint_probe"):
        head("مسارات المكاتب")
        lines.append(str(ctx["endpoint_probe"]))

    if ctx.get("levels"):
        head("أنواع المناطق")
        lines.append(str(ctx["levels"]))

    if ctx.get("lab"):
        head("مختبر الإرسال")
        for attempt in ctx["lab"]:
            lines.append(f"  [{attempt.get('status')}] {attempt.get('label')}"
                         f" — {attempt.get('body') or '(فارغ)'}")

    return "\n".join(str(line) for line in lines)


@admin_bp.route("/orders/<int:order_id>/zr-debug")
@login_required
def order_zr_debug(order_id):
    """
    تشخيص الإرسال لـZR — يعرض بالضبط واش نبعثو وواش يرد.

    ⚠️ علاش هاد الصفحة؟
    ZR يرد بـ400 «One or more validation errors» بلا تفاصيل فبعض الحالات.
    بلا ما نشوفو الـpayload الحقيقي والرد الخام، أي إصلاح يكون تخمين.
    """
    from services import zrexpress as zr

    order = Order.query.get_or_404(order_id)

    # نبنيو نفس الـpayload اللي راح يتبعث — بلا ما نبعثوه
    preview, build_error = None, None
    try:
        import json as _json
        # ⚠️ المعاينة لازم تكون **نسخة طبق الأصل** من اللي يبعثو الإرسال
        # الحقيقي. قبل، كانت تبني الـpayload بلا `hubId` — فتوري طردًا
        # لمكتب بلا ما تقول أي مكتب، والأدمن يشخّص على معلومة غالطة.
        # (صرات فعلاً: التشخيص يوري payload ناقص والإرسال يبعث كامل.)
        preview_territory, _why = zr.resolve_territory_id(order)
        preview_hub, _hub_why = (
            zr.resolve_hub_id(order) if order.delivery_type == "office"
            else (None, None)
        )
        payload = zr.build_parcel_payload(order, preview_territory, preview_hub)
        # ⚠️ ensure_ascii=False باش العربية تبان كيما هي، ماشي \u0627\u0644...
        preview = _json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        build_error = str(e)

    # ⚠️ زر واحد «شخّص كل شي» يشعّل كل الفحوصات اللي **ما تكتبش والو**
    # عند ZR. كانو 5 أزرار منفصلة، والأدمن لازم يضغطهم وحدة بوحدة
    # ويقارن — وينسى وحدة فيهم فكل مرة.
    #
    # الأزرار اللي **تنشئ طرود حقيقية** (المختبر والإرسال التجريبي)
    # ما دخلوش هنا قصدًا: ضغطة وحدة كانت تولّد 4 طرود لطلبية وحدة.
    diagnose_all = bool(request.args.get("all"))

    def wanted(flag):
        return diagnose_all or bool(request.args.get(flag))

    # ⚠️ نجمعو الـcontext فقاموس واحد باش التقرير النصّي والقالب
    # يقراو **نفس البيانات**. لو بنيناهم منفصلين، التقرير يقدر
    # يقول حاجة والصفحة توري حاجة أخرى — وهذا أسوأ من بلا تقرير.
    ctx = dict(
        order=order,
        preview=preview,
        build_error=build_error,
        problems=zr._check_parcel_data(order),
        diagnose_all=diagnose_all,
        # ⚠️ للمكتب نشخّصو على **بلدية المكتب** ماشي على `commune`
        # الفارغة — وإلا التشخيص يقول «ما لقيناش» بلا ما يقول واش دوّر.
        territory_diag=(
            _safe(zr.diagnose_territory_lookup,
                order.wilaya_code,
                zr.office_commune(order) if order.delivery_type == "office"
                else order.commune,
            )
            if wanted("check_territory") else None
        ),
        # نتائج مختبر الإرسال (إذا اتشغّل قبل شوية)
        lab=session.pop("zr_lab", None),
        # ⚠️ كل نداء لـZR ملفوف: صفحة **التشخيص** ما لازمش تنهار كي
        # ZR يطيح — هي بالضبط الصفحة اللي نجيو ليها كي يطيح شي حاجة.
        # (طاحت 500 فالإنتاج علاه.)
        hubs=_safe(zr.search_hubs) if wanted("hubs") else None,
        hub_match=(
            _safe(zr.resolve_hub_id, order) if wanted("hubs") else None
        ),
        spec=_safe(zr.fetch_openapi_spec) if wanted("spec") else None,
        endpoint_probe=(
            _safe(zr.probe_pickup_endpoints) if wanted("probe") else None
        ),
        levels=(
            _safe(zr.inspect_territory_levels) if wanted("levels") else None
        ),
        diag_error=session.pop("zr_diag_error", None),
        last=zr.LAST_EXCHANGE,
        last_payload=(
            __import__("json").dumps(zr.LAST_EXCHANGE.get("payload"),
                                     ensure_ascii=False, indent=2)
            if zr.LAST_EXCHANGE.get("payload") else None
        ),
        app_version=__import__("VERSION").VERSION,
        build_note=__import__("VERSION").BUILD_NOTE,
        config_ok=bool(os.environ.get("ZR_TENANT_ID", "").strip()
                       and os.environ.get("ZR_API_KEY", "").strip()),
        base_url=zr.BASE_URL,
    )

    ctx["report"] = _zr_report(order, ctx)

    return render_template("admin/zr_debug.html", **ctx)


@admin_bp.route("/orders/<int:order_id>/zr-lab", methods=["POST"])
@login_required
def order_zr_lab(order_id):
    """
    مختبر الإرسال: يجرّب عدة أشكال للـpayload ويوقف عند أول نجاح.

    ⚠️ ZR يرد 400 بحجم 0 — بلا أي رسالة. التخمين ما نفعش ثلاث جولات.
    هنا نجرّبو الأشكال المحتملة ونشوفو أشمن واحد يعدّي فعلاً.

    ⚠️ إذا نجح شكل، الطرد **اتنشأ حقيقة** — نحفظو رقم التتبّع.
    """
    from services import zrexpress as zr

    order = Order.query.get_or_404(order_id)

    if not zr.is_configured():
        flash("مفاتيح ZR غير معمّرة", "error")
        return redirect(url_for("admin.order_zr_debug", order_id=order.id))

    territory_id, _why = zr.resolve_territory_id(order)
    attempts = zr.send_variants(order, territory_id)

    session["zr_lab"] = [
        {"label": a["label"], "status": a["status"],
         "body": (a["body"] or "")[:400], "ok": a["ok"]}
        for a in attempts
    ]

    winner = next((a for a in attempts if a["ok"]), None)

    if winner:
        flash(f"✅ نجح الشكل: «{winner['label']}» — الطرد اتنشأ فعلاً",
              "success")

        # ⚠️ الطرد موجود درك عند ZR — نحفظو رقم التتبّع وإلا يضيع
        try:
            import json as _json
            data = _json.loads(winner["body"] or "{}")
            tracking = (data.get("trackingNumber")
                        or data.get("tracking_number")
                        or (data.get("data") or {}).get("trackingNumber"))
            if tracking:
                order.tracking_number = tracking
                db.session.commit()
                flash(f"رقم التتبّع: {tracking}", "success")
            else:
                flash("⚠️ نجح لكن ماكانش رقم تتبّع فالرد — راجع عند ZR",
                      "warning")
        except Exception:
            flash("⚠️ ما قدرناش نقراو رقم التتبّع — راجع الرد تحت", "warning")
    else:
        flash(f"كل الأشكال ({len(attempts)}) فشلو — شوف التفاصيل تحت",
              "error")

    return redirect(url_for("admin.order_zr_debug", order_id=order.id))


@admin_bp.route("/orders/<int:order_id>/zr-prepare", methods=["POST"])
@login_required
def order_zr_prepare(order_id):
    """
    يجهّز ما يحتاجو ZR قبل الإرسال: الزبون + المنتج فكتالوجهم.

    ⚠️ منفصل على الإرسال عمدًا: إذا التجهيز طاح، نشوفو رسالة ZR
    بالضبط بدل ما تتخبّى داخل فشل الإرسال.
    """
    from services import zrexpress as zr

    order = Order.query.get_or_404(order_id)

    if not zr.is_configured():
        flash("مفاتيح ZR غير معمّرة", "error")
        return redirect(url_for("admin.order_zr_debug", order_id=order.id))

    # 1) المنتج العام فكتالوج ZR (مرة وحدة للمتجر كامل)
    (pid, sku), prod_why = zr.ensure_zr_product()
    if pid:
        flash(f"✅ منتج ZR جاهز: {sku} ({pid})", "success")
    else:
        flash(f"🔴 المنتج: {prod_why}", "error")

    # 2) الزبون
    cust_id, cust_why = zr.ensure_customer(order)
    if cust_id:
        if not order.customer_external_id:
            order.customer_external_id = cust_id
            db.session.commit()
        flash(f"✅ الزبون جاهز عند ZR: {cust_id}", "success")
    else:
        flash(f"🔴 الزبون: {cust_why}", "error")

    return redirect(url_for("admin.order_zr_debug", order_id=order.id))


@admin_bp.route("/orders/<int:order_id>/zr-lab-dims", methods=["POST"])
@login_required
def order_zr_lab_dims(order_id):
    """
    مختبر الأبعاد: طرود **تجريبية** بمبلغ صغير، كل وحدة تعزل بُعدًا.

    ⚠️ الفرق على المختبر الأول: هنا ما نجرّبوش نبعثو الطلبية الحقيقية،
    نجرّبو نعرفو **واش الإرسال خدم عمرو** — بالدار، بالحد الأدنى،
    بلا معرّف، بلا عربية.
    """
    from services import zrexpress as zr

    order = Order.query.get_or_404(order_id)

    if not zr.is_configured():
        flash("مفاتيح ZR غير معمّرة", "error")
        return redirect(url_for("admin.order_zr_debug", order_id=order.id))

    territory_id, _why = zr.resolve_territory_id(order)
    attempts = zr.lab_dimensions(order, territory_id)

    session["zr_lab"] = [
        {"label": a["label"], "status": a["status"],
         "body": (a["body"] or "")[:400], "ok": a["ok"]}
        for a in attempts
    ]

    winner = next((a for a in attempts if a["ok"]), None)

    if winner:
        # ⚠️ ما نحفظوش رقم التتبّع: هذا طرد **تجريبي** بمبلغ 100 دج،
        # ماشي طلبية الزبون. ربطو بالطلبية يخربّق الحسابات.
        flash(
            f"✅ نجح: «{winner['label']}» — معناها الإرسال يخدم، "
            "والمشكل فهاد البُعد بالضبط.",
            "success",
        )
        flash("⚠️ اتنشأ طرد تجريبي عند ZR — امسحو من لوحتهم.", "warning")
    else:
        flash(
            f"كل الأبعاد ({len(attempts)}) فشلو — معناها المشكل ماشي "
            "فالطلبية ولا فنوع التوصيل، بل فالحساب ولا فحقل ناقص. "
            "لازم تتواصل مع دعم ZR.",
            "error",
        )

    return redirect(url_for("admin.order_zr_debug", order_id=order.id))


@admin_bp.route("/orders/<int:order_id>/zr-send-test", methods=["POST"])
@login_required
def order_zr_send_test(order_id):
    """
    يجرّب الإرسال ويرجّع للتشخيص — بلا ما يغيّر حالة الطلبية.

    مفيد باش نجرّبو تعديلات على الـpayload بلا ما نخربو الطلبية.
    """
    from services import zrexpress as zr

    order = Order.query.get_or_404(order_id)

    # ⚠️ نفس الدالة اللي يستعملها الإرسال الحقيقي — وإلا التشخيص
    # يقوللك حاجة والإرسال يدير حاجة أخرى.
    had_id = bool(order.commune_external_id)
    territory_id, why = zr.resolve_territory_id(order)

    if territory_id and not had_id:
        order.commune_external_id = territory_id
        db.session.commit()
        flash(f"لقينا معرّف الوجهة وحفظناه: {territory_id}", "success")
    elif not territory_id:
        flash(f"ما لقيناش معرّف الوجهة ({why}) — شوف القسم 3ب تحت", "warning")

    # نفس الشي للمكتب: UUID تاع الـhub
    city_id, city_why = zr.resolve_city_id(order)
    if city_id and not order.wilaya_external_id:
        order.wilaya_external_id = city_id
        db.session.commit()
        flash(f"لقينا معرّف الولاية وحفظناه: {city_id}", "success")

    hub_id, hub_why = zr.resolve_hub_id(order)
    if hub_id and not order.hub_external_id:
        order.hub_external_id = hub_id
        db.session.commit()
        flash(f"لقينا معرّف المكتب (hub) وحفظناه: {hub_id}", "success")
    elif order.delivery_type == "office" and not hub_id:
        flash(f"ما لقيناش معرّف المكتب ({hub_why})", "warning")

    try:
        zr.create_parcel(order, territory_id=territory_id, hub_id=hub_id)
        flash("نجح الإرسال التجريبي — شوف الرد تحت", "success")
    except Exception as e:
        flash(f"فشل: {e}", "error")

    return redirect(url_for("admin.order_zr_debug", order_id=order.id))


@admin_bp.route("/orders/<int:order_id>/delete", methods=["POST"])
@login_required
def order_delete(order_id):
    """
    يحذف طلبية نهائيًا.

    ⚠️ نقطتان مهمتان:

    1) **المخزون**: إذا الطلبية مازالت نشطة (ماشي ملغاة)، المخزون
       مازال محجوز عندها. لازم نرجّعوه قبل الحذف، وإلا المخزون
       ينقص بلا سبب ويولّي غلط بصمت.

    2) **الطرد عند ZR**: إذا الطلبية تبعثت، حذفها من عندنا
       **ما يلغيش الطرد عند ZR**. نحذّرو الأدمن باش يلغيه يدويًا.
    """
    order = Order.query.get_or_404(order_id)
    number = order.order_number

    # نرجّعو المخزون إذا كان مازال محجوز
    if order.status != Order.STATUS_CANCELLED:
        restore_stock(order)

    warning = None
    if order.tracking_number:
        warning = (
            f"⚠️ الطلبية كانت مبعوثة لـZR (رقم التتبّع {order.tracking_number}). "
            "الحذف هنا ما يلغيش الطرد — ألغيه من موقع ZR."
        )

    # أسطر الطلبية تتحذف معها (cascade)
    for item in list(order.items):
        db.session.delete(item)
    db.session.delete(order)
    db.session.commit()

    flash(f"تم حذف الطلبية {number} نهائيًا", "success")
    if warning:
        flash(warning, "warning")

    return redirect(url_for("admin.orders_list"))


def _render_bordereaux(orders):
    """يرسم صفحة البوردرو (A6 لكل طلبية) — الأدمن يطبعها ولا يحفظها PDF."""
    from services.barcode import code128_svg

    barcodes = {
        o.id: code128_svg(o.tracking_number or o.order_number) for o in orders
    }
    return render_template(
        "admin/bordereau.html", orders=orders, barcodes=barcodes,
        sender_phone=current_app.config.get("WHATSAPP_NUMBER"),
    )


@admin_bp.route("/orders/<int:order_id>/bordereau")
@login_required
def order_bordereau(order_id):
    """بوردرو طلبية وحدة."""
    order = db.get_or_404(Order, order_id)
    return _render_bordereaux([order])


@admin_bp.route("/orders/bordereau", methods=["POST"])
@login_required
def orders_bulk_bordereau():
    """بوردرو للطلبيات المختارة فالقائمة (ورقة لكل طلبية)."""
    ids = list(dict.fromkeys(request.form.getlist("order_ids", type=int)))
    if not ids:
        flash("ما اخترت حتى طلبية", "warning")
        return redirect(url_for("admin.orders_list"))

    by_id = {o.id: o for o in Order.query.filter(Order.id.in_(ids)).all()}
    orders = [by_id[i] for i in ids if i in by_id]
    return _render_bordereaux(orders)


def _is_safe_label_url(url):
    """نقبلو غير روابط https عامة (ماشي localhost ولا IP) — الرابط يجي من ZR."""
    from urllib.parse import urlparse
    import ipaddress

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or host == "localhost":
        return False
    try:
        ipaddress.ip_address(host)
        return False            # IP مباشر = مرفوض
    except ValueError:
        return True


def _download_label_pdf(url):
    """ينزّل PDF تاع ملصق (حد 5 ميغا، مهلة 20 ثانية) ويرجّع bytes."""
    import requests as _rq

    if not _is_safe_label_url(url):
        raise ValueError("رابط الملصق غير مقبول")
    resp = _rq.get(url, timeout=20, stream=True)
    resp.raise_for_status()
    data = resp.raw.read(5 * 1024 * 1024 + 1, decode_content=True)
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("ملف الملصق كبير بزاف")
    if not data.startswith(b"%PDF"):
        raise ValueError("الملف راجع ماشي PDF")
    return data


@admin_bp.route("/orders/<int:order_id>/zr-label")
@login_required
def order_zr_label(order_id):
    """البوردرو **الرسمي** تاع ZR Express لطلبية وحدة (يفتح PDF)."""
    from services.zrexpress import get_label_files, ZRError

    order = db.get_or_404(Order, order_id)
    back = redirect(url_for("admin.order_detail", order_id=order.id))

    if not order.tracking_number:
        flash("هاد الطلبية ما تبعثتش لـZR Express بعد — ما كاينش بوردرو رسمي", "warning")
        return back

    try:
        urls, failed = get_label_files([order.tracking_number], "a6")
    except ZRError as e:
        flash(str(e), "error")
        return back
    except Exception:
        current_app.logger.exception("order_zr_label: خطأ غير متوقع %s", order.order_number)
        flash("صار خطأ غير متوقع فجلب البوردرو من ZR. جرّب مرة أخرى.", "error")
        return back

    url = urls.get(order.tracking_number)
    if not url or not _is_safe_label_url(url):
        flash("ZR ما عطاتش الملصق تاع هاد الطرد (رقم التتبع: "
              f"{order.tracking_number}). جرّب «بوردرو بسيط».", "error")
        return back
    return redirect(url)


@admin_bp.route("/orders/zr-labels", methods=["POST"])
@login_required
def orders_bulk_zr_labels():
    """بوردرو ZR الرسمي للطلبيات المختارة: ملف PDF واحد، ورقة لكل طرد."""
    from concurrent.futures import ThreadPoolExecutor
    from io import BytesIO
    from flask import Response
    from pypdf import PdfReader, PdfWriter
    from services.zrexpress import get_label_files, ZRError

    MAX_LABELS = 40
    back = redirect(url_for("admin.orders_list"))

    ids = list(dict.fromkeys(request.form.getlist("order_ids", type=int)))
    if not ids:
        flash("ما اخترت حتى طلبية", "warning")
        return back

    by_id = {o.id: o for o in Order.query.filter(Order.id.in_(ids)).all()}
    orders = [by_id[i] for i in ids if i in by_id]
    sent = [o for o in orders if o.tracking_number]
    skipped = len(orders) - len(sent)

    if not sent:
        flash("ولا وحدة من الطلبيات المختارة تبعثت لـZR — ما كاينش بوردرو رسمي", "warning")
        return back
    if len(sent) > MAX_LABELS:
        flash(f"بزاف: اختار {MAX_LABELS} طلبية كحد أقصى فالمرة", "warning")
        return back

    try:
        urls, failed = get_label_files([o.tracking_number for o in sent], "a6")
    except ZRError as e:
        flash(str(e), "error")
        return back
    except Exception:
        current_app.logger.exception("orders_bulk_zr_labels: خطأ غير متوقع")
        flash("صار خطأ غير متوقع فجلب البوردرو من ZR. جرّب مرة أخرى.", "error")
        return back

    # ننزّلو الملفات بالتوازي، ونحافظو على ترتيب الاختيار
    def fetch(order):
        url = urls.get(order.tracking_number)
        if not url:
            return order, None
        try:
            return order, _download_label_pdf(url)
        except Exception:
            current_app.logger.warning("تعذّر تنزيل ملصق %s", order.tracking_number, exc_info=True)
            return order, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch, sent))

    writer = PdfWriter()
    missing = []
    for order, data in results:
        if not data:
            missing.append(order.order_number)
            continue
        try:
            for page in PdfReader(BytesIO(data)).pages:
                writer.add_page(page)
        except Exception:
            missing.append(order.order_number)

    if not len(writer.pages):
        flash("ما قدرناش نجيبو أي ملصق من ZR. جرّب «بوردرو بسيط».", "error")
        return back

    out = BytesIO()
    writer.write(out)

    # ⚠️ التنبيه يبان فالصفحة الجاية تاع الأدمن (التبويب الجديد PDF ما يعرضوش)
    notes = []
    if skipped:
        notes.append(f"{skipped} طلبية ما تبعثتش لـZR")
    if missing:
        notes.append("بلا ملصق: " + "، ".join(missing[:8]))
    if notes:
        flash(" · ".join(notes), "warning")

    return Response(
        out.getvalue(), mimetype="application/pdf",
        headers={"Content-Disposition": "inline; filename=zr-labels.pdf"},
    )


@admin_bp.route("/orders/bulk-delete", methods=["POST"])
@login_required
def orders_bulk_delete():
    """
    يحذف عدة طلبيات دفعة وحدة — مفيد لتنظيف طلبيات التجربة.

    ⚠️ نرجّعو المخزون لكل طلبية نشطة، نفس منطق الحذف الفردي.
    """
    ids = list(dict.fromkeys(request.form.getlist("order_ids", type=int)))

    if not ids:
        flash("ما اخترت حتى طلبية", "warning")
        return redirect(url_for("admin.orders_list"))

    orders = Order.query.filter(Order.id.in_(ids)).all()
    sent_count = 0

    for order in orders:
        if order.status != Order.STATUS_CANCELLED:
            restore_stock(order)
        if order.tracking_number:
            sent_count += 1
        for item in list(order.items):
            db.session.delete(item)
        db.session.delete(order)

    # ⚠️ نسجّلو أرقام الطلبيات برك — ماشي أسماء الزبائن ولا هواتفهم.
    # السجل لازم يجاوب «شكون مسح واش»، ماشي يولّي نسخة ثانية من
    # بيانات الزبائن.
    audit.record(
        AdminAction.BULK_DELETE, "order", None,
        f"{len(orders)} طلبية: " + ", ".join(o.order_number for o in orders[:15]),
    )

    db.session.commit()

    flash(f"تم حذف {len(orders)} طلبية", "success")
    if sent_count:
        flash(
            f"⚠️ {sent_count} منها كانت مبعوثة لـZR — "
            "الطرود مازالت عندهم، ألغيها يدويًا.",
            "warning",
        )

    return redirect(url_for("admin.orders_list"))


# ==================== ربط YouCan ====================

def _youcan_redirect_uri():
    """
    رابط الرجوع اللي يبعثو YouCan بعد الموافقة.

    ⚠️ لازم يطابق **بالحرف** اللي فـ«Allowed redirect URLs» عند YouCan.
    نبنيوه من الطلب الحالي باش يخدم على أي دومين بلا إعداد يدوي.
    """
    return url_for("admin.youcan_callback", _external=True, _scheme="https")


@admin_bp.route("/youcan")
@login_required
def youcan_settings():
    from services import youcan as yc

    return render_template(
        "admin/youcan.html",
        client_id=yc.get_client_id(),
        has_secret=bool(yc.get_client_secret()),
        connected=yc.is_connected(),
        redirect_uri=_youcan_redirect_uri(),
    )


@admin_bp.route("/youcan/save", methods=["POST"])
@login_required
def youcan_save():
    from services import youcan as yc

    yc.save_credentials(
        request.form.get("client_id"),
        request.form.get("client_secret"),
    )
    flash("تحفظو الاعتمادات", "success")
    return redirect(url_for("admin.youcan_settings"))


@admin_bp.route("/youcan/connect")
@login_required
def youcan_connect():
    """يوجّه الأدمن لصفحة الموافقة عند YouCan."""
    from services import youcan as yc
    import secrets

    try:
        # ⚠️ state عشوائي يتحفظ ونقارنوه فالرجوع — حماية من CSRF
        state = secrets.token_urlsafe(16)
        StoreSetting.set(yc.SETTING_STATE, state)
        db.session.commit()

        variant = request.args.get("variant", type=int) or 0
        url = yc.build_authorize_url(_youcan_redirect_uri(), state, variant)
    except yc.YouCanError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.youcan_settings"))

    return redirect(url)


@admin_bp.route("/youcan/callback")
@login_required
def youcan_callback():
    """يستقبل رد YouCan ويبدّل الـcode بتوكن."""
    from services import youcan as yc

    error = request.args.get("error_description") or request.args.get("error")
    if error:
        flash(f"YouCan رفض الربط: {error}", "error")
        return redirect(url_for("admin.youcan_settings"))

    code = request.args.get("code")
    if not code:
        flash("ما وصلناش code من YouCan", "error")
        return redirect(url_for("admin.youcan_settings"))

    # نتأكدو أن الرد جاي من نفس الطلب اللي بعثناه
    expected = StoreSetting.get(yc.SETTING_STATE)
    if expected and request.args.get("state") != expected:
        flash("state ماشي مطابق — توقّفنا (احتمال طلب مزوّر)", "error")
        return redirect(url_for("admin.youcan_settings"))

    try:
        yc.exchange_code(code, _youcan_redirect_uri())
    except yc.YouCanError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.youcan_settings"))

    flash("✅ تربط YouCan بنجاح — التوكن محفوظ", "success")
    return redirect(url_for("admin.youcan_settings"))


@admin_bp.route("/youcan/test", methods=["POST"])
@login_required
def youcan_test():
    from services import youcan as yc

    try:
        result = yc.test_token()

        flash(
            f"✅ الاتصال نجح — لقينا {result['count']} منتج "
            f"(عبر {result['pages']} صفحة)",
            "success",
        )

        # ⚠️ إذا الـAPI يتجاهل الترقيم، نقولوها صراحة بدل ما نعرضو
        # رقم ناقص ونخليو الأدمن يظن أن متجرو فيه 10 منتجات.
        if result["repeated"]:
            flash(
                "⚠️ YouCan رجّع نفس الصفحة — الترقيم ما يخدمش. "
                "العدد اللي فوق ماشي كامل.",
                "warning",
            )

        total = result["reported_total"]
        if total and total != result["count"]:
            flash(
                f"⚠️ YouCan يقول العدد {total} بينما جبنا {result['count']}. "
                "شوف سجلات الاستيراد.",
                "warning",
            )
    except yc.YouCanError as e:
        flash(f"🔴 {e}", "error")

    return redirect(url_for("admin.youcan_settings"))


@admin_bp.route("/youcan/disconnect", methods=["POST"])
@login_required
def youcan_disconnect():
    from services import youcan as yc

    yc.disconnect()
    flash("تفك الربط — التوكن تمسح", "success")
    return redirect(url_for("admin.youcan_settings"))


@admin_bp.route("/visitors")
@login_required
def visitors():
    """صفحة الزوار — كانت داخل «نظرة عامة»، ولات صفحة وحدها."""
    from services.analytics import get_summary, purge_scan_noise
    try:
        # ⚠️ ننظّفو زيارات روبوتات المسح فكل فتح للصفحة، ماشي فالإقلاع
        # وحدو. السبب: سجلات قديمة تسجّلت قبل ما نصلحو الفلتر، والأدمن
        # ما يستاهلش يعيد النشر باش يشوف أرقام صحيحة.
        purge_scan_noise()
        data = get_summary()
    except Exception:
        data = None   # ⚠️ الجدول ممكن ما يكونش موجود بعد أول نشر
    return render_template("admin/visitors.html", visitors=data)


@admin_bp.route("/visitors/reset", methods=["POST"])
@login_required
def visitors_reset():
    """يصفّر عدّاد الزوار — قبل الإطلاق ولا بعد تجارب."""
    from services.analytics import reset_all, purge_scan_noise, keep_current_week

    mode = request.form.get("mode")

    if mode == "all":
        removed = reset_all()
        flash(f"تصفّر العدّاد — مسحنا {removed} سطر", "success")
    elif mode == "week":
        # يخلّي غير الأسبوع الجاري (من السبت لليوم)
        removed, start = keep_current_week()
        flash(
            f"مسحنا {removed} سطر قديم — بقى الأسبوع الجاري برك "
            f"(من {start.strftime('%d/%m')})",
            "success",
        )
    else:
        removed = purge_scan_noise()
        if removed:
            flash(f"مسحنا {removed} زيارة من روبوتات المسح", "success")
        else:
            flash("ماكانش زيارات روبوتات — الأرقام نظيفة", "success")

    return redirect(url_for("admin.visitors"))


@admin_bp.route("/shipping/sync-zr", methods=["POST"])
@login_required
def shipping_sync_zr():
    """يحدّث أسعار ZR من جدول أسعارهم الرسمي."""
    from services.zrexpress import sync_shipping_rates, ZRError

    try:
        result = sync_shipping_rates()
    except ZRError as e:
        flash(f"🔴 {e}", "error")
        return redirect(url_for("admin.shipping_list"))

    if result["changed"]:
        flash(
            f"✅ تحدّثو {result['changed']} ولاية "
            f"({result['unchanged']} بلا تبديل، {result['from_zr']} وصلتنا من ZR)",
            "success",
        )
        # نعرضو أول التبديلات باش الأدمن يشوف واش صرا
        for change in result["details"][:12]:
            flash(
                f"{change['wilaya']}: منزل {change['old_home'] or '—'} → "
                f"{change['new_home'] or 'غير متوفر'} · "
                f"مكتب {change['old_office'] or '—'} → "
                f"{change['new_office'] or 'غير متوفر'}",
                "info",
            )
    else:
        flash(
            f"الأسعار مطابقة أصلاً — ماكانش تبديل "
            f"({result['unchanged']} ولاية)",
            "success",
        )

    if result["missing"]:
        flash(
            f"⚠️ {len(result['missing'])} ولاية ماكانتش فأسعار ZR "
            f"(خلّيناها كيما هي): {'، '.join(result['missing'][:8])}",
            "warning",
        )

    return redirect(url_for("admin.shipping_list"))


@admin_bp.route("/shipping/sync-file/<carrier_code>", methods=["POST"])
@login_required
def shipping_sync_file(carrier_code):
    """
    يحدّث أسعار World Express من جدولهم الرسمي المخزّن عندنا.

    ⚠️ الفرق على ZR: هنا ماكانش API — الجدول مخزّن فـ
    `data/world_rates.json`. الزر يقارن قاعدة البيانات بالجدول
    ويوري بالضبط واش تبدّل، بدل ما الأدمن يقلّب 58 خانة بالعين.
    """
    from services.carrier_offices import sync_rates_from_file

    try:
        result = sync_rates_from_file(carrier_code)
    except (ValueError, OSError) as e:
        flash(f"🔴 {e}", "error")
        return redirect(url_for("admin.shipping_list"))

    if result["changed"]:
        flash(
            f"✅ تحدّثو {result['changed']} ولاية "
            f"({result['unchanged']} بلا تبديل، {result['from_file']} فالجدول)",
            "success",
        )
        for change in result["details"][:12]:
            flash(
                f"{change['wilaya']}: منزل {change['old_home'] or '—'} → "
                f"{change['new_home'] or 'غير متوفر'} · "
                f"مكتب {change['old_office'] or '—'} → "
                f"{change['new_office'] or 'غير متوفر'}",
                "info",
            )
    else:
        flash(
            f"الأسعار مطابقة أصلاً — ماكانش تبديل ({result['unchanged']} ولاية)",
            "success",
        )

    if result["missing"]:
        flash(
            f"⚠️ {len(result['missing'])} ولاية ماكانتش فالجدول "
            f"(باقية غير متوفرة): {'، '.join(result['missing'][:8])}",
            "warning",
        )

    return redirect(url_for("admin.shipping_list"))


@admin_bp.route("/variants/<int:variant_id>/packs", methods=["POST"])
@login_required
def variant_packs(variant_id):
    """
    يحفظ العروض الجاهزة لموديل.

    ⚠️ نمسحو القديم ونكتبو الجديد — أبسط من محاولة مطابقة كل سطر
    بسطرو، والعروض قليلة فالعادة (2–6 لكل موديل).
    """
    variant = ProductVariant.query.get_or_404(variant_id)
    product = variant.product

    quantities = request.form.getlist("pack_quantity")
    labels = request.form.getlist("pack_label")
    prices = request.form.getlist("pack_price")

    new_packs = []

    for i in range(len(labels)):
        label = (labels[i] or "").strip()
        raw_qty = (quantities[i] if i < len(quantities) else "").strip()
        raw_price = (prices[i] if i < len(prices) else "").strip()

        # سطر فارغ كليًا = الأدمن خلّاه، نتجاوزوه بصمت
        if not label and not raw_price:
            continue

        if not label:
            flash("عرض بلا اسم — تجاوزناه", "warning")
            continue

        try:
            price = int(raw_price)
            quantity = int(raw_qty) if raw_qty else 1
        except ValueError:
            flash(f"«{label}»: السعر ولا العدد ماشي رقم — تجاوزناه", "warning")
            continue

        if price < 0 or quantity < 1:
            flash(f"«{label}»: العدد لازم 1 فأكثر والسعر ماشي سالب", "warning")
            continue

        new_packs.append((quantity, label[:160], price))

    # ⚠️ نمنعو تكرار الاسم — وإلا الزبون يشوف خيارين بنفس الاسم
    seen = set()
    unique = []
    for quantity, label, price in new_packs:
        key = label.lower()
        if key in seen:
            flash(f"«{label}» مكرر — خليّنا الأول برك", "warning")
            continue
        seen.add(key)
        unique.append((quantity, label, price))

    VariantPack.query.filter_by(variant_id=variant.id).delete(synchronize_session=False)

    for order, (quantity, label, price) in enumerate(unique):
        db.session.add(VariantPack(
            variant_id=variant.id,
            quantity=quantity,
            label=label,
            price=price,
            sort_order=order,
        ))

    db.session.commit()

    if unique:
        flash(f"تحفظو {len(unique)} عرض لـ«{variant.label}»", "success")
    else:
        flash(f"تمسحو العروض الجاهزة تاع «{variant.label}»", "success")

    return redirect(url_for("admin.product_form", product_id=product.id))


@admin_bp.route("/products/<int:product_id>/packs-bulk", methods=["POST"])
@login_required
def product_packs_bulk(product_id):
    """
    يضيف عرضًا جاهزًا لعدة موديلات ضربة وحدة.

    ⚠️ علاش هاد المسار؟
    منتج فيه 10 موديلات (ألوان) بنفس التغليف — «رولو (12 متر)» ينطبق
    عليهم كلهم. بلا هذا، الأدمن يعمّر نفس العرض 10 مرات.

    الوضعان:
      • كل الموديلات
      • موديلات مختارة (checkbox)

    السلوك عند التعارض: إذا الموديل عندو عرض بنفس الاسم، **نحدّثو**
    بدل ما نزيدو مكرر. هكذا الأدمن يقدر يبدّل سعر عرض على كل
    الموديلات بضغطة وحدة.
    """
    product = Product.query.get_or_404(product_id)

    label = (request.form.get("bulk_label") or "").strip()
    mode = request.form.get("bulk_mode") or "all"

    try:
        price = int(request.form.get("bulk_price") or 0)
        quantity = int(request.form.get("bulk_quantity") or 1)
    except ValueError:
        flash("السعر ولا العدد ماشي رقم", "error")
        return redirect(url_for("admin.product_form", product_id=product.id))

    if not label:
        flash("اكتب اسم العرض", "error")
        return redirect(url_for("admin.product_form", product_id=product.id))

    if price < 0 or quantity < 1:
        flash("العدد لازم 1 فأكثر والسعر ماشي سالب", "error")
        return redirect(url_for("admin.product_form", product_id=product.id))

    # ---------- نحدّدو الموديلات ----------
    if mode == "selected":
        chosen = request.form.getlist("bulk_variants", type=int)
        if not chosen:
            flash("ما اخترت حتى موديل", "warning")
            return redirect(url_for("admin.product_form", product_id=product.id))

        variants = [v for v in product.variants if v.id in chosen]
    else:
        variants = list(product.variants)

    if not variants:
        flash("هاد المنتج ماكانش فيه موديلات", "warning")
        return redirect(url_for("admin.product_form", product_id=product.id))

    added, updated = 0, 0

    for variant in variants:
        existing = next(
            (pk for pk in variant.packs if pk.label.strip().lower() == label.lower()),
            None,
        )

        if existing:
            existing.quantity = quantity
            existing.price = price
            updated += 1
        else:
            db.session.add(VariantPack(
                variant_id=variant.id,
                quantity=quantity,
                label=label[:160],
                price=price,
                sort_order=len(variant.packs),
            ))
            added += 1

    db.session.commit()

    parts = []
    if added:
        parts.append(f"زدنا العرض لـ{added} موديل")
    if updated:
        parts.append(f"حدّثناه فـ{updated}")

    flash(f"«{label}» — {' · '.join(parts)}", "success")
    return redirect(url_for("admin.product_form", product_id=product.id))


@admin_bp.route("/products/<int:product_id>/packs-bulk-delete", methods=["POST"])
@login_required
def product_packs_bulk_delete(product_id):
    """يمسح عرضًا بالاسم من كل الموديلات — للتراجع السريع."""
    product = Product.query.get_or_404(product_id)
    label = (request.form.get("bulk_label") or "").strip()

    if not label:
        flash("اكتب اسم العرض اللي تحب تمسحو", "error")
        return redirect(url_for("admin.product_form", product_id=product.id))

    removed = 0
    for variant in product.variants:
        for pack in list(variant.packs):
            if pack.label.strip().lower() == label.lower():
                db.session.delete(pack)
                removed += 1

    db.session.commit()

    if removed:
        flash(f"مسحنا «{label}» من {removed} موديل", "success")
    else:
        flash(f"ماكانش عرض بالاسم «{label}»", "warning")

    return redirect(url_for("admin.product_form", product_id=product.id))


def _bulk_target_variants(product, mode, field="bulk_variants"):
    """
    يحدّد الموديلات المستهدفة فالعمليات الجماعية.

    يرجع (قائمة الموديلات، رسالة خطأ) — الرسالة None إذا كلش مليح.
    """
    if mode == "selected":
        chosen = request.form.getlist(field, type=int)
        if not chosen:
            return [], "ما اخترت حتى موديل"
        return [v for v in product.variants if v.id in chosen], None

    return list(product.variants), None


@admin_bp.route("/products/<int:product_id>/tiers-bulk", methods=["POST"])
@login_required
def product_tiers_bulk(product_id):
    """
    يضيف مجال سعر (من/إلى) لعدة موديلات ضربة وحدة.

    ⚠️ نفس منطق العروض الجاهزة، لكن للتدرّج بالكمية. منتج فيه 10 ألوان
    غالبًا عندهم نفس سلّم الأسعار — الأدمن ما يعاودش يكتبو 10 مرات.

    السلوك عند التعارض: إذا الموديل عندو مجال بنفس «من»، **نحدّثوه**
    (إلى + السعر) بدل ما نزيدو مجالًا متداخلاً — التداخل يخلّي السعر
    غامضًا.
    """
    from models import VariantPriceTier

    product = Product.query.get_or_404(product_id)
    mode = request.form.get("bulk_mode") or "all"

    raw_min = (request.form.get("tier_bulk_min") or "").strip()
    raw_max = (request.form.get("tier_bulk_max") or "").strip()
    raw_price = (request.form.get("tier_bulk_price") or "").strip()

    try:
        min_q = int(raw_min)
        max_q = int(raw_max) if raw_max else None
        price = int(raw_price)
    except ValueError:
        flash("عمّر «من» والسعر بأرقام صحيحة", "error")
        return redirect(url_for("admin.product_form", product_id=product.id))

    if min_q < 1 or price < 0:
        flash("«من» لازم 1 فأكثر والسعر ماشي سالب", "error")
        return redirect(url_for("admin.product_form", product_id=product.id))

    if max_q is not None and max_q < min_q:
        flash("«إلى» أصغر من «من» — صحّحها", "error")
        return redirect(url_for("admin.product_form", product_id=product.id))

    variants, error = _bulk_target_variants(product, mode, "tier_bulk_variants")
    if error:
        flash(error, "warning")
        return redirect(url_for("admin.product_form", product_id=product.id))

    if not variants:
        flash("هاد المنتج ماكانش فيه موديلات", "warning")
        return redirect(url_for("admin.product_form", product_id=product.id))

    added, updated, skipped = 0, 0, []

    for variant in variants:
        same_start = next(
            (t for t in variant.price_tiers if t.min_quantity == min_q), None
        )

        if same_start:
            same_start.max_quantity = max_q
            same_start.unit_price = price
            updated += 1
            continue

        # ⚠️ نتحققو من التداخل مع المجالات الموجودة: مجالان يغطّيو نفس
        # الكمية = السعر يولّي غامض، والزبون يشوف رقمًا والحساب يعطي آخر.
        overlap = False
        for tier in variant.price_tiers:
            other_max = tier.max_quantity
            starts_before = tier.min_quantity <= min_q
            ends_after = other_max is None or other_max >= min_q

            if starts_before and ends_after:
                overlap = True
                break
            if max_q is None or tier.min_quantity <= max_q:
                if tier.min_quantity >= min_q:
                    overlap = True
                    break

        if overlap:
            skipped.append(variant.label)
            continue

        db.session.add(VariantPriceTier(
            variant_id=variant.id,
            min_quantity=min_q,
            max_quantity=max_q,
            unit_price=price,
        ))
        added += 1

    db.session.commit()

    parts = []
    if added:
        parts.append(f"زدناه لـ{added} موديل")
    if updated:
        parts.append(f"حدّثناه فـ{updated}")

    range_text = f"من {min_q} " + (f"إلى {max_q}" if max_q else "وفوق")
    flash(f"«{range_text} بـ{price} دج» — {' · '.join(parts) or 'ماكانش تبديل'}", "success")

    if skipped:
        flash(
            f"⚠️ تجاوزنا {len(skipped)} موديل — عندهم مجال متداخل: "
            f"{'، '.join(skipped[:6])}",
            "warning",
        )

    return redirect(url_for("admin.product_form", product_id=product.id))


@admin_bp.route("/products/<int:product_id>/tiers-bulk-delete", methods=["POST"])
@login_required
def product_tiers_bulk_delete(product_id):
    """يمسح مجالًا يبدا من «من» المحدّد، من كل الموديلات."""
    product = Product.query.get_or_404(product_id)

    try:
        min_q = int((request.form.get("tier_bulk_min") or "").strip())
    except ValueError:
        flash("اكتب «من» باش نعرفو أي مجال نمسحو", "error")
        return redirect(url_for("admin.product_form", product_id=product.id))

    removed = 0
    for variant in product.variants:
        for tier in list(variant.price_tiers):
            if tier.min_quantity == min_q:
                db.session.delete(tier)
                removed += 1

    db.session.commit()

    if removed:
        flash(f"مسحنا المجال «من {min_q}» من {removed} موديل", "success")
    else:
        flash(f"ماكانش مجال يبدا من {min_q}", "warning")

    return redirect(url_for("admin.product_form", product_id=product.id))


@admin_bp.route("/products/<int:product_id>/variant-price-bulk", methods=["POST"])
@login_required
def product_variant_price_bulk(product_id):
    """
    يبدّل السعر العادي لعدة موديلات ضربة وحدة.

    ⚠️ الفرق عن المسارات الأخرى: هنا نبدّلو **سعر الموديل نفسو**
    (ProductVariant.price)، ماشي مجال كمية ولا عرض جاهز. مفيد كي
    يتغيّر سعر المورّد على كل الألوان مرة وحدة.

    وضعان للقيمة:
      • مبلغ ثابت: كل الموديلات تولّي بهاد السعر
      • نسبة: نزيدو/نقصو نسبة من سعر كل موديل (±)
    """
    product = Product.query.get_or_404(product_id)
    mode = request.form.get("bulk_mode") or "all"
    kind = request.form.get("price_kind") or "fixed"

    raw = (request.form.get("price_value") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        flash("القيمة لازم رقم صحيح", "error")
        return redirect(url_for("admin.product_form", product_id=product.id))

    variants, error = _bulk_target_variants(product, mode, "price_bulk_variants")
    if error:
        flash(error, "warning")
        return redirect(url_for("admin.product_form", product_id=product.id))

    if not variants:
        flash("هاد المنتج ماكانش فيه موديلات", "warning")
        return redirect(url_for("admin.product_form", product_id=product.id))

    changed, skipped = 0, []

    for variant in variants:
        old = variant.effective_price

        if kind == "percent":
            # ⚠️ نحسبو من السعر الحالي تاع كل موديل — الموديلات ممكن
            # تكون بأسعار مختلفة، فالنسبة تنطبق على كل واحد بسعرو.
            new_price = round(old * (100 + value) / 100)
        else:
            new_price = value

        if new_price < 0:
            skipped.append(variant.label)
            continue

        variant.price = new_price
        changed += 1

    db.session.commit()

    if kind == "percent":
        sign = "+" if value >= 0 else ""
        flash(f"بدّلنا سعر {changed} موديل بنسبة {sign}{value}%", "success")
    else:
        flash(f"بدّلنا سعر {changed} موديل لـ{value:,} دج", "success")

    if skipped:
        flash(
            f"⚠️ تجاوزنا {len(skipped)} موديل — السعر يولّي سالب: "
            f"{'، '.join(skipped[:6])}",
            "warning",
        )

    return redirect(url_for("admin.product_form", product_id=product.id))


@admin_bp.route("/products/<int:product_id>/images/<int:image_id>/move", methods=["POST"])
@login_required
def product_image_move(product_id, image_id):
    """
    يحرّك صورة خطوة يمين ولا يسار فترتيب المعرض.

    ⚠️ علاش أزرار وماشي سحب؟ السحب (drag & drop) صعيب فالموبايل ويحتاج
    مكتبة. الأدمن يرتّب صور مرة وحدة عند إضافة المنتج، فأزرار بسيطة
    تخدم الغرض بلا تعقيد.
    """
    product = Product.query.get_or_404(product_id)
    image = ProductImage.query.filter_by(id=image_id, product_id=product.id).first_or_404()

    direction = request.form.get("dir")

    # ⚠️ كل نوع يتحرك داخل مجموعتو وحدو. لو خلطناهم، صورة توضيحية
    # تقدر تتبدّل مع صورة منتج ويتخربّط ترتيب الموديلات.
    group = [i for i in product.images if i.is_extra == image.is_extra]
    ordered = sorted(group, key=lambda i: (i.sort_order, i.id))

    # ⚠️ نعاودو ترقيم sort_order: البيانات القديمة ممكن كلها أصفار،
    # وبلا ترقيم نظيف التحريك ما يبانش.
    for position, img in enumerate(ordered):
        img.sort_order = position

    index = next((i for i, img in enumerate(ordered) if img.id == image.id), None)
    if index is None:
        flash("الصورة ماكانتش", "error")
        return redirect(url_for("admin.product_form", product_id=product.id))

    target = index - 1 if direction == "up" else index + 1

    if 0 <= target < len(ordered):
        other = ordered[target]
        image.sort_order, other.sort_order = other.sort_order, image.sort_order
        db.session.commit()
    else:
        db.session.commit()   # نحفظو الترقيم النظيف على أي حال

    return redirect(url_for("admin.product_form", product_id=product.id))


@admin_bp.route("/products/<int:product_id>/images/<int:image_id>/regroup", methods=["POST"])
@login_required
def product_image_regroup(product_id, image_id):
    """
    ينقّل صورة بين «صور المنتج» و«الصور التوضيحية».

    ⚠️ علاش هاد المسار موجود؟ كي ترفع صورة موديل بالغلط فخانة
    «توضيحية»، ماكانش طريقة تصلحها: أزرار ‹ › تحرّك الصورة داخل
    مجموعتها برك. الحل الوحيد كان تمسحها وتعاود ترفعها.

    عند النقل للصور الرئيسية، تقدر تربطها بموديل مباشرة (variant_id) —
    وإلا تبقى صورة معرض بلا موديل، وهذا مقبول.
    """
    product = Product.query.get_or_404(product_id)
    image = ProductImage.query.filter_by(
        id=image_id, product_id=product.id
    ).first_or_404()

    to_extra = request.form.get("to") == "extra"

    if image.is_extra == to_extra:
        flash("الصورة راهي ديجا فهاد المجموعة", "error")
        return redirect(url_for("admin.product_form", product_id=product.id))

    # ---------- التوضيحية ما تتربطش بموديل ----------
    if to_extra:
        # ⚠️ الموديلات تشير للصورة بالاسم. لو خلّيناها مربوطة وهي
        # توضيحية، الموديل يبقى يشير لصورة برّا المعرض — الزبون
        # يختار الموديل وما تتبدّلش الصورة.
        cleared = (
            ProductVariant.query
            .filter_by(product_id=product.id, image_filename=image.image_filename)
            .update({"image_filename": None}, synchronize_session=False)
        )

        # وإذا كانت هي الصورة الرئيسية تاع المنتج، نرقّيو وحدة أخرى
        if product.image_filename == image.image_filename:
            replacement = next(
                (i for i in sorted(product.main_images, key=lambda x: (x.sort_order, x.id))
                 if i.id != image.id),
                None,
            )
            product.image_filename = replacement.image_filename if replacement else None
    else:
        cleared = 0
        image.caption = None   # الوصف خاص بالتوضيحية برك

        variant_id = request.form.get("variant_id", type=int)
        if variant_id:
            variant = ProductVariant.query.filter_by(
                id=variant_id, product_id=product.id
            ).first()
            if variant:
                variant.image_filename = image.image_filename

        # أول صورة منتج؟ تولّي هي الرئيسية
        if not product.image_filename:
            product.image_filename = image.image_filename

    image.is_extra = to_extra

    # ⚠️ نحطّوها فآخر مجموعتها الجديدة بترقيم نظيف: البيانات القديمة
    # ممكن كلها أصفار، وبلا ترقيم الصورة تقفز لبلاصة عشوائية.
    group = sorted(
        [i for i in product.images if i.is_extra == to_extra and i.id != image.id],
        key=lambda i: (i.sort_order, i.id),
    )
    for position, img in enumerate(group):
        img.sort_order = position
    image.sort_order = len(group)

    db.session.commit()

    where = "الصور التوضيحية" if to_extra else "صور المنتج"
    if cleared:
        flash(f"اتنقّلت لـ{where} — واتحيّدت من {cleared} موديل", "success")
    else:
        flash(f"اتنقّلت لـ{where}", "success")

    return redirect(url_for("admin.product_form", product_id=product.id))


@admin_bp.route("/variants/<int:variant_id>/price", methods=["POST"])
@login_required
def variant_price(variant_id):
    """
    يبدّل سعر موديل واحد — تعديل سريع من رأس البطاقة.

    ⚠️ علاش مسار مستقل؟ الفورم الرئيسي تاع المنتج طويل ويحفظ كلش.
    تبديل سعر موديل واحد ما يستاهلش حفظ الصفحة كاملة.

    القيمة الفارغة = الموديل يرث سعر المنتج (ماشي صفر).
    """
    variant = ProductVariant.query.get_or_404(variant_id)
    product = variant.product

    raw = (request.form.get("price") or "").strip()
    is_ajax = request.headers.get("X-Requested-With") == "fetch"

    def reply(ok, message, category="success"):
        if is_ajax:
            return jsonify({
                "ok": ok,
                "message": message,
                "price": variant.effective_price,
                "display": variant.price_display,
                "inherited": variant.price is None,
            }), (200 if ok else 400)
        flash(message, category)
        return redirect(url_for("admin.product_form", product_id=product.id))

    # فارغ = يرث سعر المنتج
    if not raw:
        variant.price = None
        db.session.commit()
        return reply(True, f"«{variant.label}» ولّى يرث سعر المنتج")

    try:
        price = int(raw)
    except ValueError:
        return reply(False, "السعر لازم رقم صحيح", "error")

    if price < 0:
        return reply(False, "السعر ماشي سالب", "error")

    variant.price = price
    db.session.commit()
    return reply(True, f"«{variant.label}» ولّى بـ{price:,} دج")


@admin_bp.route("/variants/<int:variant_id>/available", methods=["POST"])
@login_required
def variant_toggle_available(variant_id):
    """
    يبدّل توفّر موديل (متوفر ↔ غير متوفر).

    ⚠️ الموديل غير المتوفر يبقى موجودًا بأسعارو وعروضو — يبان للزبون
    مشطوبًا وما يقدرش يختارو. أحسن من الحذف: كي يرجع من عند المورّد،
    ضغطة وحدة ويولّي متوفرًا.
    """
    variant = ProductVariant.query.get_or_404(variant_id)
    product = variant.product

    variant.is_available = not variant.is_available

    # ⚠️ إذا عطّلنا الافتراضي، لازم موديل آخر يولّي هو الافتراضي —
    # وإلا صفحة المنتج تفتح على موديل ما يتباعش.
    moved = None
    if not variant.is_available and variant == product.default_variant:
        for other in product.ordered_variants:
            if other.id != variant.id and other.is_available:
                product.image_filename = other.image_filename or product.image_filename
                other.is_default = True
                variant.is_default = False
                moved = other.label
                break

    db.session.commit()

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, "is_available": variant.is_available,
                        "moved": moved})

    state = "متوفر" if variant.is_available else "غير متوفر"
    msg = f"«{variant.label}» ولّى {state}"
    if moved:
        msg += f" — والافتراضي ولّى «{moved}»"
    flash(msg, "success")
    return redirect(url_for("admin.product_form", product_id=product.id))


@admin_bp.route("/variants/<int:variant_id>/label", methods=["POST"])
@login_required
def variant_label(variant_id):
    """
    يبدّل اسم موديل واحد — تعديل سريع فمكانو، كيما السعر.

    ⚠️ قبل، تبديل اسم موديل كان مستحيلاً: لازم تمسحو وتعاود تزيدو،
    وتخسر معاه أسعار الكمية والعروض الجاهزة كاملين.
    """
    variant = ProductVariant.query.get_or_404(variant_id)
    product = variant.product

    label = (request.form.get("label") or "").strip()
    is_ajax = request.headers.get("X-Requested-With") == "fetch"

    def reply(ok, message, category="success"):
        if is_ajax:
            return jsonify({"ok": ok, "message": message,
                            "label": variant.label}), (200 if ok else 400)
        flash(message, category)
        return redirect(url_for("admin.product_form", product_id=product.id))

    if not label:
        return reply(False, "اسم الموديل ما يقدرش يكون فارغ", "error")

    if len(label) > 160:
        return reply(False, "الاسم طويل بزاف (160 حرف كحد أقصى)", "error")

    # ⚠️ اسمان متشابهان فنفس المنتج = الزبون يحتار
    twin = ProductVariant.query.filter(
        ProductVariant.product_id == product.id,
        ProductVariant.id != variant.id,
        ProductVariant.label == label,
    ).first()
    if twin:
        return reply(False, f"«{label}» موجود من قبل فهاد المنتج", "error")

    variant.label = label
    db.session.commit()
    return reply(True, f"الاسم ولّى «{label}»")


@admin_bp.route("/products/export")
@login_required
def products_export():
    """يحمّل كل المنتجات كملف Excel."""
    from services.excel_io import export_workbook
    from flask import send_file
    from datetime import datetime

    stream = export_workbook()
    name = f"budac-products-{datetime.now().strftime('%Y-%m-%d')}.xlsx"

    return send_file(
        stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=name,
    )


# حد حجم طلب الاستيراد (Excel + ZIP الصور). الحد العام MAX_CONTENT_LENGTH = 30 ميغا.
IMPORT_MAX_BYTES = 80 * 1024 * 1024


@admin_bp.route("/products/import", methods=["POST"])
@login_required
def products_import():
    """
    يستورد المنتجات من ملف Excel.

    ⚠️ ما نمسحوش حتى منتج: الملف يحدّث الموجود ويزيد الجديد فقط.
    الحذف يبقى يدويًا من اللوحة — عملية خطيرة ما تستاهلش تصرا
    بالغلط من ملف.
    """
    from services.excel_io import import_workbook
    import zipfile

    # ⚠️ الحد العام (30 ميغا) صغير على Excel + صور. نرفعوه لهاد المسار برك
    # (ماشي لكل الموقع) — لازم يتحط قبل ما نقراو request.files.
    request.max_content_length = IMPORT_MAX_BYTES

    uploaded = request.files.get("excel_file")

    if not uploaded or not uploaded.filename:
        flash("اختر ملف Excel أولاً", "error")
        return redirect(url_for("admin.products_list"))

    if not uploaded.filename.lower().endswith((".xlsx", ".xlsm")):
        flash("الملف لازم يكون .xlsx", "error")
        return redirect(url_for("admin.products_list"))

    # ملف الصور (اختياري): ZIP برك. RAR ما نقدروش نقراوه (يحتاج برنامج خارجي).
    images_zip = request.files.get("images_zip")
    if images_zip and images_zip.filename:
        name = images_zip.filename.lower()
        if name.endswith((".rar", ".7z")):
            flash("ملف RAR/7z ما نقراوهش — حوّلو لـZIP (كليك يمين ← Send to ← "
                  "Compressed folder، ولا WinRAR ← صيغة الأرشيف ZIP).", "error")
            return redirect(url_for("admin.products_list"))
        if not zipfile.is_zipfile(images_zip.stream):
            flash("ملف الصور ماشي ZIP صحيح", "error")
            return redirect(url_for("admin.products_list"))
        images_zip.stream.seek(0)
    else:
        images_zip = None

    try:
        report = import_workbook(uploaded, images_zip=images_zip.stream if images_zip else None)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.products_list"))
    except Exception:
        db.session.rollback()
        # ⚠️ رسالة SQLAlchemy فيها جملة SQL كاملة بأسماء الجداول
        # والقيم. حتى للأدمن، عرضها فالمتصفح ماشي فكرة مليحة —
        # تنسخ فسكرينشوت وتتبعث فمحادثة دعم. التفاصيل للسجلات.
        current_app.logger.exception("فشل استيراد ملف Excel")
        flash(
            "فشل الاستيراد. تأكد أن الملف مبني على ملف التصدير، "
            "وشوف السجلات للتفاصيل.",
            "error",
        )
        return redirect(url_for("admin.products_list"))

    parts = []
    if report["products_new"]:
        parts.append(f"{report['products_new']} منتج جديد")
    if report["products_updated"]:
        parts.append(f"{report['products_updated']} محدّث")
    if report["variants_new"]:
        parts.append(f"{report['variants_new']} موديل جديد")
    if report["variants_updated"]:
        parts.append(f"{report['variants_updated']} موديل محدّث")
    if report["tiers"]:
        parts.append(f"{report['tiers']} سعر كمية")
    if report["packs"]:
        parts.append(f"{report['packs']} عرض جاهز")
    if report["images_added"]:
        parts.append(f"{report['images_added']} صورة")
    if report["images_existing"]:
        parts.append(f"{report['images_existing']} صورة كانت موجودة")
    if report["variants_linked"]:
        parts.append(f"{report['variants_linked']} موديل رُبط بصورتو")

    flash("✅ " + (" · ".join(parts) if parts else "ماكانش تبديل"), "success")

    for warning in report["warnings"][:12]:
        flash(f"⚠️ {warning}", "warning")

    if len(report["warnings"]) > 12:
        flash(f"⚠️ و{len(report['warnings']) - 12} تحذير آخر", "warning")

    return redirect(url_for("admin.products_list"))


# ══════════════════════════════════════════════════════════════
# v181 — «واتساب التلقائي»: حالة الربط، تفعيل كل نوع، سجل الرسائل
# ══════════════════════════════════════════════════════════════

@admin_bp.route("/whatsapp-auto", methods=["GET", "POST"])
@login_required
def wa_auto_page():
    from models import StoreSetting, WhatsAppOutbox
    from services import whatsapp_api as api

    if request.method == "POST":
        StoreSetting.set("wa_auto_master", "1" if request.form.get("master") else "0")
        for kind in api.KINDS:
            api.set_kind_enabled(kind, bool(request.form.get(f"kind_{kind}")))
        audit.record(AdminAction.SETTINGS_CHANGE, "wa_auto", "settings", "تعديل واتساب التلقائي")
        db.session.commit()
        flash("تحفظت إعدادات الواتساب التلقائي", "success")
        return redirect(url_for("admin.wa_auto_page"))

    rows = (WhatsAppOutbox.query.order_by(WhatsAppOutbox.id.desc()).limit(60).all())
    counts = dict(db.session.query(WhatsAppOutbox.status, func.count())
                  .group_by(WhatsAppOutbox.status).all())
    kinds = [{"key": k, "label": spec["label"], "enabled": api.kind_enabled(k)}
             for k, spec in api.KINDS.items()]
    return render_template(
        "admin/wa_auto.html",
        configured=api.is_configured(),
        master=StoreSetting.get("wa_auto_master", "1") == "1",
        kinds=kinds, rows=rows, counts=counts,
        webhook_ready=bool(os.environ.get("WA_VERIFY_TOKEN") and os.environ.get("WA_APP_SECRET")),
        sync_minutes=int(os.environ.get("TRACKING_SYNC_MINUTES", "20")),
    )


@admin_bp.route("/whatsapp-auto/<int:row_id>/retry", methods=["POST"])
@login_required
def wa_auto_retry(row_id):
    from models import WhatsAppOutbox
    from services import whatsapp_api as api

    row = db.session.get(WhatsAppOutbox, row_id) or abort(404)
    api.retry(row)
    db.session.commit()
    flash("رجعت الرسالة للطابور، تتبعث فدقائق", "success")
    return redirect(url_for("admin.wa_auto_page"))


# ══════════════════════════════════════════════════════════════
# v185 — لوحة تحكم الصفحة الرئيسية (ترتيب/إظهار الأقسام + خيارات)
# ══════════════════════════════════════════════════════════════

@admin_bp.route("/homepage", methods=["GET", "POST"])
@login_required
def homepage_settings():
    from services import homepage

    if request.method == "POST":
        if request.form.get("action") == "reset":
            homepage.reset_layout()
            audit.record(AdminAction.SETTINGS_CHANGE, "homepage", "settings", "إعادة ضبط الصفحة الرئيسية")
            db.session.commit()
            flash("رجعت الصفحة الرئيسية للترتيب الافتراضي", "success")
        else:
            homepage.save_layout_from_form(request.form)
            audit.record(AdminAction.SETTINGS_CHANGE, "homepage", "settings", "تعديل ترتيب الصفحة الرئيسية")
            db.session.commit()
            flash("تحفظت الصفحة الرئيسية", "success")
        return redirect(url_for("admin.homepage_settings"))

    specs = {s["key"]: s for s in homepage.SECTIONS}
    rows = [{**item, "spec": specs[item["key"]]} for item in homepage.get_layout()]
    return render_template("admin/homepage.html", rows=rows,
                           count_min=homepage.COUNT_MIN, count_max=homepage.COUNT_MAX)
