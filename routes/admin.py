"""
لوحة تحكم الأدمن — كاملة.

الأمان:
- كل الصفحات محمية بـ@login_required ما عدا صفحة الدخول
- كلمة السر مهشّرة (hash)، أبدًا نص صريح
- رفع الصور يمر على فحص أمني (services/uploads.py)
- كل عمليات التعديل/الحذف عبر POST فقط (ما يتنفذوش برابط GET عادي)
"""

from datetime import datetime

from models import utcnow   # ⚠️ بديل datetime.utcnow المهجورة
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, abort, jsonify, current_app,
)
from sqlalchemy import func

import os

from models import (
    db, Product, Category, ProductImage, ProductVariant,
    Offer, OfferItem, Carrier, ShippingRate, AdminUser, Coupon, Order, OrderItem,
    AbandonedCart, Review, UpsellRule, LandingPage, StoreSetting,
    VariantPack, VariantPriceTier,
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
            user.last_login_at = utcnow()
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

    # إحصائيات الزوار — من services/analytics
    from services.analytics import get_summary, purge_scan_noise
    try:
        # ⚠️ ننظّفو زيارات روبوتات المسح فكل فتح للوحة، ماشي فالإقلاع
        # وحدو. السبب: سجلات قديمة تسجّلت قبل ما نصلحو الفلتر، والأدمن
        # ما يستاهلش يعيد النشر باش يشوف أرقام صحيحة.
        purge_scan_noise()
        visitors = get_summary()
    except Exception:
        visitors = None   # ⚠️ الجدول ممكن ما يكونش موجود بعد أول نشر

    return render_template(
        "admin/dashboard.html",
        visitors=visitors,
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


@admin_bp.route("/products/new", methods=["GET", "POST"])
@admin_bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def product_form(product_id=None):
    """
    نموذج واحد للإضافة والتعديل — أبسط من ملوين منفصلين فيهم نفس الكود مكرر.
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
        return redirect(url_for("admin.product_form", product_id=product.id))

    return render_template(
        "admin/product_form.html", product=product, categories=categories
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

    ⚠️ ليش هكذا وماشي مسار لكل عملية؟
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
        return jsonify({"ok": False, "message": "ما وصلاتش حتى صورة"}), 400

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
    db.session.delete(product)
    db.session.flush()

    purge_unused_image_files(candidates)

    db.session.commit()
    flash(f"تم حذف «{name}»", "success")
    return redirect(url_for("admin.products_list"))


@admin_bp.route("/products/<int:product_id>/images/<int:image_id>/make-main", methods=["POST"])
@login_required
def product_image_make_main(product_id, image_id):
    """
    يخلي صورة معينة هي الرئيسية (اللي تبان فبطاقة المنتج وفالسلة).

    ⚠️ نتأكدو أن الصورة تخص هاد المنتج بالضبط — باش حد ما يقدرش يبعث id
    تاع صورة منتج آخر ويخلط الصور بين المنتجات.
    """
    image = ProductImage.query.filter_by(id=image_id, product_id=product_id).first_or_404()

    # ⚠️ الصورة التوضيحية ما تقدرش تكون رئيسية — الرئيسية هي اللي
    # تبان فبطاقة المنتج وفالسلة، ولازم تكون صورة السلعة نفسها.
    if image.is_extra:
        flash("الصور التوضيحية ما تقدرش تكون رئيسية", "error")
        return redirect(url_for("admin.product_form", product_id=product_id))

    image.product.image_filename = image.image_filename
    db.session.commit()

    flash("تم تعيين الصورة الرئيسية", "success")
    return redirect(url_for("admin.product_form", product_id=product_id))


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

    ⚠️ ليش ماشي رفع جديد؟ الصور نفسها: الأدمن يرفع صور المنتج مرة وحدة،
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
    return reply(True, f"تربطات صورة «{variant.label}»")


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

    # ⚠️ كل المنتجات وموديلاتهم — باش الأدمن يزيد حاجة للطلبية بلا ما
    # يخرج من الصفحة. نجيبوهم مرة وحدة هنا بدل ما القالب يسقسي
    # قاعدة البيانات فكل دورة.
    products = (
        Product.query.filter_by(is_active=True)
        .order_by(Product.name)
        .all()
    )

    return render_template(
        "admin/order_detail.html",
        order=order,
        status_labels=Order.STATUS_LABELS,
        products=products,
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
            return ("الطلبية اتبعثات لشركة التوصيل — التعديل عندنا "
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

    # ⚠️ «راجعة» كيما «ملغاة» فحساب المخزون: السلعة رجعات لينا فعليًا،
    # فلازم ترجع للرفوف. بلا هذا نبيعو حاجة وحنا نحسبوها ناقصة.
    was_off = old_status in Order.STATUS_TERMINAL
    now_off = new_status in Order.STATUS_TERMINAL

    # طلبية نشطة ولّات ملغاة/راجعة → نرجّعو المخزون
    if now_off and not was_off:
        restore_stock(order)
        flash("تم إرجاع المخزون للمنتجات", "success")

    # طلبية ملغاة/راجعة رجعات للنشاط → نعاودو نخصمو المخزون
    elif was_off and not now_off:
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
    elif show == "store":
        # تقييمات المتجر وحدها — مفيدة كي تبغي تراجعهم دفعة
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
    from services.zrexpress import (
        create_parcel, ZRError, resolve_territory_id, resolve_hub_id,
        resolve_city_id, is_configured,
    )

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
            "اضغط «افحص ليش ما لقيناش المعرّف» فصفحة التشخيص.",
            "warning",
        )

    try:
        result = create_parcel(order, territory_id=territory_id, hub_id=hub_id)
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


@admin_bp.route("/orders/<int:order_id>/zr-debug")
@login_required
def order_zr_debug(order_id):
    """
    تشخيص الإرسال لـZR — يعرض بالضبط واش نبعثو وواش يرد.

    ⚠️ ليش هاد الصفحة؟
    ZR يرد بـ400 «One or more validation errors» بلا تفاصيل فبعض الحالات.
    بلا ما نشوفو الـpayload الحقيقي والرد الخام، أي إصلاح يكون تخمين.
    """
    from services import zrexpress as zr

    order = Order.query.get_or_404(order_id)

    # نبنيو نفس الـpayload اللي راح يتبعث — بلا ما نبعثوه
    preview, build_error = None, None
    try:
        import json as _json
        # ⚠️ نستعملو نفس المعرّف اللي راح يستعملو الإرسال الحقيقي —
        # قبل، المعاينة كانت توري `territoryId` ناقصًا حتى كي الإرسال
        # يلقاه. البحث هنا بلا حفظ: المعاينة ما تبدّلش البيانات.
        preview_territory, _why = zr.resolve_territory_id(order)
        payload = zr.build_parcel_payload(order, preview_territory)
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
        # ⚠️ للمكتب نشخّصو على **بلدية المكتب** ماشي على `commune`
        # الفارغة — وإلا التشخيص يقول «ما لقيناش» بلا ما يقول واش دوّر.
        territory_diag=(
            _safe(zr.diagnose_territory_lookup,
                order.wilaya_code,
                zr.office_commune(order) if order.delivery_type == "office"
                else order.commune,
            )
            if request.args.get("check_territory") else None
        ),
        # نتائج مختبر الإرسال (إذا اتشغّل قبل شوية)
        lab=session.pop("zr_lab", None),
        # ⚠️ كل نداء لـZR ملفوف: صفحة **التشخيص** ما لازمش تنهار كي
        # ZR يطيح — هي بالضبط الصفحة اللي نجيو ليها كي يطيح شي حاجة.
        # (طاحت 500 فالإنتاج علاه.)
        hubs=_safe(zr.search_hubs) if request.args.get("hubs") else None,
        hub_match=(
            _safe(zr.resolve_hub_id, order) if request.args.get("hubs") else None
        ),
        spec=_safe(zr.fetch_openapi_spec) if request.args.get("spec") else None,
        endpoint_probe=(
            _safe(zr.probe_pickup_endpoints)
            if request.args.get("probe") else None
        ),
        levels=(
            _safe(zr.inspect_territory_levels)
            if request.args.get("levels") else None
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

    2) **الطرد عند ZR**: إذا الطلبية تبعثات، حذفها من عندنا
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


@admin_bp.route("/orders/bulk-delete", methods=["POST"])
@login_required
def orders_bulk_delete():
    """
    يحذف عدة طلبيات دفعة وحدة — مفيد لتنظيف طلبيات التجربة.

    ⚠️ نرجّعو المخزون لكل طلبية نشطة، نفس منطق الحذف الفردي.
    """
    ids = request.form.getlist("order_ids", type=int)

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
            f"مسحنا {removed} سطر قديم — بقى غير الأسبوع الجاري "
            f"(من {start.strftime('%d/%m')})",
            "success",
        )
    else:
        removed = purge_scan_noise()
        if removed:
            flash(f"مسحنا {removed} زيارة من روبوتات المسح", "success")
        else:
            flash("ماكانش زيارات روبوتات — الأرقام نظيفة", "success")

    return redirect(url_for("admin.dashboard"))


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
            f"({result['unchanged']} بلا تبديل، {result['from_zr']} وصلاتنا من ZR)",
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


@admin_bp.route("/shipping/sync-world", methods=["POST"])
@login_required
def shipping_sync_world():
    """
    يحدّث أسعار World Express من جدولهم الرسمي المخزّن عندنا.

    ⚠️ الفرق على ZR: هنا ماكانش API — الجدول مخزّن فـ
    `data/world_rates.json`. الزر يقارن قاعدة البيانات بالجدول
    ويوري بالضبط واش تبدّل، بدل ما الأدمن يقلّب 58 خانة بالعين.
    """
    from services.carrier_offices import sync_rates_from_file

    try:
        result = sync_rates_from_file("worldexpress")
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
            f"⚠️ {len(result['missing'])} ولاية ماكانتش فجدول World "
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
            flash(f"«{label}» مكرر — خليّنا الأول فقط", "warning")
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

    ⚠️ ليش هاد المسار؟
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

    ⚠️ ليش أزرار وماشي سحب؟ السحب (drag & drop) صعيب فالموبايل ويحتاج
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


@admin_bp.route("/variants/<int:variant_id>/price", methods=["POST"])
@login_required
def variant_price(variant_id):
    """
    يبدّل سعر موديل واحد — تعديل سريع من رأس البطاقة.

    ⚠️ ليش مسار مستقل؟ الفورم الرئيسي تاع المنتج طويل ويحفظ كلش.
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

    uploaded = request.files.get("excel_file")

    if not uploaded or not uploaded.filename:
        flash("اختر ملف Excel أولاً", "error")
        return redirect(url_for("admin.products_list"))

    if not uploaded.filename.lower().endswith((".xlsx", ".xlsm")):
        flash("الملف لازم يكون .xlsx", "error")
        return redirect(url_for("admin.products_list"))

    try:
        report = import_workbook(uploaded)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.products_list"))
    except Exception as e:
        db.session.rollback()
        flash(f"فشل الاستيراد: {e}", "error")
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

    flash("✅ " + (" · ".join(parts) if parts else "ماكانش تبديل"), "success")

    for warning in report["warnings"][:12]:
        flash(f"⚠️ {warning}", "warning")

    if len(report["warnings"]) > 12:
        flash(f"⚠️ و{len(report['warnings']) - 12} تحذير آخر", "warning")

    return redirect(url_for("admin.products_list"))
