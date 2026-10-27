"""
صفحات الهبوط للحملات الإعلانية.

الفكرة: رابط /lp/<slug> يعرض منتج واحد + فورم طلب مباشر.
ماكانش هيدر ولا سلة ولا روابط تخرّج الزائر — هدف واحد فقط.
"""

from flask import (
    Blueprint, render_template, abort, request, redirect, url_for, session, current_app,
)

from models import db, LandingPage, Carrier
from services import ratelimit
from services import stock as stock_service
from services.order_access import remember_order, session_owns_order
from utils import normalize_algerian_phone
from services.marketing import mark_cart_recovered
from services.orders import create_order, OrderError
from services.shipping import calculate_shipping, get_active_carriers
from routes.cart import _get_all_wilayas
from services.cart import CartLine

landing_bp = Blueprint("landing", __name__)


def _resolve_variant(page, form):
    """
    التنويعة اللي تتباع: اختيار الزبون (إذا الصفحة فيها اختيار موديل)،
    وإلا التنويعة الثابتة تاع الصفحة.

    ⚠️ ما نثقوش فـvariant_id اللي جا من الفورم: نتأكدو أنو تاع نفس المنتج.
    """
    if page.show_variant_picker:
        allowed = {v.id: v for v in page.pickable_variants}
        chosen = allowed.get(form.get("variant_id"))
        if chosen:
            return chosen
        return None
    return page.variant


def _build_lines(page, quantity, variant):
    """
    يبني سطور الطلبية من صفحة الهبوط — بلا مرور بالـsession.

    ⚠️ نستعملو نفس CartLine تاع السلة العادية باش create_order يخدم
    بلا تعديل، والطلبية تبان طبيعية فلوحة التحكم.

    إذا فيها باقة: سطرين (الأساسي + الثابت)، وسعر الحملة يتوزع عليهم
    بحيث المجموع = سعر الحملة بالضبط.
    """
    product = page.product
    main_price, bundle_price = page.price_split()

    lines = [CartLine(
        key=f"lp-{page.id}",
        kind="product",
        name=product.name,
        unit_price=main_price,
        quantity=quantity,
        image_filename=(variant.image_filename if variant and variant.image_filename
                        else page.image_filename),
        variant_label=variant.label if variant else None,
        slug=product.slug,
        product_id=product.id,
        variant_id=variant.id if variant else None,
        available_stock=(
            variant.stock_quantity if variant else product.stock_quantity
        ),
    )]

    if page.has_bundle:
        extra = page.bundle_product
        lines.append(CartLine(
            key=f"lp-{page.id}-bundle",
            kind="product",
            name=extra.name,
            unit_price=bundle_price,
            quantity=quantity,
            image_filename=extra.image_filename,
            slug=extra.slug,
            product_id=extra.id,
            available_stock=extra.stock_quantity,
        ))
    return lines


def _render_page(page, form, errors, status=200):
    """يعرض الصفحة. المعاينة (?theme= و ?fit=) للأدمن فقط — ما تتحفظش."""
    theme, fit, layout = page.theme, page.fit, page.page_layout
    preview = False
    if session.get("admin_id"):
        valid_themes = {t[0] for t in page.THEMES}
        valid_fits = {f[0] for f in page.FITS}
        valid_layouts = {l[0] for l in page.LAYOUTS}
        q_theme = request.args.get("theme")
        q_fit = request.args.get("fit")
        q_layout = request.args.get("layout")
        if q_theme in valid_themes:
            theme, preview = q_theme, True
        if q_fit in valid_fits:
            fit, preview = q_fit, True
        if q_layout in valid_layouts:
            layout, preview = q_layout, True

    html = render_template(
        "landing/page.html",
        page=page,
        product=page.product,
        theme=theme,
        fit=fit,
        layout=layout,
        expired=page.offer_expired,
        is_preview=preview,
        carriers=get_active_carriers(),
        wilayas=_get_all_wilayas(),
        form=form,
        errors=errors,
    )
    return (html, status) if status != 200 else html


@landing_bp.route("/lp/<slug>")
def view(slug):
    """يعرض صفحة الهبوط."""
    page = LandingPage.query.filter_by(slug=slug, is_active=True).first()
    if not page or not page.product or not page.product.is_active:
        abort(404)

    # ⚠️ العدّاد يتزاد بلا commit كامل باش ما نبطّئوش الصفحة.
    # الدقة المطلقة ماشي ضرورية هنا — الاتجاه هو المهم.
    # المعاينة من الأدمن ما تتحسبش كزيارة.
    if not session.get("admin_id"):
        page.view_count = (page.view_count or 0) + 1
        db.session.commit()

    return _render_page(page, form={}, errors=[])


@landing_bp.route("/lp/<slug>", methods=["POST"])
@ratelimit.limit("landing_submit")
def submit(slug):
    """يستقبل الطلبية من صفحة الهبوط."""
    page = LandingPage.query.filter_by(slug=slug, is_active=True).first()
    if not page or not page.product or not page.product.is_active:
        abort(404)

    form = _read_form(request.form)
    variant = _resolve_variant(page, form)
    errors = _validate(form, page, variant)

    if errors:
        return _render_page(page, form, errors, 400)

    # ---------- حساب التوصيل ----------
    quote = calculate_shipping(
        form["carrier_code"], form["wilaya_code"],
        form["delivery_type"], is_free=page.free_shipping,
    )

    if not quote.is_ok:
        return _render_page(
            page, form,
            [quote.message or "التوصيل غير متاح لهاد الولاية"], 400)

    carrier = Carrier.query.filter_by(code=form["carrier_code"]).first()

    wilayas = _get_all_wilayas()
    wilaya = next((w for w in wilayas if w["code"] == form["wilaya_code"]), None)
    wilaya_name = wilaya["name"] if wilaya else form["wilaya_code"]

    lines = _build_lines(page, form["quantity"], variant)

    try:
        order = create_order(
            lines=lines,
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
        return _render_page(page, form, [str(e)], 400)

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

    # ⚠️ صفحات الهبوط كانت ما تنحّيش السلات المتروكة إطلاقًا: زبون
    # يبدا فالمتجر (تتسجّل سلة)، يشوف إعلانًا، ويكمّل من صفحة الهبوط —
    # والسلة تبقى فقائمة المتابعة رغم أنو خلّص.
    mark_cart_recovered(form["phone"], order.order_number)

    # رسالة تأكيد تلقائية على الواتساب — تتسجّل فنفس الـcommit تاع الطلبية
    try:
        from services import whatsapp_api
        whatsapp_api.enqueue(order, "confirm")
    except Exception:   # noqa: BLE001 — الطلبية أهم من الواتساب
        current_app.logger.exception("تعذّر تسجيل رسالة التأكيد %s", order.order_number)

    db.session.commit()

    # ⚠️ نسجّلو الملكية فالجلسة **قبل** التوجيه لصفحة الشكر.
    # بلاها، صفحة الشكر ما تعرفش أن هاد المتصفح هو صاحب الطلبية،
    # وما تعرضش التفاصيل (وهذا هو المطلوب من غيرو).
    remember_order(order.order_number)

    return redirect(url_for("landing.thanks", slug=slug, order=order.order_number))


@landing_bp.route("/lp/<slug>/شكرا")
@landing_bp.route("/lp/<slug>/thanks")
@ratelimit.limit("landing_thanks")
def thanks(slug):
    """صفحة الشكر — هنا يتسجّل حدث Purchase فالبكسل."""
    page = LandingPage.query.filter_by(slug=slug).first()
    if not page:
        abort(404)

    from models import Order

    # 🔴 إصلاح ثغرة مؤكدة (IDOR): كان هنا
    #     Order.query.filter_by(order_number=...).first()
    # بلا أي تحقق من الملكية. رقم الطلبية 6 أرقام برك، وماكانش حد
    # للطلبات — فأي واحد يلوپ على مليون رقم ويقرا مبالغ **كل**
    # طلبيات المتجر (رقم أعمالك اليومي، متوسط الطلبية، سرعة النموّ).
    #
    # درك نفس قاعدة /order/success: الطلبية تبان فقط للمتصفح اللي
    # دارها. الرقم معرّف، ماشي صلاحية.
    order_number = (request.args.get("order") or "").strip().upper()
    order = None

    if order_number and session_owns_order(order_number):
        order = Order.query.filter_by(order_number=order_number).first()

    # ⚠️ ما نديروش abort(404) كي الملكية ما تثبتش: صفحة الشكر تبقى
    # تبان بلا تفاصيل. بلا هذا، الرد نفسو يولّي مؤشرًا (404 = الرقم
    # ماكانش، 200 = موجود) وترجع نفس ثغرة التعداد من باب آخر.
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

    try:
        variant_id = int(data.get("variant_id") or 0) or None
    except (TypeError, ValueError):
        variant_id = None

    return {
        "variant_id": variant_id,
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


def _validate(form, page, variant=None):
    """
    يتحقق من الفورم. يرجع قائمة أخطاء بالعربية.

    ⚠️ نفس قواعد الـcheckout العادي — خاصة الهاتف: ZR يرفض الأرقام
    غير الجزائرية بـ400، فأحسن نمسكوها هنا.
    """
    errors = []

    # العرض المحدّد بتاريخ: بعد آخر يوم ما نقبلوش طلبيات بسعر الحملة
    # (حتى لو الزبون فتح الصفحة قبل نهاية العرض وكمّل الفورم بعدها).
    if page.offer_expired:
        errors.append("انتهى العرض — تواصل معنا إذا حاب تطلب")
        return errors

    if not form["name"] or len(form["name"]) < 3:
        errors.append("اكتب اسمك ولقبك كاملين")

    # ---------- الهاتف ----------
    # ⚠️ المنطق انتقل لـutils.normalize_algerian_phone باش يكون مصدرًا
    # وحيدًا: السلات المتروكة كانت تقارن الأرقام بطريقة أخرى، فنفس
    # الزبون يبان شخصين ما داموش يكتبو الرقم بنفس الصيغة.
    normalized = normalize_algerian_phone(form["phone"])
    if not normalized:
        errors.append("رقم الهاتف غير صحيح — لازم يبدا بـ05 ولا 06 ولا 07")
    else:
        form["phone"] = normalized

    # ---------- الكمية ----------
    if form["quantity"] > page.max_quantity:
        errors.append(f"أقصى كمية {page.max_quantity}")

    # 🔴 إصلاح حرج: كان هنا فحص يقرا stock_quantity مباشرة ويتجاهل أن
    # تتبّع المخزون مطفي عالميًا. القيمة 0 لكل المنتجات، فالشرط
    # `1 > 0` كان صحيحًا دائمًا و**كل** طلبية من صفحة الهبوط تترفض
    # برسالة «المخزون المتوفر: 0 فقط» — يعني الإعلانات تخلّص والزبون
    # يشوف خطأ.
    #
    # درك نستعملو نفس الدالة اللي تستعملها السلة والطلبيات
    # (services/stock.py) — قاعدة وحدة، ما تتناقضش.
    if page.show_variant_picker and variant is None:
        errors.append("اختر الموديل")

    stock_error = stock_service.check_availability(
        page.product, variant, form["quantity"]
    )
    if stock_error:
        errors.append(stock_error)

    # الباقة: المنتج الثابت لازم يكون متوفر هو كذلك
    if page.has_bundle:
        bundle_error = stock_service.check_availability(
            page.bundle_product, None, form["quantity"]
        )
        if bundle_error:
            errors.append(bundle_error)

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
