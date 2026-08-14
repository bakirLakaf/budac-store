"""
لوحة تحكم الأدمن — كاملة.

الأمان:
- كل الصفحات محمية بـ@login_required ما عدا صفحة الدخول
- كلمة السر مهشّرة (hash)، أبدًا نص صريح
- رفع الصور يمر على فحص أمني (services/uploads.py)
- كل عمليات التعديل/الحذف عبر POST فقط (ما يتنفذوش برابط GET عادي)
"""

from datetime import datetime
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, abort,
)
from sqlalchemy import func

import os

from models import (
    db, Product, Category, ProductImage, ProductVariant,
    Offer, OfferItem, Carrier, ShippingRate, AdminUser, Coupon, Order,
    AbandonedCart, Review, UpsellRule, LandingPage,
)
from services.cart import CartLine
from services.orders import restore_stock, reserve_stock, OrderError
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
            "pending_abandoned": AbandonedCart.query.filter_by(
                status=AbandonedCart.STATUS_NEW
            ).count(),
            "pending_reviews": Review.query.filter_by(is_approved=False).count(),
        }
    except Exception:
        return {}  # قاعدة البيانات مازال ما اتعمّرتش


# ==================== المصادقة ====================

def login_required(view):
    """يحمي أي صفحة: إذا ماشي داخل، يوجهو لصفحة الدخول."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            flash("خاصك تسجل الدخول باش تدخل للوحة التحكم", "warning")
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
    from time import time

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
    from time import time

    count, first_try = _LOGIN_ATTEMPTS.get(ip, (0, time()))
    _LOGIN_ATTEMPTS[ip] = (count + 1, first_try)

    # تنظيف: ما نخليوش القاموس يكبر بلا حدود
    if len(_LOGIN_ATTEMPTS) > 500:
        now = time()
        for key in [k for k, v in _LOGIN_ATTEMPTS.items() if now - v[1] > LOCKOUT_SECONDS]:
            _LOGIN_ATTEMPTS.pop(key, None)


@admin_bp.route("/login", methods=["GET", "POST"])
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
            session["admin_id"] = user.id
            session["admin_username"] = user.username
            user.last_login_at = datetime.utcnow()
            db.session.commit()

            _LOGIN_ATTEMPTS.pop(ip, None)  # دخول ناجح → نصفّرو العداد

            next_page = request.args.get("next")
            # نتأكدو أن الوجهة داخلية فقط (حماية من open redirect)
            if next_page and next_page.startswith("/"):
                return redirect(next_page)
            return redirect(url_for("admin.dashboard"))

        _record_failed_login(ip)
        flash("اسم المستخدم أو كلمة السر غير صحيحة", "error")

    return render_template("admin/login.html")


@admin_bp.route("/logout", methods=["POST"])
def logout():
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
        Order.status.notin_([Order.STATUS_DELIVERED, Order.STATUS_CANCELLED])
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
        uncategorized=uncategorized,
        stock_value=stock_value,
    )


# ==================== المنتجات ====================

@admin_bp.route("/products")
@login_required
def products_list():
    """قائمة المنتجات مع بحث وفلترة."""

    search = (request.args.get("q") or "").strip()
    category_id = request.args.get("category", type=int)
    stock_filter = request.args.get("stock")

    query = Product.query

    if search:
        query = query.filter(Product.name.contains(search))
    if category_id:
        query = query.filter_by(category_id=category_id)
    if stock_filter == "out":
        query = query.filter_by(stock_quantity=0)
    elif stock_filter == "low":
        query = query.filter(Product.stock_quantity > 0, Product.stock_quantity <= 5)

    products = query.order_by(Product.created_at.desc()).all()
    categories = Category.query.order_by(Category.name).all()

    return render_template(
        "admin/products_list.html",
        products=products,
        categories=categories,
        search=search,
        active_category=category_id,
        stock_filter=stock_filter,
    )


@admin_bp.route("/products/new", methods=["GET", "POST"])
@admin_bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def product_form(product_id=None):
    """
    نموذج واحد للإضافة والتعديل — أبسط من ملفين منفصلين فيهم نفس الكود مكرر.
    """
    product = Product.query.get_or_404(product_id) if product_id else None
    categories = Category.query.order_by(Category.name).all()

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
        product.stock_quantity = request.form.get("stock_quantity", type=int) or 0
        product.category_id = request.form.get("category_id", type=int) or None
        product.is_active = bool(request.form.get("is_active"))
        product.is_featured = bool(request.form.get("is_featured"))

        # إعدادات حاسبة الكمية
        calc_type = request.form.get("calculator_type") or None
        product.calculator_type = calc_type if calc_type in ("perimeter", "area") else None
        product.roll_length_m = request.form.get("roll_length_m", type=float) or None
        product.roll_width_m = request.form.get("roll_width_m", type=float) or None
        product.calculator_margin = request.form.get("calculator_margin", type=int) or 10

        # الوصف: نقبل HTML ونخزّن النسختين (الأصلية + نص واضح للعرض)
        raw_description = request.form.get("description") or ""
        product.description_html = raw_description
        product.description = html_to_text(raw_description)

        db.session.flush()  # باش ناخذو product.id إذا كان جديد

        # رفع صور جديدة (متعددة)
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
        flash(f"تم حفظ المنتج «{product.name}»", "success")
        return redirect(url_for("admin.product_form", product_id=product.id))

    return render_template(
        "admin/product_form.html", product=product, categories=categories
    )


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

    for img in product.images:
        delete_image(img.image_filename, "products")

    name = product.name
    db.session.delete(product)
    db.session.commit()
    flash(f"تم حذف «{name}»", "success")
    return redirect(url_for("admin.products_list"))


@admin_bp.route("/products/<int:product_id>/images/<int:image_id>/make-main", methods=["POST"])
@login_required
def product_image_make_main(product_id, image_id):
    """
    يخلي صورة معينة هي الرئيسية (اللي تبان فبطاقة المنتج وفالسلة).

    ⚠️ نتأكدو أن الصورة تخص هاد المنتج بالضبط — باش حد ما يقدرش يبعث id
    ديال صورة منتج آخر ويخلط الصور بين المنتجات.
    """
    image = ProductImage.query.filter_by(id=image_id, product_id=product_id).first_or_404()

    image.product.image_filename = image.image_filename
    db.session.commit()

    flash("تم تعيين الصورة الرئيسية", "success")
    return redirect(url_for("admin.product_form", product_id=product_id))


@admin_bp.route("/products/<int:product_id>/images/<int:image_id>/delete", methods=["POST"])
@login_required
def product_image_delete(product_id, image_id):
    image = ProductImage.query.filter_by(id=image_id, product_id=product_id).first_or_404()
    product = image.product

    delete_image(image.image_filename, "products")
    was_main = product.image_filename == image.image_filename
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

    db.session.commit()
    flash("تم حذف الصورة", "success")
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

    ⚠️ ليش ماشي رفع جديد؟ الصور نفسها: الأدمن يرفع صور المنتج مرة وحدة،
    وبعدها يقول "هاد الموديل = هاد الصورة". بلا تكرار على القرص، وبلا
    ما يعاود يرفع نفس الصورة لكل موديل.
    """
    variant = ProductVariant.query.get_or_404(variant_id)
    product = variant.product

    filename = (request.form.get("image_filename") or "").strip()

    # فراغ = نحيّدو الربط
    if not filename:
        variant.image_filename = None
        db.session.commit()
        flash(f"تحيّدت صورة «{variant.label}»", "success")
        return redirect(url_for("admin.product_form", product_id=product.id))

    # نتحققو أن الصورة فعلاً من صور هاد المنتج (أمان: ما نقبلوش أي اسم)
    valid = any(img.image_filename == filename for img in product.images)
    if not valid and filename != product.image_filename:
        flash("هاد الصورة ماشي من صور هاد المنتج", "error")
        return redirect(url_for("admin.product_form", product_id=product.id))

    variant.image_filename = filename
    db.session.commit()
    flash(f"تربطات صورة «{variant.label}»", "success")
    return redirect(url_for("admin.product_form", product_id=product.id))


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

    if variant.image_filename:
        delete_image(variant.image_filename, "products")

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
                "admin/offer_form.html", offer=offer, all_products=all_products
            )

        slug = (request.form.get("slug") or "").strip() or slugify(title)

        clash = Offer.query.filter(Offer.slug == slug)
        if offer:
            clash = clash.filter(Offer.id != offer.id)
        if clash.first():
            flash(f"الرابط '{slug}' مستعمل عند عرض آخر", "error")
            return render_template(
                "admin/offer_form.html", offer=offer, all_products=all_products
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

        # التواريخ اختيارية
        offer.starts_at = _parse_date(request.form.get("starts_at"))
        offer.ends_at = _parse_date(request.form.get("ends_at"))

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
        "admin/offer_form.html", offer=offer, all_products=all_products
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

    # نعرضو كل الشركات (حتى المعطّلة) — الأدمن خاصو يشوفهم باش يفعّلهم
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

def _parse_date(value):
    """يحول نص تاريخ من الفورم لـdatetime. يرجع None إذا فارغ أو غير صالح."""
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        return None


# ==================== الطلبيات ====================

@admin_bp.route("/orders")
@login_required
def orders_list():
    """قائمة الطلبيات مع فلترة بالحالة والبحث."""
    status = request.args.get("status")
    search = (request.args.get("q") or "").strip()

    query = Order.query

    if status and status in Order.STATUS_LABELS:
        query = query.filter_by(status=status)

    if search:
        query = query.filter(
            db.or_(
                Order.order_number.contains(search.upper()),
                Order.customer_phone.contains(search),
                Order.customer_name.contains(search),
            )
        )

    orders = query.order_by(Order.created_at.desc()).limit(200).all()

    # عدّاد لكل حالة — باش الأدمن يشوف بسرعة شحال عندو جديد
    counts = dict(
        db.session.query(Order.status, func.count(Order.id))
        .group_by(Order.status)
        .all()
    )

    return render_template(
        "admin/orders_list.html",
        orders=orders, counts=counts, current_status=status,
        search=search, status_labels=Order.STATUS_LABELS,
    )


@admin_bp.route("/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template(
        "admin/order_detail.html", order=order, status_labels=Order.STATUS_LABELS
    )


@admin_bp.route("/orders/<int:order_id>/status", methods=["POST"])
@login_required
def order_update_status(order_id):
    """
    تبديل حالة الطلبية.

    ⚠️ منطق مهم: كي نلغيو طلبية، نرجّعو المخزون تلقائيًا.
    وكي نلغيو الإلغاء (نرجعوها لحالة نشطة)، نخصمو المخزون من جديد.
    بلا هذا، المخزون يولي غلط بصمت.
    """

    order = Order.query.get_or_404(order_id)
    new_status = request.form.get("status")

    if new_status not in Order.STATUS_LABELS:
        flash("حالة غير معروفة", "error")
        return redirect(url_for("admin.order_detail", order_id=order.id))

    old_status = order.status

    # إلغاء طلبية كانت نشطة → نرجّعو المخزون
    if new_status == Order.STATUS_CANCELLED and old_status != Order.STATUS_CANCELLED:
        restore_stock(order)
        flash("تم إرجاع المخزون للمنتجات", "success")

    # إرجاع طلبية ملغاة للنشاط → نعاودو نخصمو المخزون
    elif old_status == Order.STATUS_CANCELLED and new_status != Order.STATUS_CANCELLED:
        lines = [
            CartLine(
                key="", kind="offer" if item.offer_id else "product",
                name=item.item_name, unit_price=item.unit_price,
                quantity=item.quantity, product_id=item.product_id,
                variant_id=item.variant_id, offer_id=item.offer_id,
            )
            for item in order.items
        ]
        try:
            reserve_stock(lines)
        except OrderError as e:
            db.session.rollback()
            flash(f"ما قدرناش نرجعو الطلبية: {e}", "error")
            return redirect(url_for("admin.order_detail", order_id=order.id))

        flash("تم خصم المخزون من جديد", "success")

    order.status = new_status

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
            flash("النسبة خاصها تكون بين 1 و100", "error")
            return redirect(url_for("admin.coupons_list"))

        if discount_type == Coupon.TYPE_FIXED and value <= 0:
            flash("مبلغ الخصم خاصو يكون أكبر من صفر", "error")
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

    # القيمة الضائعة المحتملة — يوري للأدمن شحال يستاهل الوقت
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

    reviews = query.order_by(Review.created_at.desc()).limit(200).all()

    pending_count = Review.query.filter_by(is_approved=False).count()
    approved_count = Review.query.filter_by(is_approved=True).count()

    return render_template(
        "admin/reviews.html",
        reviews=reviews, show=show,
        pending_count=pending_count, approved_count=approved_count,
    )


@admin_bp.route("/reviews/<int:review_id>/approve", methods=["POST"])
@login_required
def review_approve(review_id):
    review = db.session.get(Review, review_id) or abort(404)
    review.is_approved = True
    db.session.commit()
    flash("تمت الموافقة — التقييم يبان دابا فالمتجر", "success")
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

@admin_bp.route("/orders/<int:order_id>/send-to-carrier", methods=["POST"])
@login_required
def order_send_to_carrier(order_id):
    """
    يبعث الطلبية لـZR Express ويسجّل رقم التتبّع.

    ⚠️ الفشل ما يكسرش الطلبية: إذا ZR ما ردّش، الطلبية تبقى كيما هي والأدمن
    يقدر يبعثها يدويًا أو يعاود يجرّب. ما نخسروش بيعة بسبب مشكل شبكة.
    """
    from services.zrexpress import create_parcel, ZRError, lookup_territory_id, is_configured

    order = db.session.get(Order, order_id) or abort(404)

    if not is_configured():
        flash(
            "مفاتيح ZR Express غير معمّرة. حط ZR_TENANT_ID و ZR_API_KEY فملف .env",
            "error",
        )
        return redirect(url_for("admin.order_detail", order_id=order.id))

    if order.tracking_number:
        flash(f"هاد الطلبية تبعثات من قبل — رقم التتبّع: {order.tracking_number}", "warning")
        return redirect(url_for("admin.order_detail", order_id=order.id))

    # ⚠️ معرّف البلدية عند ZR (territoryId) يسهّل الأمور، لكن **ماشي إجباري**:
    # ZR يقبل الطرد بالاسم كذلك. قبل، كنّا نمنعو الإرسال بلا معرّف — وهذا
    # كان يحبس طلبيات صحيحة بلا سبب. دابا نبعثو ونخلّيو ZR يقرّر.
    territory_id = order.commune_external_id or None

    # ⚠️ ZR يحدّد الوجهة بـterritoryId، ماشي بأسماء الولاية والبلدية.
    # بلاه يرد بـ400 بلا رسالة. إذا الطلبية ما عندهاش معرّف محفوظ
    # (قديمة، ولا الزبون كتب البلدية بيدو)، ندوّرو عليه دابا ونحفظوه.
    if not territory_id and order.delivery_type != "office" and order.commune:
        try:
            territory_id = lookup_territory_id(order.wilaya_code, order.commune)
            if territory_id:
                order.commune_external_id = territory_id
                db.session.commit()
            else:
                # ⚠️ ما نبلعوش الفشل بصمت — الأدمن لازم يعرف
                flash(
                    f"ما لقيناش معرّف البلدية «{order.commune}» عند ZR. "
                    "اضغط «افحص ليش ما لقيناش المعرّف» فصفحة التشخيص.",
                    "warning",
                )
        except Exception as e:
            flash(f"فشل البحث عن معرّف البلدية: {e}", "warning")

    try:
        result = create_parcel(order, territory_id=territory_id)
    except ZRError as e:
        flash(f"ما قدرناش نبعثو الطلبية: {e}", "error")
        return redirect(url_for("admin.order_detail", order_id=order.id))

    order.tracking_number = result["tracking_number"]
    order.carrier_parcel_id = result.get("parcel_id")

    # نرقّيو الحالة تلقائيًا — الطلبية ولّات فعلاً عند الشركة
    if order.status == Order.STATUS_PENDING:
        order.status = Order.STATUS_CONFIRMED

    db.session.commit()

    flash(f"✓ تبعثات لـZR Express — رقم التتبّع: {order.tracking_number}", "success")
    return redirect(url_for("admin.order_detail", order_id=order.id))


@admin_bp.route("/orders/<int:order_id>/refresh-tracking", methods=["POST"])
@login_required
def order_refresh_tracking(order_id):
    """يحدّث حالة الطرد من ZR Express."""
    from services.zrexpress import get_parcel_status, ZRError

    order = db.session.get(Order, order_id) or abort(404)

    if not order.tracking_number:
        flash("هاد الطلبية ما تبعثاتش لشركة التوصيل بعد", "warning")
        return redirect(url_for("admin.order_detail", order_id=order.id))

    try:
        status = get_parcel_status(order.tracking_number)
    except ZRError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.order_detail", order_id=order.id))

    order.carrier_state = status["state"]
    db.session.commit()

    flash(f"الحالة عند الشركة: {status['state']}", "success")
    return redirect(url_for("admin.order_detail", order_id=order.id))


@admin_bp.route("/integrations", methods=["GET", "POST"])
@login_required
def integrations():
    """صفحة التكاملات — اختبار الاتصال بشركات التوصيل."""
    from services.zrexpress import test_connection, is_configured

    from services.zrexpress import sync_communes, ZRError
    from models import Carrier, Commune, CarrierCommune

    zr_result = None
    sync_result = None
    inspect_data = None

    if request.method == "POST":
        action = request.form.get("test") or request.form.get("action")

        if action == "zr":
            ok, message = test_connection()
            zr_result = {"ok": ok, "message": message}

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

    # كم بلدية محفوظة دابا — نعرضوها للأدمن باش يعرف واش لازم يزامن
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
        "cta_text": (data.get("cta_text") or "").strip() or "اطلب الآن",
        "campaign_price": data.get("campaign_price", type=int) or None,
        "compare_at_price": data.get("compare_at_price", type=int) or None,
        "show_countdown": bool(data.get("show_countdown")),
        "countdown_hours": data.get("countdown_hours", type=int) or 24,
        "stock_warning": (data.get("stock_warning") or "").strip() or None,
        "free_shipping": bool(data.get("free_shipping")),
        "max_quantity": data.get("max_quantity", type=int) or 10,
    }


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

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/landing_form.html",
                                   page=None, products=products, form=form)

        page = LandingPage(**form)
        db.session.add(page)
        db.session.commit()

        flash(f"تم إنشاء صفحة الهبوط — الرابط: /lp/{page.slug}", "success")
        return redirect(url_for("admin.landing_list"))

    return render_template("admin/landing_form.html",
                           page=None, products=products, form={})


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

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/landing_form.html",
                                   page=page, products=products, form=form)

        for key, value in form.items():
            setattr(page, key, value)
        db.session.commit()

        flash("تم حفظ التعديلات", "success")
        return redirect(url_for("admin.landing_list"))

    return render_template("admin/landing_form.html",
                           page=page, products=products, form={})


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


@admin_bp.route("/orders/<int:order_id>/zr-debug")
@login_required
def order_zr_debug(order_id):
    """
    تشخيص الإرسال لـZR — يعرض بالضبط شنو نبعثو وشنو يرد.

    ⚠️ ليش هاد الصفحة؟
    ZR يرد بـ400 «One or more validation errors» بلا تفاصيل فبعض الحالات.
    بلا ما نشوفو الـpayload الحقيقي والرد الخام، أي إصلاح يكون تخمين.
    """
    from services import zrexpress as zr

    order = Order.query.get_or_404(order_id)

    # نبنيو نفس الـpayload اللي غادي يتبعث — بلا ما نبعثوه
    preview, build_error = None, None
    try:
        import json as _json
        payload = zr.build_parcel_payload(order, order.commune_external_id or None)
        # ⚠️ ensure_ascii=False باش العربية تبان كيما هي، ماشي \u0627\u0644...
        preview = _json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        build_error = str(e)

    return render_template(
        "admin/zr_debug.html",
        order=order,
        preview=preview,
        build_error=build_error,
        problems=zr._check_parcel_data(order),
        territory_diag=(
            zr.diagnose_territory_lookup(order.wilaya_code, order.commune)
            if request.args.get("check_territory") else None
        ),
        last=zr.LAST_EXCHANGE,
        last_payload=(
            __import__("json").dumps(zr.LAST_EXCHANGE.get("payload"),
                                     ensure_ascii=False, indent=2)
            if zr.LAST_EXCHANGE.get("payload") else None
        ),
        config_ok=bool(os.environ.get("ZR_TENANT_ID", "").strip()
                       and os.environ.get("ZR_API_KEY", "").strip()),
        base_url=zr.BASE_URL,
    )


@admin_bp.route("/orders/<int:order_id>/zr-send-test", methods=["POST"])
@login_required
def order_zr_send_test(order_id):
    """
    يجرّب الإرسال ويرجّع للتشخيص — بلا ما يغيّر حالة الطلبية.

    مفيد باش نجرّبو تعديلات على الـpayload بلا ما نخربو الطلبية.
    """
    from services import zrexpress as zr

    order = Order.query.get_or_404(order_id)

    territory_id = order.commune_external_id or None
    if not territory_id and order.delivery_type != "office" and order.commune:
        try:
            territory_id = zr.lookup_territory_id(order.wilaya_code, order.commune)
            if territory_id:
                order.commune_external_id = territory_id
                db.session.commit()
                flash(f"لقينا معرّف البلدية وحفظناه: {territory_id}", "success")
            else:
                flash(
                    f"ما لقيناش معرّف «{order.commune}» — شوف القسم 3ب تحت",
                    "warning",
                )
        except Exception as e:
            flash(f"فشل البحث عن المعرّف: {e}", "warning")

    try:
        zr.create_parcel(order, territory_id=territory_id)
        flash("نجح الإرسال التجريبي — شوف الرد تحت", "success")
    except Exception as e:
        flash(f"فشل: {e}", "error")

    return redirect(url_for("admin.order_zr_debug", order_id=order.id))
