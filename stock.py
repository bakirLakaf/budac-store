"""
المخزون — مصدر وحيد للحقيقة.

════════════════════════════════════════════════════════════════
علاش هاد الملف موجود؟
────────────────────────────────────────────────────────────────
كان منطق المخزون مكرّرًا فـ4 أماكن: services/cart.py، services/orders.py،
routes/landing.py، وmodels.py. ثلاثة منهم كانو يحترمو العلم
`Product.is_stock_tracked`، والرابع (صفحات الهبوط) نساه.

النتيجة: `stock_quantity` قيمتها 0 لكل منتج (لأن التتبّع مطفي أصلاً)،
فالشرط `quantity > 0` كان صحيحًا دائمًا، وصفحات الهبوط كانت ترفض
**كل** طلبية برسالة «المخزون المتوفر: 0 فقط».

هذا ماشي غلطة إملائية — هو نتيجة حتمية لتكرار القاعدة. القاعدة درك
مكتوبة مرة وحدة هنا، وكل من يحتاجها يناديها.
════════════════════════════════════════════════════════════════

الاتفاقية: `None` = بلا حد (المخزون ماشي متتبّعًا)، ماشي 0.
هاد الفرق مهم — 0 يعني «نفد»، وNone يعني «ما نعدّوش».
"""

from models import Product

# نص واضح: كمية غير محدودة
UNLIMITED = None


def tracking_enabled():
    """
    واش تتبّع المخزون مفعّل؟

    ⚠️ العلم على الصنف (Product.is_stock_tracked) ماشي على الكائن —
    هو إعداد عام للمتجر كامل، ماشي خاصية لكل منتج. نلفّوه فدالة باش
    لو ولّى إعدادًا فقاعدة البيانات بعدين، يتبدّل هنا برك.
    """
    return bool(Product.is_stock_tracked)


def available_for(product, variant=None):
    """
    الكمية المتوفرة لمنتج (أو تنويعة منّو).

    يرجّع:
      • None  → بلا حد (التتبّع مطفي)
      • رقم   → الكمية الحقيقية
    """
    if not tracking_enabled():
        return UNLIMITED

    if variant is not None:
        return variant.stock_quantity or 0

    if product is None:
        return 0

    return product.available_quantity


def check_availability(product, variant=None, quantity=1, label=None):
    """
    واش نقدرو نبيعو `quantity` من هاد المنتج/التنويعة؟

    يرجّع رسالة خطأ بالعربية، أو None إذا كلش مليح.

    label : اسم يبان فرسالة الخطأ (اختياري — نستعملو اسم المنتج بلاه)
    """
    if quantity is None or quantity < 1:
        return "الكمية لازم تكون 1 على الأقل"

    name = label or (getattr(product, "name", None) or "هذا المنتج")

    # ⚠️ التعطيل اليدوي للتنويعة يغلب كلش — حتى إذا التتبّع مطفي.
    # الأدمن اللي يعلّم تنويعة «ماشي متوفرة» يقصد ما تنباعش، نقطة.
    if variant is not None and not getattr(variant, "is_available", True):
        return f"«{name} — {variant.label}» ماشي متوفر درك"

    if not tracking_enabled():
        return None

    stock = available_for(product, variant)
    if stock is UNLIMITED:
        return None

    if stock <= 0:
        return f"«{name}» نفد من المخزون"

    if quantity > stock:
        if variant is not None:
            return f"«{name} — {variant.label}» متوفر منو غير {stock}"
        return f"«{name}» متوفر منو غير {stock}"

    return None


def check_offer_availability(offer, quantity=1):
    """
    نفس الفحص لكن لباقة (عرض): الباقة متوفرة بقدر أقل منتج فيها.
    """
    if offer is None or not offer.is_running:
        return "هذا العرض غير متوفر"

    if not tracking_enabled():
        return None

    if not offer.is_available:
        return "أحد منتجات هذه الباقة نفذ من المخزون"

    max_orderable = offer.max_orderable
    if max_orderable is not None and quantity > max_orderable:
        return f"متوفر غير {max_orderable} من هذه الباقة"

    return None
