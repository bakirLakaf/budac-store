"""
منطق السلة — مركزي فمكان واحد.

قرار تصميم: السلة تتخزن فالـsession (كوكي مشفّر عند Flask)، ماشي فقاعدة البيانات.
السبب: الزبون ما يحتاجش حساب باش يشري، والسلة مؤقتة بطبيعتها. هذا أبسط وأسرع.
لكن ⚠️ نخزّنو فالـsession غير المعرّفات والكميات — الأسعار والأسماء نجيبوهم من
قاعدة البيانات فكل مرة، باش زبون ما يقدرش يعدّل الكوكي ويبدّل السعر.
"""

from flask import session

from models import db, Product, ProductVariant, Offer

CART_KEY = "cart"
MAX_QUANTITY_PER_ITEM = 50  # سقف معقول، يمنع أخطاء الإدخال والطلبات الوهمية


class CartLine:
    """سطر فالسلة — جاهز للعرض، بالأسعار الحقيقية من قاعدة البيانات."""

    def __init__(self, key, kind, name, unit_price, quantity,
                 image_filename=None, variant_label=None, slug=None,
                 product_id=None, variant_id=None, offer_id=None,
                 available_stock=None):
        self.key = key                  # المفتاح فالـsession
        self.kind = kind                # "product" أو "offer"
        self.name = name
        self.unit_price = unit_price
        self.quantity = quantity
        self.image_filename = image_filename
        self.variant_label = variant_label
        self.slug = slug
        self.product_id = product_id
        self.variant_id = variant_id
        self.offer_id = offer_id
        self.available_stock = available_stock

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    @property
    def line_total_display(self):
        return f"{self.line_total:,} دج"

    @property
    def unit_price_display(self):
        return f"{self.unit_price:,} دج"

    @property
    def exceeds_stock(self):
        """واش الكمية المطلوبة أكثر من المتوفر."""
        if self.available_stock is None:
            return False
        return self.quantity > self.available_stock


def _get_raw_cart():
    """السلة الخام من الـsession: {key: quantity}."""
    return session.get(CART_KEY, {})


def _save_raw_cart(cart):
    session[CART_KEY] = cart
    session.modified = True


def _make_key(kind, item_id, variant_id=None):
    """مفتاح فريد للسطر: 'product:12:3' أو 'offer:5'."""
    if kind == "offer":
        return f"offer:{item_id}"
    return f"product:{item_id}:{variant_id or 0}"


def _parse_key(key):
    """يفكّ المفتاح لأجزائه. يرجع (kind, item_id, variant_id) أو None إذا غير صالح."""
    parts = key.split(":")
    try:
        if parts[0] == "offer" and len(parts) == 2:
            return "offer", int(parts[1]), None
        if parts[0] == "product" and len(parts) == 3:
            variant_id = int(parts[2])
            return "product", int(parts[1]), (variant_id or None)
    except (ValueError, IndexError):
        pass
    return None


def add_to_cart(kind, item_id, variant_id=None, quantity=1):
    """
    يزيد عنصر للسلة. يرجع (True, None) أو (False, "سبب الرفض").
    نتحققو من التوفر هنا باش الزبون يعرف فورًا ماشي فالـcheckout.
    """
    quantity = max(1, min(quantity, MAX_QUANTITY_PER_ITEM))

    if kind == "offer":
        offer = db.session.get(Offer, item_id)
        if not offer or not offer.is_running:
            return False, "هذا العرض غير متوفر"
        if not offer.is_available:
            return False, "أحد منتجات هذه الباقة نفذ من المخزون"

        # نتأكدو أن الكمية المطلوبة من الباقة متاحة فعليًا
        existing = _get_raw_cart().get(_make_key(kind, item_id), 0)
        if offer.max_orderable < existing + quantity:
            return False, f"متوفر غير {offer.max_orderable} من هذه الباقة"
    else:
        product = Product.query.filter_by(id=item_id, is_active=True).first()
        if not product:
            return False, "هذا المنتج غير متوفر"

        if variant_id:
            variant = ProductVariant.query.filter_by(
                id=variant_id, product_id=product.id
            ).first()
            if not variant:
                return False, "التنويعة المختارة غير موجودة"
            if not variant.is_in_stock:
                return False, f"التنويعة «{variant.label}» نفذت من المخزون"
        elif not product.is_in_stock:
            return False, "هذا المنتج نفذ من المخزون"

    cart = _get_raw_cart()
    key = _make_key(kind, item_id, variant_id)
    new_qty = cart.get(key, 0) + quantity
    cart[key] = min(new_qty, MAX_QUANTITY_PER_ITEM)
    _save_raw_cart(cart)

    return True, None


def update_quantity(key, quantity):
    """يبدّل كمية سطر. كمية 0 أو أقل = حذف السطر."""
    cart = _get_raw_cart()
    if key not in cart:
        return False

    if quantity <= 0:
        del cart[key]
    else:
        cart[key] = min(quantity, MAX_QUANTITY_PER_ITEM)

    _save_raw_cart(cart)
    return True


def remove_from_cart(key):
    cart = _get_raw_cart()
    if key in cart:
        del cart[key]
        _save_raw_cart(cart)
        return True
    return False


def clear_cart():
    session.pop(CART_KEY, None)
    session.pop("coupon_code", None)
    session.modified = True


def get_cart_lines():
    """
    يبني قائمة CartLine بالبيانات الحقيقية من قاعدة البيانات.

    ⚠️ مهم: إذا عنصر انمسح من المتجر (أو تعطّل)، نحيّدوه من السلة بصمت هنا
    ونحدّثو الـsession — أحسن من ما نعرضو للزبون منتج ما نقدروش نبيعوه.
    """
    cart = _get_raw_cart()
    lines = []
    stale_keys = []

    for key, quantity in cart.items():
        parsed = _parse_key(key)
        if not parsed:
            stale_keys.append(key)
            continue

        kind, item_id, variant_id = parsed

        if kind == "offer":
            offer = db.session.get(Offer, item_id)
            if not offer or not offer.is_running or not offer.items:
                stale_keys.append(key)
                continue

            lines.append(CartLine(
                key=key, kind="offer", name=offer.title,
                unit_price=offer.bundle_price, quantity=quantity,
                image_filename=offer.banner_filename, slug=offer.slug,
                offer_id=offer.id,
                available_stock=offer.max_orderable,
            ))
        else:
            product = Product.query.filter_by(id=item_id, is_active=True).first()
            if not product:
                stale_keys.append(key)
                continue

            variant = None
            if variant_id:
                variant = ProductVariant.query.filter_by(
                    id=variant_id, product_id=product.id
                ).first()
                if not variant:
                    stale_keys.append(key)
                    continue

            unit_price = variant.effective_price if variant else product.price

            # 🔴 إصلاح حرج: التنويعة كانت تقرا stock_quantity مباشرة وتتجاهل
            # النظام المفتوح. المنتجات المستوردة كميتها 0، فأي طلبية بتنويعة
            # كانت تُرفض ("عدّل الكميات المعلّمة") وزر الطلب يبقى مقفول.
            if not Product.is_stock_tracked:
                stock = None                 # None = بلا حد
            elif variant:
                stock = variant.stock_quantity
            else:
                stock = product.available_quantity
            image = (variant.image_filename if variant and variant.image_filename
                     else product.image_filename)

            lines.append(CartLine(
                key=key, kind="product", name=product.name,
                unit_price=unit_price, quantity=quantity,
                image_filename=image, slug=product.slug,
                variant_label=variant.label if variant else None,
                product_id=product.id,
                variant_id=variant.id if variant else None,
                available_stock=stock,
            ))

    # تنظيف العناصر اللي ما بقاتش صالحة
    if stale_keys:
        for k in stale_keys:
            cart.pop(k, None)
        _save_raw_cart(cart)

    return lines


def get_cart_count():
    """
    عدد **المنتجات المختلفة** فالسلة — للأيقونة فالهيدر والزر العائم.

    ⚠️ ماشي مجموع الكميات: 3 قطع من منتج واحد = 1، ماشي 3.
    هاذي أوضح للزبون (يعرف شحال عندو من منتج مختلف).
    """
    return len(_get_raw_cart())


def get_cart_subtotal(lines=None):
    """مجموع أسعار المنتجات (قبل التوصيل والخصم)."""
    if lines is None:
        lines = get_cart_lines()
    return sum(line.line_total for line in lines)


def has_stock_issues(lines=None):
    """واش كاين سطر كميتو أكثر من المتوفر — نمنعو الطلب فهاد الحالة."""
    if lines is None:
        lines = get_cart_lines()
    return [line for line in lines if line.exceeds_stock]
