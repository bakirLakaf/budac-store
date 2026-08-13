"""
منطق إنشاء الطلبيات.

القرارات المهمة هنا:
1. نخصمو المخزون وقت إنشاء الطلبية (ماشي وقت التسليم) — باش ما نبيعوش نفس القطعة مرتين
2. نتحققو من التوفر **مرة أخرى** هنا حتى لو تحققنا فالسلة — لأن الوقت بين الاثنين
   يقدر يكون طويل وزبون آخر يقدر يشري نفس القطعة
3. كل شي يتم فمعاملة (transaction) وحدة — يا يتم كامل يا ما يتم والو
"""

import random
import string
from datetime import datetime

from models import (
    db, Order, OrderItem, Product, ProductVariant, Offer, Coupon,
)


class OrderError(Exception):
    """خطأ فإنشاء الطلبية — رسالتو تتعرض للزبون بالعربية."""
    pass


def generate_order_number():
    """
    رقم طلبية سهل القراءة والنطق فالتلفون: BD-XXXXXX
    نتأكدو أنو فريد قبل ما نرجعوه.
    """
    for _ in range(20):
        suffix = "".join(random.choices(string.digits, k=6))
        number = f"BD-{suffix}"
        if not Order.query.filter_by(order_number=number).first():
            return number

    # احتياط شبه مستحيل يوصلو، لكن أحسن من فشل صامت
    return f"BD-{int(datetime.utcnow().timestamp())}"


def reserve_stock(lines):
    """
    يتحقق من التوفر ويخصم المخزون.
    يرمي OrderError إذا شي حاجة ما بقاتش متوفرة.

    ⚠️ ما نديروش commit هنا — المستدعي هو اللي يديرو، باش لو فشل شي حاجة بعد،
    كل شي يترجع للوراء (rollback).
    """
    for line in lines:
        if line.kind == "offer":
            offer = db.session.get(Offer, line.offer_id)
            if not offer or not offer.is_running:
                raise OrderError(f"العرض «{line.name}» ما بقاش متوفر")

            # باقة = نخصمو من كل منتج فيها حسب كميتو × كمية الباقة.
            # نستعملو consume_stock باش يتعامل صح مع المنتجات ذات التنويعات.
            for item in offer.items:
                needed = item.quantity * line.quantity
                product = item.product
                if not product.consume_stock(needed):
                    raise OrderError(
                        f"«{product.name}» ما بقاش فيه كمية كافية للباقة «{offer.title}» "
                        f"(متوفر {product.available_quantity}، مطلوب {needed})"
                    )
            continue

        # منتج عادي بتنويعة محددة
        if line.variant_id:
            variant = db.session.get(ProductVariant, line.variant_id)
            if not variant:
                raise OrderError(f"التنويعة ديال «{line.name}» ما بقاتش موجودة")

            # 🔴 إصلاح حرج: فالنظام المفتوح (is_stock_tracked = False) ما
            # نتبعوش الكميات أصلاً. هاد السطر كان يقارن مع stock_quantity
            # (=0 للمنتجات المستوردة) ويرفض **كل** طلبية فيها تنويعة.
            if not Product.is_stock_tracked:
                continue

            if variant.stock_quantity < line.quantity:
                raise OrderError(
                    f"«{line.name} — {variant.label}» متوفر منو غير {variant.stock_quantity}"
                )
            variant.stock_quantity -= line.quantity
        else:
            product = db.session.get(Product, line.product_id)
            if not product:
                raise OrderError(f"«{line.name}» ما بقاش موجود")
            if not product.consume_stock(line.quantity):
                raise OrderError(
                    f"«{line.name}» متوفر منو غير {product.available_quantity}"
                )


def restore_stock(order):
    """
    يرجّع المخزون كي تتلغى طلبية.
    نستعملوه فلوحة التحكم كي الأدمن يلغي طلبية.
    """
    for item in order.items:
        if item.offer_id:
            offer = db.session.get(Offer, item.offer_id)
            if offer:
                for offer_item in offer.items:
                    if offer_item.product:
                        offer_item.product.restore_stock(offer_item.quantity * item.quantity)
            continue

        if item.variant_id:
            variant = db.session.get(ProductVariant, item.variant_id)
            if variant:
                variant.stock_quantity += item.quantity
        elif item.product_id:
            product = db.session.get(Product, item.product_id)
            if product:
                product.restore_stock(item.quantity)


def create_order(lines, customer_data, shipping_info, coupon=None, customer_note=None):
    """
    ينشئ طلبية كاملة.

    lines         : قائمة CartLine من services.cart
    customer_data : dict فيه name, phone, wilaya_code, wilaya_name, commune, address_note
    shipping_info : dict فيه carrier_code, carrier_name, delivery_type, price
    coupon        : كائن Coupon أو None
    customer_note : ملاحظة الزبون

    يرجع Order. يرمي OrderError إذا فشل.
    """
    if not lines:
        raise OrderError("السلة فارغة")

    subtotal = sum(line.line_total for line in lines)
    shipping_price = shipping_info.get("price") or 0

    discount_amount, shipping_discount = 0, 0
    if coupon:
        is_valid, reason = coupon.check_validity(subtotal)
        if not is_valid:
            raise OrderError(reason)
        discount_amount, shipping_discount = coupon.compute_discount(subtotal, shipping_price)

    total = subtotal + shipping_price - discount_amount - shipping_discount
    total = max(0, total)

    try:
        # نخصمو المخزون أولاً — إذا فشل، ما نخلقو حتى طلبية
        reserve_stock(lines)

        order = Order(
            order_number=generate_order_number(),
            customer_name=customer_data["name"],
            customer_phone=customer_data["phone"],
            wilaya_code=customer_data["wilaya_code"],
            wilaya_name=customer_data["wilaya_name"],
            commune=customer_data.get("commune"),
            commune_external_id=customer_data.get("commune_external_id"),
            address_note=customer_data.get("address_note"),
            carrier_code=shipping_info.get("carrier_code"),
            carrier_name=shipping_info.get("carrier_name"),
            delivery_type=shipping_info.get("delivery_type", "home"),
            subtotal=subtotal,
            shipping_price=shipping_price,
            discount_amount=discount_amount,
            shipping_discount=shipping_discount,
            total=total,
            coupon_code=coupon.code if coupon else None,
            customer_note=customer_note,
            status=Order.STATUS_PENDING,
        )
        db.session.add(order)
        db.session.flush()

        for line in lines:
            db.session.add(OrderItem(
                order_id=order.id,
                product_id=line.product_id,
                variant_id=line.variant_id,
                offer_id=line.offer_id,
                item_name=line.name,
                variant_label=line.variant_label,
                unit_price=line.unit_price,
                quantity=line.quantity,
                image_filename=line.image_filename,
            ))

        if coupon:
            coupon.used_count += 1

        db.session.commit()
        return order

    except OrderError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise OrderError(f"صار خطأ أثناء تسجيل الطلبية: {e}")


def find_coupon(code, subtotal):
    """
    يلقى كوبون بالكود ويتحقق من صلاحيتو.
    يرجع (Coupon, None) أو (None, "سبب الرفض").
    """
    if not code or not code.strip():
        return None, "اكتب كود الكوبون"

    coupon = Coupon.query.filter_by(code=code.strip().upper()).first()
    if not coupon:
        return None, "هذا الكود غير موجود"

    is_valid, reason = coupon.check_validity(subtotal)
    if not is_valid:
        return None, reason

    return coupon, None
