"""
السلة، الدفع (checkout)، وتتبع الطلبية.
"""

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, jsonify, abort,
)

from models import db, Coupon, Order, ShippingRate
from services import cart as cart_service
from services.marketing import (
    save_abandoned_cart, mark_cart_recovered, get_upsell_suggestions,
)
from services.orders import create_order, find_coupon, OrderError
from services.shipping import (
    calculate_shipping, get_active_carriers, STATUS_OK, STATUS_FREE,
)

cart_bp = Blueprint("cart", __name__)


# ==================== السلة ====================

@cart_bp.route("/cart")
def view_cart():
    lines = cart_service.get_cart_lines()
    subtotal = cart_service.get_cart_subtotal(lines)
    stock_issues = cart_service.has_stock_issues(lines)

    coupon, coupon_error = _get_session_coupon(subtotal)

    discount, shipping_discount = (0, 0)
    if coupon:
        discount, shipping_discount = coupon.compute_discount(subtotal)

    # اقتراحات: نعرضوها فالسلة (ماشي بعد الشراء كيما YouCan) — الزبون مازال
    # فوضع شراء، فاحتمال القبول أعلى، وما نخربوش عليه فرحة إتمام الطلب
    upsells = get_upsell_suggestions(lines, limit=3)

    return render_template(
        "shop/cart.html",
        lines=lines,
        subtotal=subtotal,
        stock_issues=stock_issues,
        coupon=coupon,
        coupon_error=coupon_error,
        discount=discount,
        upsells=upsells,
    )


@cart_bp.route("/cart/add", methods=["POST"])
def add():
    kind = request.form.get("kind", "product")
    item_id = request.form.get("item_id", type=int)
    variant_id = request.form.get("variant_id", type=int)
    quantity = request.form.get("quantity", type=int) or 1

    if not item_id:
        flash("طلب غير صالح", "error")
        return redirect(request.referrer or url_for("shop.home"))

    ok, error = cart_service.add_to_cart(kind, item_id, variant_id, quantity)

    if ok:
        flash("تمت الإضافة للسلة", "success")
    else:
        flash(error, "error")

    # لو الزبون بغا يكمل تسوق، نرجعوه لفين كان
    if request.form.get("go_to_cart"):
        return redirect(url_for("cart.view_cart"))
    return redirect(request.referrer or url_for("shop.home"))


@cart_bp.route("/cart/update", methods=["POST"])
def update():
    key = request.form.get("key")
    quantity = request.form.get("quantity", type=int)

    if key and quantity is not None:
        cart_service.update_quantity(key, quantity)

    return redirect(url_for("cart.view_cart"))


@cart_bp.route("/cart/remove", methods=["POST"])
def remove():
    key = request.form.get("key")
    if key:
        cart_service.remove_from_cart(key)
        flash("تم حذف العنصر من السلة", "success")
    return redirect(url_for("cart.view_cart"))


# ==================== API السلة (اللوحة الجانبية) ====================

def _cart_json():
    """
    يرجّع حالة السلة كـJSON — تستعملها لوحة السلة الجانبية فالصفحة الرئيسية.
    ما نعتمدوش على أسعار من المتصفح: كلش يُحسب هنا من قاعدة البيانات.
    """
    lines = cart_service.get_cart_lines()
    subtotal = cart_service.get_cart_subtotal(lines)
    return jsonify({
        "count": sum(line.quantity for line in lines),
        "subtotal": subtotal,
        "subtotal_display": f"{subtotal:,} دج",
        "lines": [
            {
                "key": line.key,
                "name": line.name,
                "variant_label": line.variant_label,
                "quantity": line.quantity,
                "unit_price_display": line.unit_price_display,
                "line_total_display": line.line_total_display,
                "image": line.image_filename,
                "kind": line.kind,
            }
            for line in lines
        ],
    })


@cart_bp.route("/api/cart")
def api_cart():
    """حالة السلة الحالية."""
    return _cart_json()


@cart_bp.route("/api/cart/update", methods=["POST"])
def api_cart_update():
    """تغيير الكمية من اللوحة الجانبية. quantity = 0 يعني حذف."""
    data = request.get_json(silent=True) or {}
    key = data.get("key")
    quantity = data.get("quantity")

    if not key or quantity is None:
        return jsonify({"error": "طلب غير صالح"}), 400

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return jsonify({"error": "كمية غير صالحة"}), 400

    if quantity <= 0:
        cart_service.remove_from_cart(key)
    else:
        cart_service.update_quantity(key, quantity)

    return _cart_json()


# ==================== الكوبونات ====================

@cart_bp.route("/cart/coupon", methods=["POST"])
def apply_coupon():
    code = request.form.get("coupon_code", "")
    subtotal = cart_service.get_cart_subtotal()

    coupon, error = find_coupon(code, subtotal)

    if coupon:
        session["coupon_code"] = coupon.code
        flash(f"تم تطبيق الكوبون «{coupon.code}» — خصم {coupon.value_display}", "success")
    else:
        session.pop("coupon_code", None)
        flash(error, "error")

    return redirect(request.referrer or url_for("cart.view_cart"))


@cart_bp.route("/cart/coupon/remove", methods=["POST"])
def remove_coupon():
    session.pop("coupon_code", None)
    flash("تم إلغاء الكوبون", "success")
    return redirect(request.referrer or url_for("cart.view_cart"))


def _get_session_coupon(subtotal):
    """
    يجيب الكوبون المحفوظ فالـsession ويعاود يتحقق منو.

    ⚠️ نعاودو التحقق فكل مرة (ماشي غير وقت التطبيق) لأن السلة تقدر تتبدّل
    بعد ما طبّق الكوبون — مثلاً حيّد منتج وما بقاش يوصل للحد الأدنى.
    """
    code = session.get("coupon_code")
    if not code:
        return None, None

    coupon = Coupon.query.filter_by(code=code).first()
    if not coupon:
        session.pop("coupon_code", None)
        return None, None

    is_valid, reason = coupon.check_validity(subtotal)
    if not is_valid:
        return None, reason

    return coupon, None


# ==================== الدفع (Checkout) ====================

@cart_bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    lines = cart_service.get_cart_lines()

    if not lines:
        flash("السلة فارغة", "warning")
        return redirect(url_for("shop.home"))

    subtotal = cart_service.get_cart_subtotal(lines)
    stock_issues = cart_service.has_stock_issues(lines)

    if stock_issues:
        flash("بعض المنتجات ما بقاتش فيها كمية كافية — عدّل السلة", "error")
        return redirect(url_for("cart.view_cart"))

    carriers = get_active_carriers()
    wilayas = _get_all_wilayas()
    coupon, _ = _get_session_coupon(subtotal)

    if request.method == "POST":
        form = _read_checkout_form(request.form)
        errors = _validate_checkout_form(form)

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "shop/checkout.html", lines=lines, subtotal=subtotal,
                carriers=carriers, wilayas=wilayas, coupon=coupon, form=form,
            )

        # نحسبو التوصيل من جديد فالسيرفر — ما نثقوش فأي سعر جاي من الفورم
        quote = calculate_shipping(
            form["carrier_code"], form["wilaya_code"], form["delivery_type"]
        )

        if quote.status not in (STATUS_OK, STATUS_FREE):
            flash(quote.message or "التوصيل غير متوفر لهذا الاختيار", "error")
            return render_template(
                "shop/checkout.html", lines=lines, subtotal=subtotal,
                carriers=carriers, wilayas=wilayas, coupon=coupon, form=form,
            )

        carrier = next((c for c in carriers if c.code == form["carrier_code"]), None)
        wilaya = next((w for w in wilayas if w["code"] == form["wilaya_code"]), None)

        try:
            order = create_order(
                lines=lines,
                customer_data={
                    "name": form["name"],
                    "phone": form["phone"],
                    "wilaya_code": form["wilaya_code"],
                    "wilaya_name": wilaya["name"] if wilaya else form["wilaya_code"],
                    "commune": form["commune"],
                    "address_note": form["address_note"],
                },
                shipping_info={
                    "carrier_code": form["carrier_code"],
                    "carrier_name": carrier.name if carrier else None,
                    "delivery_type": form["delivery_type"],
                    "price": quote.price or 0,
                },
                coupon=coupon,
                customer_note=form["note"],
            )
        except OrderError as e:
            flash(str(e), "error")
            return render_template(
                "shop/checkout.html", lines=lines, subtotal=subtotal,
                carriers=carriers, wilayas=wilayas, coupon=coupon, form=form,
            )

        # الزبون كمّل — نعلّمو سلاتو المتروكة باش الأدمن ما يتصلش بيه بلا فايدة
        mark_cart_recovered(form["phone"])

        cart_service.clear_cart()
        session["last_order_number"] = order.order_number

        return redirect(url_for("cart.order_success", order_number=order.order_number))

    # GET
    return render_template(
        "shop/checkout.html", lines=lines, subtotal=subtotal,
        carriers=carriers, wilayas=wilayas, coupon=coupon, form={},
    )


@cart_bp.route("/api/save-progress", methods=["POST"])
def api_save_progress():
    """
    يسجّل بيانات الزبون بمجرد ما يكتبها فالـcheckout — قبل ما يضغط "أكّد".

    ⚠️ هذا هو جوهر استرجاع السلات: بلاه، الزبون اللي يخرج قبل التأكيد
    يضيع كليًا وما نعرفوش حتى مين كان.

    الصفحة تنادي هاد المسار تلقائيًا كي الزبون يكمّل اسمو وهاتفو.
    """
    data = request.get_json(silent=True) or {}

    phone = (data.get("phone") or "").strip()
    phone_digits = "".join(c for c in phone if c.isdigit())

    # ما نسجّلوش أرقام ناقصة — تولّي بيانات بلا فايدة عند الأدمن
    if len(phone_digits) < 9:
        return jsonify({"saved": False})

    lines = cart_service.get_cart_lines()
    if not lines:
        return jsonify({"saved": False})

    save_abandoned_cart(
        {
            "name": (data.get("name") or "").strip(),
            "phone": phone,
            "wilaya_name": (data.get("wilaya_name") or "").strip(),
            "commune": (data.get("commune") or "").strip(),
        },
        lines,
        cart_service.get_cart_subtotal(lines),
    )

    return jsonify({"saved": True})


@cart_bp.route("/api/shipping-price")
def api_shipping_price():
    """
    يرجع سعر التوصيل مباشرة (JSON) باش الصفحة تحدّث المجموع بلا إعادة تحميل.
    """
    carrier_code = request.args.get("carrier")
    wilaya_code = request.args.get("wilaya")
    delivery_type = request.args.get("type", "home")

    quote = calculate_shipping(carrier_code, wilaya_code, delivery_type)

    return jsonify({
        "status": quote.status,
        "price": quote.price,
        "display": quote.price_display,
        "ok": quote.is_ok,
        "message": quote.message,
    })


@cart_bp.route("/order/success/<order_number>")
def order_success(order_number):
    """
    صفحة تأكيد الطلبية. متاحة فقط للطلبية اللي تسجلت فهاد الجلسة —
    باش حد ما يقدرش يشوف طلبيات الناس بتجريب أرقام.
    """
    if session.get("last_order_number") != order_number:
        abort(404)

    order = Order.query.filter_by(order_number=order_number).first_or_404()
    return render_template("shop/order_success.html", order=order)


@cart_bp.route("/track", methods=["GET", "POST"])
def track_order():
    """
    تتبع الطلبية برقمها + رقم الهاتف.
    نطلبو الاثنين معًا باش حد ما يقدرش يتصفح طلبيات الناس برقم عشوائي.
    """
    order = None

    if request.method == "POST":
        number = (request.form.get("order_number") or "").strip().upper()
        phone = (request.form.get("phone") or "").strip()

        if not number or not phone:
            flash("اكتب رقم الطلبية ورقم الهاتف", "error")
        else:
            order = Order.query.filter_by(
                order_number=number, customer_phone=phone
            ).first()

            if not order:
                # رسالة وحدة للحالتين، باش ما نكشفوش واش الرقم موجود
                flash("ما لقيناش طلبية بهاد المعلومات — تأكد منهم", "error")

    return render_template("shop/track.html", order=order)


# ==================== أدوات مساعدة ====================

def _get_all_wilayas():
    """
    قائمة الولايات الفريدة (58 ولاية) من جدول أسعار التوصيل.
    نجيبوها من قاعدة البيانات باش تبقى مصدر وحيد للحقيقة.
    """
    rows = (
        db.session.query(ShippingRate.wilaya_code, ShippingRate.wilaya_name)
        .distinct()
        .order_by(ShippingRate.wilaya_code)
        .all()
    )
    return [{"code": code, "name": name} for code, name in rows]


def _read_checkout_form(form_data):
    """يقرا بيانات الفورم فـdict نظيف — باش نعاودو عرضهم إذا كان خطأ."""
    return {
        "name": (form_data.get("name") or "").strip(),
        "phone": (form_data.get("phone") or "").strip(),
        "wilaya_code": (form_data.get("wilaya_code") or "").strip(),
        "commune": (form_data.get("commune") or "").strip(),
        "address_note": (form_data.get("address_note") or "").strip(),
        "carrier_code": (form_data.get("carrier_code") or "").strip(),
        "delivery_type": form_data.get("delivery_type") or "home",
        "note": (form_data.get("note") or "").strip(),
    }


def _validate_checkout_form(form):
    """
    يتحقق من بيانات الفورم. يرجع قائمة أخطاء بالعربية (فارغة = كل شي مليح).
    """
    errors = []

    if not form["name"] or len(form["name"]) < 3:
        errors.append("اكتب اسمك ولقبك كاملين")

    phone_digits = "".join(c for c in form["phone"] if c.isdigit())
    if len(phone_digits) < 9:
        errors.append("رقم الهاتف غير صحيح")

    if not form["wilaya_code"]:
        errors.append("اختر الولاية")

    if not form["carrier_code"]:
        errors.append("اختر شركة التوصيل")

    if form["delivery_type"] not in ("home", "office"):
        errors.append("اختر نوع التوصيل")

    if form["delivery_type"] == "home" and not form["commune"]:
        errors.append("اكتب البلدية (ضرورية للتوصيل للمنزل)")

    return errors
