"""
منطق حساب التوصيل — مركزي فمكان واحد، باش أي جزء من الموقع (صفحة منتج، السلة،
الـcheckout، لوحة التحكم) يحسب بنفس القواعد بلا تكرار كود.

قاعدة مهمة فالتصميم:
سعر = None معناه "الخدمة غير متوفرة" (حالة صحيحة ومعروفة).
هذا مختلف تمامًا عن "ما لقيناش سطر السعر" (حالة بيانات ناقصة).
الدالة ترجع النتيجة بشكل واضح يفرّق بين الحالتين، باش الواجهة تعرض رسالة صحيحة
بدل ما تخمّن.
"""

from models import Carrier, ShippingRate

# نتائج ممكنة لحساب التوصيل
STATUS_OK = "ok"                        # عندنا سعر
STATUS_UNAVAILABLE = "unavailable"      # الشركة ما تخدمش هاد الولاية/النوع
STATUS_NO_DATA = "no_data"              # ما لقيناش بيانات (سطر ناقص فالجدول)
STATUS_FREE = "free"                    # توصيل مجاني (عرض/شرط)


class ShippingQuote:
    """نتيجة حساب سعر التوصيل — واضحة وسهلة الاستعمال فالقوالب."""

    def __init__(self, status, price=None, message=None, carrier_name=None):
        self.status = status
        self.price = price
        self.message = message
        self.carrier_name = carrier_name

    @property
    def is_ok(self):
        return self.status in (STATUS_OK, STATUS_FREE)

    @property
    def price_display(self):
        if self.status == STATUS_FREE:
            return "مجاني"
        if self.price is None:
            return self.message or "غير متوفر"
        return f"{self.price:,} دج"

    def __repr__(self):
        return f"<ShippingQuote {self.status} {self.price}>"


def round_up_to_100(price):
    """
    يقرّب السعر للأعلى لأقرب 100 دج (470 ⇐ 500، 970 ⇐ 1000).
    نستعملو نفس القاعدة اللي كانت فسكريبت YouCan، باش الأسعار تبقى متطابقة.
    """
    if price is None:
        return None
    return -(-int(price) // 100) * 100  # تقريب للأعلى بلا math.ceil (أدق مع الأعداد الصحيحة)


def get_active_carriers():
    """كل شركات التوصيل المفعّلة، مرتبة بالاسم."""
    return Carrier.query.filter_by(is_active=True).order_by(Carrier.name).all()

def calculate_shipping(carrier_code, wilaya_code, delivery_type="home", is_free=False):
    """
    الدالة الرئيسية: تحسب سعر التوصيل.

    carrier_code : "zrexpress" / "worldexpress" / "yalidine"
    wilaya_code  : "01" ... "58"
    delivery_type: "home" (للمنزل) أو "office" (للمكتب)
    is_free      : True إذا العرض/الشرط يخلي التوصيل مجاني

    ترجع ShippingQuote.
    """
    if is_free:
        return ShippingQuote(STATUS_FREE, price=0, message="توصيل مجاني")

    if not carrier_code or not wilaya_code:
        return ShippingQuote(STATUS_NO_DATA, message="اختر الولاية وشركة التوصيل")

    carrier = Carrier.query.filter_by(code=carrier_code, is_active=True).first()
    if not carrier:
        return ShippingQuote(STATUS_NO_DATA, message="شركة التوصيل غير معروفة")

    rate = ShippingRate.query.filter_by(
        carrier_id=carrier.id, wilaya_code=wilaya_code
    ).first()

    if not rate:
        # بيانات ناقصة — ماشي نفس الشي كي الخدمة غير متوفرة
        return ShippingQuote(
            STATUS_NO_DATA,
            message="السعر ماهوش متوفر درك، راسلنا ونعطيوك السعر",
            carrier_name=carrier.name,
        )

    raw_price = rate.price_for(delivery_type)

    if raw_price is None:
        type_label = "التوصيل للمكتب" if delivery_type == "office" else "التوصيل للمنزل"
        return ShippingQuote(
            STATUS_UNAVAILABLE,
            message=f"{type_label} غير متوفر في {rate.wilaya_name} عبر {carrier.name}",
            carrier_name=carrier.name,
        )

    return ShippingQuote(
        STATUS_OK,
        price=round_up_to_100(raw_price),
        carrier_name=carrier.name,
    )
