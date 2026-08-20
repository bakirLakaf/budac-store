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
    pack_id = request.form.get("pack_id", type=int)

    if not item_id:
        flash("طلب غير صالح", "error")
        return redirect(request.referrer or url_for("shop.home"))

    ok, error = cart_service.add_to_cart(kind, item_id, variant_id, quantity, pack_id)

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
        # ⚠️ عدد المنتجات المختلفة، ماشي مجموع الكميات — لازم يطابق
        # get_cart_count() المستعملة فالهيدر، وإلا الرقمين يختلفو.
        "count": len(lines),
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


@cart_bp.route("/api/communes")
def api_communes():
    """
    بلديات ولاية معيّنة — لقائمة البلديات فصفحة الطلب.

    ⚠️ نعرضو **كل** بلديات الولاية (القائمة الرسمية)، ونعلّمو اللي الشركة
    ما توصلش ليها. هكذا الزبون يلقى بلديتو دائمًا ويفهم علاش ما تخدمش،
    بدل ما يظن أن الموقع ناقص.
    """
    from models import Carrier, Commune, CarrierCommune

    carrier_code = (request.args.get("carrier") or "").strip()
    wilaya_code = (request.args.get("wilaya") or "").strip()

    if not wilaya_code:
        return jsonify({"communes": [], "message": "اختر الولاية أولاً"})

    wilaya_code = wilaya_code.zfill(2)

    communes = (
        Commune.query
        .filter_by(wilaya_code=wilaya_code)
        .order_by(Commune.name_ar)
        .all()
    )

    if not communes:
        return jsonify({
            "communes": [],
            "message": "ماكانش بلديات لهاد الولاية. شغّل seed_communes.py",
        })

    # روابط الشركة (إذا كانت مزامَنة)
    links = {}
    carrier = Carrier.query.filter_by(code=carrier_code, is_active=True).first()
    if carrier:
        links = {
            l.commune_id: l
            for l in CarrierCommune.query.filter_by(carrier_id=carrier.id).all()
        }

    results = []
    for c in communes:
        link = links.get(c.id)

        # ⚠️ هاد القائمة تُعرض **فقط للتوصيل للمنزل** — التوصيل للمكتب
        # عندو قائمة مكاتب خاصة بيه (شوف /api/pickup-points).
        # فالتوفّر هنا = التوصيل للمنزل، ماشي can_send العام.
        if link is None:
            # بلا مزامنة نقبلو الكل (الأدمن يبعث يدويًا)
            available = True
            external_id = ""
        else:
            available = link.has_home_delivery
            external_id = link.external_id or ""

        results.append({
            "id": external_id,
            "name": c.display_name,
            "daira": c.daira or "",
            "available": available,
        })

    return jsonify({
        "communes": results,
        "count": len(results),
        "synced": bool(links),
    })


@cart_bp.route("/api/pickup-points")
def api_pickup_points():
    """
    مكاتب الاستلام لولاية معيّنة — كي يختار الزبون «التوصيل للمكتب».
    """
    from services.zrexpress import get_pickup_points, ZRError, is_configured

    carrier_code = (request.args.get("carrier") or "").strip()
    wilaya_code = (request.args.get("wilaya") or "").strip()

    if carrier_code != "zrexpress":
        return jsonify({
            "points": [],
            "message": "هاد الشركة ما عندهاش قائمة مكاتب — اكتب البلدية عادي",
        })

    if not is_configured():
        return jsonify({"points": [], "message": "التكامل ماشي مفعّل"})

    try:
        points = get_pickup_points(wilaya_code)
    except ZRError as e:
        return jsonify({"points": [], "message": str(e)})

    return jsonify({"points": points, "count": len(points)})


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
                    "commune_external_id": form.get("commune_id") or None,
                    "pickup_point_id": form.get("pickup_point_id") or None,
                    "pickup_point_name": form.get("pickup_point_name") or None,
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
        "commune_id": (form_data.get("commune_id") or "").strip(),
        "pickup_point_id": (form_data.get("pickup_point_id") or "").strip(),
        "pickup_point_name": (form_data.get("pickup_point_name") or "").strip(),
        "address_note": (form_data.get("address_note") or "").strip(),
        "carrier_code": (form_data.get("carrier_code") or "").strip(),
        # ⚠️ ما نفرضوش "home" كقيمة افتراضية — لازم الزبون يختار بنفسو.
        # القيمة الفارغة تولّي خطأ واضح "اختر نوع التوصيل".
        "delivery_type": (form_data.get("delivery_type") or "").strip(),
        "note": (form_data.get("note") or "").strip(),
    }


def _validate_checkout_form(form):
    """
    يتحقق من بيانات الفورم. يرجع قائمة أخطاء بالعربية (فارغة = كل شي مليح).
    """
    errors = []

    if not form["name"] or len(form["name"]) < 3:
        errors.append("اكتب اسمك ولقبك كاملين")

    # ⚠️ التحقق القديم كان يقبل أي 9 أرقام — حتى 0123456789.
    # ZR يرفض هاد الأرقام بـ400، والطلبية تتحبس عندنا بلا ما نفهمو ليش.
    # أحسن نمنعوه وقت الطلب بدل ما نكتشفوه وقت الإرسال.
    #
    # الأرقام الجزائرية الصحيحة:
    #   جوال : 05/06/07 + 8 أرقام  (مثال 0666255200)
    #   ثابت : 02/03/04 + 7 أرقام  (مثال 032123456)
    phone_digits = "".join(c for c in form["phone"] if c.isdigit())

    # نحيّدو رمز الدولة إذا كتبو (+213 ولا 00213)
    if phone_digits.startswith("00213"):
        phone_digits = "0" + phone_digits[5:]
    elif phone_digits.startswith("213"):
        phone_digits = "0" + phone_digits[3:]

    is_mobile = len(phone_digits) == 10 and phone_digits[:2] in ("05", "06", "07")
    is_landline = len(phone_digits) == 9 and phone_digits[:2] in ("02", "03", "04")

    if not (is_mobile or is_landline):
        errors.append(
            "رقم الهاتف غير صحيح — لازم يبدا بـ05 ولا 06 ولا 07 (مثال: 0666255200)"
        )
    else:
        # نخزّنو الرقم منظّف — بلا مسافات ولا رموز
        form["phone"] = phone_digits

    if not form["wilaya_code"]:
        errors.append("اختر الولاية")

    if not form["carrier_code"]:
        errors.append("اختر شركة التوصيل")

    if form["delivery_type"] not in ("home", "office"):
        errors.append("اختر نوع التوصيل")
    # ⚠️ التوصيل للمنزل يحتاج بلدية، والتوصيل للمكتب يحتاج مكتب.
    # ما نخلطوش بيناتهم: الشركة تحتاج معلومة مختلفة فكل حالة.
    elif form["delivery_type"] == "office":
        if not form.get("pickup_point_name"):
            errors.append("اختر مكتب الاستلام من القائمة")
    else:
        if not form["commune"]:
            errors.append("اختر البلدية من القائمة")

    # ⚠️ تنظيف إجباري: كل نوع توصيل عندو حقولو.
    # بلا هذا، الزبون اللي يبدّل من "مكتب" لـ"منزل" يخلي وراه اسم مكتب
    # قديم فالحقل المخفي — وتتسجّل طلبية "توصيل للمنزل" فيها مكتب تبسة
    # بينما الولاية الجزائر. الواجهة تنظّف كذلك، لكن السيرفر ما يثقش فيها.
    if form["delivery_type"] == "office":
        form["commune"] = ""
        form["commune_id"] = ""
    else:
        form["pickup_point_id"] = ""
        form["pickup_point_name"] = ""

    return errors
