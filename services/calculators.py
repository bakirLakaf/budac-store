"""
حاسبات الكمية — تحسب للزبون قداش يحتاج بدل ما يخمّن.

نوعان:

1. **المحيط (perimeter)** — للأشرطة اللي تدور حول حاجة:
   جوان ميروار، السفيفة، أشرطة الكريستال...
   الزبون يعطي أبعاد المرآة (دائرية / مربعة / مستطيلة)، ونحسبو المحيط.

2. **المساحة (area)** — للورق اللاصق اللي يغطي سطح:
   الزبون يعطي الطول والعرض (ولا عدة قطع)، ونحسبو كم لفة يحتاج.

⚠️ قرار تصميم مهم: كل الحسابات **فالسيرفر**، ماشي فالجافاسكريبت فقط.
السبب: نفس المنطق يستعمل فصفحة المنتج (عبر API) وفلوحة التحكم (للتحقق)،
وبلا تكرار نتجنبو أن الاثنين يعطيو نتائج مختلفة.
"""

import math

# نوعا الحاسبة
CALC_PERIMETER = "perimeter"
CALC_AREA = "area"

CALCULATOR_LABELS = {
    CALC_PERIMETER: "حاسبة المحيط (أشرطة وحواف)",
    CALC_AREA: "حاسبة المساحة (ورق لاصق)",
}

# أشكال المرايا المدعومة فحاسبة المحيط
SHAPE_CIRCLE = "circle"
SHAPE_SQUARE = "square"
SHAPE_RECTANGLE = "rectangle"


class CalculationError(Exception):
    """خطأ فالمدخلات — رسالتو تتعرض للزبون بالعربية."""
    pass


def _to_float(value, field_name):
    """يحول مدخل لرقم عشري، برسالة خطأ واضحة إذا فشل."""
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        raise CalculationError(f"«{field_name}» لازمو يكون رقم")

    if number <= 0:
        raise CalculationError(f"«{field_name}» لازمو يكون أكبر من صفر")

    # سقف معقول — يمنع أخطاء الإدخال (واحد يكتب 5000 بدل 50)
    if number > 10000:
        raise CalculationError(f"«{field_name}» كبير بزاف — تأكد من الوحدة")

    return number


def calculate_perimeter(shape, dimensions, margin_percent=10):
    """
    يحسب كم متر يحتاج الزبون.

    🔴 كان هنا حساب «عدد اللفات» — اتحيّد.
    السبب: المنتج يتباع **بالمتر**، والزبون يطلب أمتار ماشي لفات.
    الحساب القديم كان يقول «اطلب 2 لفة» على 6.22 متر، والزبون
    ما يفهمش واش هي اللفة ولا قداش فيها. درك: رقم واحد بالمتر.

    shape      : "circle" / "square" / "rectangle"
    dimensions : dict بالسنتيمتر — حسب الشكل:
                   circle    → {"diameter": 60}
                   square    → {"side": 50}
                   rectangle → {"width": 80, "height": 40}
    margin_percent: هامش أمان %

    يرجع dict فيه التفاصيل كاملة.
    """
    if shape == SHAPE_CIRCLE:
        diameter_cm = _to_float(dimensions.get("diameter"), "القطر")
        perimeter_cm = math.pi * diameter_cm
        shape_label = f"دائرية (قطر {diameter_cm:g} سم)"

    elif shape == SHAPE_SQUARE:
        side_cm = _to_float(dimensions.get("side"), "طول الضلع")
        perimeter_cm = 4 * side_cm
        shape_label = f"مربعة ({side_cm:g}×{side_cm:g} سم)"

    elif shape == SHAPE_RECTANGLE:
        width_cm = _to_float(dimensions.get("width"), "العرض")
        height_cm = _to_float(dimensions.get("height"), "الطول")
        perimeter_cm = 2 * (width_cm + height_cm)
        shape_label = f"مستطيلة ({width_cm:g}×{height_cm:g} سم)"

    else:
        raise CalculationError("اختر شكل المرآة")

    # عدد القطع (لو عندو أكثر من مرآة بنفس المقاس)
    quantity = int(_to_float(dimensions.get("quantity", 1), "عدد القطع"))
    quantity = max(1, min(quantity, 500))

    perimeter_m = perimeter_cm / 100
    total_m = perimeter_m * quantity

    # هامش الأمان: القص والأخطاء
    with_margin_m = total_m * (1 + margin_percent / 100)

    return {
        "shape_label": shape_label,
        "quantity": quantity,
        "perimeter_per_piece_m": round(perimeter_m, 2),
        "total_m": round(total_m, 2),
        "with_margin_m": round(with_margin_m, 2),
        "margin_percent": margin_percent,
        # ⚠️ نقرّبو للأعلى لأقرب متر — نفس منطق حاسبة المساحة.
        # 6.22 متر تولّي 7: الزبون ما يقدرش يطلب كسر متر، والنقصان
        # أسوأ من الزيادة (يوقف الخدمة ويعاود يطلب).
        "meters_to_order": math.ceil(with_margin_m),
    }


def calculate_area(surfaces, roll_width_m, margin_percent=10):
    """
    يحسب المساحة وعدد الأمتار الطولية من الورق اللاصق.

    surfaces   : لائحة dicts بالسنتيمتر:
                 [{"width": 100, "height": 200, "quantity": 2}, ...]
    roll_width_m : عرض اللفة بالمتر (1.0 أو 1.2)
    margin_percent: هامش أمان %

    ⚠️ الحساب ماشي مجرد "المساحة ÷ عرض اللفة".
    الورق اللاصق يتقص **بالطول** من لفة عرضها ثابت. فلكل سطح، نحسبو كم متر
    طولي يلزم: إذا عرض السطح أصغر من عرض اللفة، قطعة وحدة تكفي بطول = ارتفاع
    السطح. وإذا أكبر، نحتاجو عدة شرائط جنب بعض.
    """
    if not surfaces:
        raise CalculationError("زيد على الأقل سطح واحد")

    if not roll_width_m or roll_width_m <= 0:
        raise CalculationError("عرض اللفة غير محدد لهذا المنتج")

    details = []
    total_length_m = 0.0
    total_area_m2 = 0.0

    for i, surface in enumerate(surfaces[:20], 1):  # سقف 20 سطح
        width_cm = _to_float(surface.get("width"), f"عرض السطح {i}")
        height_cm = _to_float(surface.get("height"), f"ارتفاع السطح {i}")
        quantity = int(_to_float(surface.get("quantity", 1), f"عدد قطع السطح {i}"))
        quantity = max(1, min(quantity, 200))

        width_m = width_cm / 100
        height_m = height_cm / 100

        # كم شريط بعرض اللفة نحتاجو باش نغطيو عرض السطح
        strips = math.ceil(width_m / roll_width_m)

        # الطول المطلوب من اللفة لهاد السطح
        length_needed_m = strips * height_m * quantity

        total_length_m += length_needed_m
        total_area_m2 += width_m * height_m * quantity

        details.append({
            "index": i,
            "label": f"{width_cm:g}×{height_cm:g} سم" + (f" ×{quantity}" if quantity > 1 else ""),
            "strips": strips,
            "length_m": round(length_needed_m, 2),
        })

    with_margin_m = total_length_m * (1 + margin_percent / 100)

    return {
        "surfaces": details,
        "roll_width_m": roll_width_m,
        "total_area_m2": round(total_area_m2, 2),
        "total_length_m": round(total_length_m, 2),
        "with_margin_m": round(with_margin_m, 2),
        "margin_percent": margin_percent,
        # نقرّبو للأعلى لأقرب متر — عادة يتباع بالمتر الكامل
        "meters_to_order": math.ceil(with_margin_m),
    }
