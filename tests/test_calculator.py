# -*- coding: utf-8 -*-
"""
فحص حاسبة الكمية.

🔴 هاد الملف ماكانش موجودًا — الحاسبة كانت تخدم بلا أي فحص.
لقينا فيها خللًا فالمنطق (نتيجة بـ«اللفات» على منتج يتباع بالمتر)،
وهاد النوع من الأخطاء يخسّر طلبيات: الزبون يحتار ويخرج.

التشغيل: python tests/test_calculator.py
"""
import os, sys, math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.calculators import (
    calculate_perimeter, calculate_area, CalculationError,
)

PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name
          + ("  → " + str(detail) if not ok and detail != "" else ""))


def main():
    print("\n" + "=" * 60)
    print("حاسبة المحيط")
    print("=" * 60)

    # ───────── الدائرة: المحيط = π × القطر ─────────
    r = calculate_perimeter("circle", {"diameter": 60}, margin_percent=0)
    check("دائرة قطر 60: المحيط 1.88 م",
          r["perimeter_per_piece_m"] == 1.88, r["perimeter_per_piece_m"])

    # ⚠️ نتأكدو أنها π الحقيقية ماشي 3.14 المقرّبة.
    # على قطر 100 سم الفرق 0.0016 م — يبان صغيرًا، لكن على 50 قطعة
    # يولّي 8 سم، وهي كافية باش الشريط ما يكملش.
    r = calculate_perimeter("circle", {"diameter": 100}, margin_percent=0)
    check("يستعمل math.pi ماشي 3.14",
          abs(r["perimeter_per_piece_m"] - math.pi) < 0.005,
          r["perimeter_per_piece_m"])

    # ───────── الأشكال الأخرى ─────────
    r = calculate_perimeter("square", {"side": 50}, margin_percent=0)
    check("مربع ضلع 50: المحيط 2 م", r["total_m"] == 2.0, r["total_m"])

    r = calculate_perimeter("rectangle", {"width": 80, "height": 40},
                            margin_percent=0)
    check("مستطيل 80×40: المحيط 2.4 م", r["total_m"] == 2.4, r["total_m"])

    # ───────── عدد القطع ─────────
    r = calculate_perimeter("circle", {"diameter": 60, "quantity": 3},
                            margin_percent=0)
    check("3 مرايا: المجموع 5.65 م", r["total_m"] == 5.65, r["total_m"])
    check("عدد القطع محفوظ فالنتيجة", r["quantity"] == 3)

    # ───────── الهامش ─────────
    r = calculate_perimeter("circle", {"diameter": 60, "quantity": 3},
                            margin_percent=10)
    check("هامش 10%: 5.65 → 6.22 م", r["with_margin_m"] == 6.22,
          r["with_margin_m"])

    # ───────── 🔴 النتيجة بالمتر، ماشي باللفات ─────────
    check("النتيجة فيها «متر يطلب»", "meters_to_order" in r, list(r))
    check("6.22 م → يطلب 7 متر (تقريب للأعلى)",
          r["meters_to_order"] == 7, r["meters_to_order"])
    check("🔴 ماكانش «عدد لفات» فالنتيجة",
          "rolls_needed" not in r and "roll_length_m" not in r, list(r))

    # التقريب دايمًا للأعلى — النقصان يوقف خدمة الزبون
    r = calculate_perimeter("circle", {"diameter": 32}, margin_percent=0)
    check("1.01 م → يطلب 2 متر (ما ننقصوش أبدًا)",
          r["meters_to_order"] >= r["with_margin_m"],
          (r["with_margin_m"], r["meters_to_order"]))

    # ───────── المدخلات الفاسدة ─────────
    print("\n" + "=" * 60)
    print("المدخلات الفاسدة")
    print("=" * 60)

    for label, shape, dims in [
        ("قطر بحرف", "circle", {"diameter": "شوية"}),
        ("قطر صفر", "circle", {"diameter": 0}),
        ("قطر سالب", "circle", {"diameter": -50}),
        ("قطر خيالي (20000)", "circle", {"diameter": 20000}),
        ("شكل مجهول", "triangle", {"side": 10}),
        ("قطر ناقص", "circle", {}),
    ]:
        try:
            calculate_perimeter(shape, dims)
            check(f"{label}: يرفض", False, "عدّى بلا خطأ")
        except CalculationError as ex:
            # ⚠️ الرسالة تبان للزبون — لازم تكون عربية ومفهومة
            check(f"{label}: يرفض برسالة عربية",
                  any("\u0600" <= c <= "\u06FF" for c in str(ex)), str(ex))

    print("\n" + "=" * 60)
    print("حاسبة المساحة (ورق لاصق)")
    print("=" * 60)

    r = calculate_area([{"width": 100, "height": 200}], roll_width_m=1.0,
                       margin_percent=0)
    check("سطح 1×2 م بلفة عرض 1 م: 2 متر طولي",
          r["total_length_m"] == 2.0, r["total_length_m"])

    # سطح أعرض من اللفة يحتاج شرائط جنب بعض
    r = calculate_area([{"width": 250, "height": 200}], roll_width_m=1.0,
                       margin_percent=0)
    check("سطح عرضو 2.5 م: 3 شرائط = 6 متر",
          r["total_length_m"] == 6.0, r["total_length_m"])

    try:
        calculate_area([], roll_width_m=1.0)
        check("بلا أسطح: يرفض", False)
    except CalculationError:
        check("بلا أسطح: يرفض", True)

    try:
        calculate_area([{"width": 100, "height": 100}], roll_width_m=0)
        check("عرض لفة ناقص: يرفض", False)
    except CalculationError:
        check("عرض لفة ناقص: يرفض", True)

    print("\n" + "=" * 60)
    print(f"نجح: {len(PASSED)}  ·  فشل: {len(FAILED)}")
    print("=" * 60)
    if FAILED:
        for f in FAILED:
            print("  ❌ " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
