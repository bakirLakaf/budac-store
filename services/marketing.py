"""
السلات المتروكة والاقتراحات (Upsell).

فلسفة السلات المتروكة هنا مختلفة عن YouCan:
هما يبعثو إيميل تلقائي. فالجزائر الإيميل ضعيف — بزاف من الزبائن ما عندهمش
إيميل نشط، والرسائل تروح للسبام.

نحنا نسجّلو **رقم الهاتف** بمجرد ما الزبون يكتبو فالـcheckout، والأدمن يتصل
ولا يبعث واتساب برسالة جاهزة. نسبة الاستجابة أعلى بكثير.
"""

import json

from models import db, AbandonedCart, UpsellRule
from utils import normalize_algerian_phone


def phone_key(phone):
    """
    مفتاح مطابقة الهاتف: الرقم الموحّد (0XXXXXXXXX).

    ⚠️ علاش ماشي النص كيما هو؟
    نفس الزبون يكتب رقمو بأشكال مختلفة فنفس الجلسة:
      «0666255200» · «0666 25 52 00» · «+213666255200»
    والمقارنة النصية تشوفهم 3 أشخاص مختلفين — فالسلة تتكرّر،
    وكي يكمّل الطلبية وحدة برك تتنحّى والباقي يبقى فقائمة المتابعة.

    نرجّعو None للأرقام الناقصة ولا الغالطة: رقم نصّو ما ينفع لا
    للمطابقة ولا للاتصال بيه.
    """
    return normalize_algerian_phone(phone)


def _find_open_carts_by_phone(phone):
    """السلات المفتوحة (جديدة/تواصلنا) تاع هاد الرقم."""
    key = phone_key(phone)
    if not key:
        return []

    candidates = (
        AbandonedCart.query
        .filter(AbandonedCart.status.in_([
            AbandonedCart.STATUS_NEW,
            AbandonedCart.STATUS_CONTACTED,
        ]))
        .all()
    )
    return [c for c in candidates if phone_key(c.customer_phone) == key]


def save_abandoned_cart(customer_data, lines, total):
    """
    يسجّل (أو يحدّث) سلة متروكة.

    ⚠️ نستعملو رقم الهاتف كمفتاح: إذا نفس الزبون عاود جرّب، نحدّثو السطر
    الموجود بدل ما نخلقو واحد جديد — باش الأدمن ما يلقاش 5 أسطر لنفس الشخص.

    يرجع AbandonedCart أو None إذا البيانات ناقصة.
    """
    phone = (customer_data.get("phone") or "").strip()
    if not phone or not lines:
        return None

    snapshot = [
        {
            "name": line.name,
            "variant": line.variant_label,
            "quantity": line.quantity,
            "price": line.unit_price,
        }
        for line in lines
    ]

    # ⚠️ كانت المقارنة نصية حرفية هنا — وهي سبب تكرار الأسطر:
    # الحفظ يتنادى وقت الكتابة، فكل صيغة تاع الرقم تولّد سطرًا جديدًا.
    open_carts = _find_open_carts_by_phone(phone)
    existing = open_carts[0] if open_carts else None

    if existing:
        # لو تولّدو أسطر مكرّرة من قبل (بيانات قديمة)، ندمجوهم درك
        for duplicate in open_carts[1:]:
            db.session.delete(duplicate)

        existing.customer_name = customer_data.get("name") or existing.customer_name
        existing.wilaya_name = customer_data.get("wilaya_name") or existing.wilaya_name
        existing.commune = customer_data.get("commune") or existing.commune
        existing.cart_json = json.dumps(snapshot, ensure_ascii=False)
        existing.cart_total = total
        existing.item_count = sum(line.quantity for line in lines)
        db.session.commit()
        return existing

    cart = AbandonedCart(
        customer_name=customer_data.get("name"),
        customer_phone=phone,
        wilaya_name=customer_data.get("wilaya_name"),
        commune=customer_data.get("commune"),
        cart_json=json.dumps(snapshot, ensure_ascii=False),
        cart_total=total,
        item_count=sum(line.quantity for line in lines),
        status=AbandonedCart.STATUS_NEW,
    )
    db.session.add(cart)
    db.session.commit()
    return cart


def get_upsell_suggestions(cart_lines, limit=3):
    """
    يقترح منتجات حسب محتوى السلة.

    الأولوية:
      1. قواعد مخصصة (منتج X → اقترح Y)
      2. قواعد عامة (اقتراح لكل السلات)

    نستثنيو المنتجات اللي أصلاً فالسلة — ما فايدة نقترحو حاجة عندو منها.
    """
    if not cart_lines:
        return []

    in_cart_ids = {line.product_id for line in cart_lines if line.product_id}

    suggestions = []
    seen = set()

    def add_rules(rules):
        for rule in rules:
            product = rule.suggested_product
            if not product or not product.is_active:
                continue
            if product.id in in_cart_ids or product.id in seen:
                continue

            seen.add(product.id)
            suggestions.append({
                "product": product,
                "discount_percent": rule.discount_percent,
                "final_price": (
                    int(product.price * (100 - rule.discount_percent) / 100)
                    if rule.discount_percent else product.price
                ),
            })
            if len(suggestions) >= limit:
                return True
        return False

    # 1) قواعد مخصصة لمنتجات السلة
    if in_cart_ids:
        specific = (
            UpsellRule.query
            .filter(
                UpsellRule.trigger_product_id.in_(in_cart_ids),
                UpsellRule.is_active.is_(True),
            )
            .order_by(UpsellRule.sort_order)
            .all()
        )
        if add_rules(specific):
            return suggestions

    # 2) قواعد عامة
    general = (
        UpsellRule.query
        .filter(
            UpsellRule.trigger_product_id.is_(None),
            UpsellRule.is_active.is_(True),
        )
        .order_by(UpsellRule.sort_order)
        .all()
    )
    add_rules(general)

    return suggestions


def mark_cart_recovered(phone, order_number=None):
    """
    ينحّي السلة المتروكة كي يكمّل صاحبها الطلبية.

    ⚠️ المشكل اللي تحلّ: الزبون يعمّر اسمو وهاتفو → تتسجّل سلة متروكة
    → يكمّل الطلبية بعد ثانيتين → **السلة تبقى فقائمة المتابعة**.
    الأدمن يتصل بيه ويقولو «كمّل طلبك» وهو خلاص كمّلو. محرج للاثنين،
    ووقت ضائع فقائمة نصفها وهمي.

    ⚠️ علاش نعلّموها «استُرجعت» بدل ما نمسحوها؟
    المسح يخسّر إحصائية مفيدة: كم سلة تحوّلت لطلبية فعلاً. وقائمة
    الأدمن تعرض «الجديدة» افتراضيًا، فالسلة تختفي من نظرو على أي حال —
    وهذا هو المطلوب. لو حبيت المسح النهائي، الزر موجود فاللوحة.

    نعلّمو **كل** السلات المفتوحة تاع هاد الرقم، ماشي وحدة: ممكن
    تكون عندو سلة قديمة «تواصلنا معه» وأخرى جديدة.

    يرجّع عدد السلات اللي اتعلّمو.
    """
    open_carts = _find_open_carts_by_phone(phone)

    marked = 0
    for cart in open_carts:
        cart.status = AbandonedCart.STATUS_RECOVERED
        if order_number:
            note = f"كمّل الطلبية {order_number}"
            cart.admin_note = f"{cart.admin_note}\n{note}" if cart.admin_note else note
        marked += 1

    if marked:
        db.session.commit()

    return marked
