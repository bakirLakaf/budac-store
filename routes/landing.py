"""
صفحات الهبوط للحملات الإعلانية.

الفكرة: رابط /lp/<slug> يعرض منتج واحد + فورم طلب مباشر.
ماكانش هيدر ولا سلة ولا روابط تخرّج الزائر — هدف واحد فقط.
"""

from flask import (
    Blueprint, render_template, abort, request, redirect,
    url_for, flash, jsonify,
)

from models import db, LandingPage, Product, Carrier
from services.orders import create_order, OrderError
from services.shipping import calculate_shipping, get_active_carriers
from routes.cart import _get_all_wilayas
from services.cart import CartLine

landing_bp = Blueprint("landing", __name__)


def _build_line(page, quantity):
    """
    يبني سطر السلة من صفحة الهبوط — بلا مرور بالـsession.

    ⚠️ نستعملو نفس CartLine ديال السلة العادية باش create_order يخدم
    بلا تعديل، والطلبية تبان طبيعية فلوحة التحكم.
    """
    product = page.product

    return CartLine(
        key=f"lp-{page.id}",
        kind="product",
        name=product.name,
        unit_price=page.unit_price,
        quantity=quantity,
        image_filename=page.image_filename,
        variant_label=page.variant.label if page.variant else None,
        slug=product.slug,
        product_id=product.id,
        variant_id=page.variant_id,
        available_stock=(
            page.variant.stock_quantity if page.variant
            else product.stock_quantity
        ),
    )


@landing_bp.route("/lp/<slug>")
def view(slug):
    """يعرض صفحة الهبوط."""
    page = LandingPage.query.filter_by(slug=slug, is_active=True).first()
    if not page or not page.product or not page.product.is_active:
        abort(404)

    # ⚠️ العدّاد يتزاد بلا commit كامل باش ما نبطّئوش الصفحة.
    # الدقة المطلقة ماشي ضرورية هنا — الاتجاه هو المهم.
    page.view_count = (page.view_count or 0) + 1
    db.session.commit()

    return render_template(
        "landing/page.html",
        page=page,
        product=page.product,
        carriers=get_active_carriers(),
        wilayas=_get_all_wilayas(),
        form={},
        errors=[],
    )


@landing_bp.route("/lp/<slug>", methods=["POST"])
def submit(slug):
    """يستقبل الطلبية من صفحة الهبوط."""
    page = LandingPage.query.filter_by(slug=slug, is_active=True).first()
    if not page or not page.product or not page.product.is_active:
        abort(404)

    form = _read_form(request.form)
    errors = _validate(form, page)

    if errors:
        return render_template(
            "landing/page.html",
            page=page, product=page.product,
            carriers=get_active_carriers(),
            wilayas=_get_all_wilayas(),
            form=form, errors=errors,
        ), 400

    # ---------- حساب التوصيل ----------
    quote = calculate_shipping(
        form["carrier_code"], form["wilaya_code"],
        form["delivery_type"], is_free=page.free_shipping,
    )

    if not quote.is_ok:
        return render_template(
            "landing/page.html",
            page=page, product=page.product,
            carriers=get_active_carriers(),
            wilayas=_get_all_wilayas(),
            form=form,
            errors=[quote.message or "التوصيل غير متاح لهاد الولاية"],
        ), 400

    carrier = Carrier.query.filter_by(code=form["carrier_code"]).first()

    wilayas = _get_all_wilayas()
    wilaya = next((w for w in wilayas if w["code"] == form["wilaya_code"]), None)
    wilaya_name = wilaya["name"] if wilaya else form["wilaya_code"]

    line = _build_line(page, form["quantity"])

    try:
        order = create_order(
            lines=[line],
            customer_data={
                "name": form["name"],
                "phone": form["phone"],
                "wilaya_code": form["wilaya_code"],
                "wilaya_name": wilaya_name,
                "commune": form["commune"],
                "commune_external_id": form["commune_id"] or None,
                "pickup_point_id": form["pickup_point_id"] or None,
                "pickup_point_name": form["pickup_point_name"] or None,
                "address_note": form["address_note"],
            },
            shipping_info={
                "carrier_code": form["carrier_code"],
                "carrier_name": carrier.name if carrier else None,
                "delivery_type": form["delivery_type"],
                "price": quote.price or 0,
            },
            customer_note=form["note"],
        )
    except OrderError as e:
        return render_template(
            "landing/page.html",
            page=page, product=page.product,
            carriers=get_active_carriers(),
            wilayas=_get_all_wilayas(),
            form=form, errors=[str(e)],
        ), 400

    # نسجّلو مصدر الحملة فملاحظة داخلية — مفيد لمعرفة أي إعلان جاب الطلبية
    source = (request.args.get("utm_source") or request.form.get("utm_source") or "").strip()
    campaign = (request.args.get("utm_campaign") or request.form.get("utm_campaign") or "").strip()
    if source or campaign:
        tag = f"[حملة: {page.slug}"
        if source:
            tag += f" · {source}"
        if campaign:
            tag += f" · {campaign}"
        tag += "]"
        order.admin_note = f"{tag} {order.admin_note or ''}".strip()

    page.order_count = (page.order_count or 0) + 1
    db.session.commit()

    return redirect(url_for("landing.thanks", slug=slug, order=order.order_number))


@landing_bp.route("/lp/<slug>/شكرا")
@landing_bp.route("/lp/<slug>/thanks")
def thanks(slug):
    """صفحة الشكر — هنا يتسجّل حدث Purchase فالبكسل."""
    page = LandingPage.query.filter_by(slug=slug).first()
    if not page:
        abort(404)

    from models import Order
    order_number = (request.args.get("order") or "").strip()
    order = Order.query.filter_by(order_number=order_number).first() if order_number else None

    return render_template("landing/thanks.html", page=page, order=order)


# ═══════════════════════════════════════════════════
# قراءة الفورم والتحقق
# ═══════════════════════════════════════════════════

def _read_form(data):
    """يقرا الفورم فـdict نظيف."""
    try:
        quantity = int(data.get("quantity") or 1)
    except (TypeError, ValueError):
        quantity = 1

    return {
        "name": (data.get("name") or "").strip(),
        "phone": (data.get("phone") or "").strip(),
        "quantity": max(1, quantity),
        "wilaya_code": (data.get("wilaya_code") or "").strip(),
        "commune": (data.get("commune") or "").strip(),
        "commune_id": (data.get("commune_id") or "").strip(),
        "pickup_point_id": (data.get("pickup_point_id") or "").strip(),
        "pickup_point_name": (data.get("pickup_point_name") or "").strip(),
        "address_note": (data.get("address_note") or "").strip(),
        "carrier_code": (data.get("carrier_code") or "").strip(),
        "delivery_type": (data.get("delivery_type") or "").strip(),
        "note": (data.get("note") or "").strip(),
    }


def _validate(form, page):
    """
    يتحقق من الفورم. يرجع قائمة أخطاء بالعربية.

    ⚠️ نفس قواعد الـcheckout العادي — خاصة الهاتف: ZR يرفض الأرقام
    غير الجزائرية بـ400، فأحسن نمسكوها هنا.
    """
    errors = []

    if not form["name"] or len(form["name"]) < 3:
        errors.append("اكتب اسمك ولقبك كاملين")

    # ---------- الهاتف ----------
    digits = "".join(c for c in form["phone"] if c.isdigit())
    if digits.startswith("00213"):
        digits = "0" + digits[5:]
    elif digits.startswith("213"):
        digits = "0" + digits[3:]

    is_mobile = len(digits) == 10 and digits[:2] in ("05", "06", "07")
    is_landline = len(digits) == 9 and digits[:2] in ("02", "03", "04")

    if not (is_mobile or is_landline):
        errors.append("رقم الهاتف غير صحيح — لازم يبدا بـ05 ولا 06 ولا 07")
    else:
        form["phone"] = digits

    # ---------- الكمية ----------
    if form["quantity"] > page.max_quantity:
        errors.append(f"أقصى كمية {page.max_quantity}")

    stock = (page.variant.stock_quantity if page.variant
             else page.product.stock_quantity)
    if stock is not None and form["quantity"] > stock:
        errors.append(f"المخزون المتوفر: {stock} فقط")

    # ---------- التوصيل ----------
    if not form["wilaya_code"]:
        errors.append("اختر الولاية")

    if not form["carrier_code"]:
        errors.append("اختر شركة التوصيل")

    if form["delivery_type"] not in ("home", "office"):
        errors.append("اختر نوع التوصيل")
    elif form["delivery_type"] == "office":
        if not form["pickup_point_name"]:
            errors.append("اختر مكتب الاستلام")
        # تنظيف: المكتب ما يحتاجش بلدية
        form["commune"] = ""
        form["commune_id"] = ""
    else:
        if not form["commune"]:
            errors.append("اختر البلدية")
        form["pickup_point_id"] = ""
        form["pickup_point_name"] = ""

    return errors
