# -*- coding: utf-8 -*-
"""
اختبار تطابق أسعار التوصيل.

⚠️ علاش هاد الاختبار موجود؟

كانت عندنا ثغرة حقيقية: دالة التقريب `round_up_to_100` كانت مطبّقة
فحساب الشراء برك، وصفحة «أسعار التوصيل» تعرض الأرقام الخام.

النتيجة: الزبون يشوف **670 دج** فالجدول، ويدفع **700 دج** عند الشراء.
30 دج فرق ماشي كارثة، بصح الزبون يحس روحو تغشّ — وهذا يكلّفك أكثر
من 30 دج.

هاد الاختبار يتأكد أن **الرقم اللي يشوفو الزبون = الرقم اللي يدفعو**،
مهما تبدّل الكود من بعد.

التشغيل: python tests/test_shipping_display.py
"""
import os, sys, re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "test-key-for-testing-only")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import create_app
from models import db, Carrier, ShippingRate
from services.shipping import round_up_to_100, calculate_shipping

PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name
          + ("  → " + str(detail) if not ok and detail != "" else ""))


# أرقام حقيقية من جدول الأسعار — فيها حالات التقريب المهمة
SAMPLE = [
    # (رمز، اسم، منزل خام، مكتب خام، منزل متوقّع، مكتب متوقّع)
    ("01", "أدرار",       1400,  970, 1400, 1000),
    ("03", "الأغواط",      950,  670, 1000,  700),
    ("04", "أم البواقي",   500,  370,  500,  400),
    ("08", "بشار",        1050,  770, 1100,  800),
    ("09", "البليدة",      750,  520,  800,  600),
    ("11", "تمنراست",     1600, 1270, 1600, 1300),
]


def main():
    print("\n" + "=" * 60)
    print("تطابق أسعار التوصيل: الجدول ⇄ الشراء")
    print("=" * 60)

    app = create_app()

    with app.app_context():
        db.create_all()

        carrier = Carrier(name="ZR Express", code="zr", is_active=True)
        db.session.add(carrier)
        db.session.flush()

        for code, name, home, office, _eh, _eo in SAMPLE:
            db.session.add(ShippingRate(
                carrier_id=carrier.id, wilaya_code=code, wilaya_name=name,
                home_price=home, office_price=office,
            ))
        db.session.commit()

        # ───────── 1) الدالة نفسها ─────────
        print("\n[1] دالة التقريب")
        for _c, name, home, office, exp_home, exp_office in SAMPLE:
            check(f"{name}: منزل {home} ⇐ {exp_home}",
                  round_up_to_100(home) == exp_home, round_up_to_100(home))
            check(f"{name}: مكتب {office} ⇐ {exp_office}",
                  round_up_to_100(office) == exp_office, round_up_to_100(office))

        check("None يبقى None", round_up_to_100(None) is None)
        check("0 يبقى 0", round_up_to_100(0) == 0)
        check("100 يبقى 100 (ما يقفزش لـ200)", round_up_to_100(100) == 100)
        check("1 ⇐ 100", round_up_to_100(1) == 100)

        # ───────── 2) حساب الشراء ─────────
        print("\n[2] حساب الشراء")
        for code, name, _h, _o, exp_home, exp_office in SAMPLE:
            q = calculate_shipping("zr", code, "home")
            check(f"{name}: شراء للمنزل = {exp_home}", q.price == exp_home, q.price)

            q = calculate_shipping("zr", code, "office")
            check(f"{name}: شراء للمكتب = {exp_office}", q.price == exp_office, q.price)

        # ───────── 3) الجدول العمومي ─────────
        # ⚠️ هذا هو بيت القصيد: الصفحة اللي يشوفها الزبون.
        print("\n[3] صفحة «أسعار التوصيل»")
        client = app.test_client()
        page = client.get("/shipping-prices").get_data(as_text=True)

        check("الصفحة تتفتح", len(page) > 1000, len(page))

        # الأرقام الخام اللي **لازم ما تبانش** (اللي التقريب يبدّلها)
        must_not_show = {
            raw
            for _c, _n, home, office, _eh, _eo in SAMPLE
            for raw in (home, office)
            if round_up_to_100(raw) != raw
        }

        shown = set()
        for token in re.findall(r">\s*([\d,]{3,6})\s*<", page):
            digits = token.replace(",", "")
            if digits.isdigit():
                shown.add(int(digits))

        leaked = sorted(must_not_show & shown)
        check("ماكانش سعر خام غير مقرّب فالجدول",
              not leaked, f"تسرّبو: {leaked}")

        # وكل سعر مقرّب لازم **يبان**
        for _c, name, _h, _o, exp_home, exp_office in SAMPLE:
            check(f"{name}: {exp_home} يبان فالجدول", exp_home in shown)
            check(f"{name}: {exp_office} يبان فالجدول", exp_office in shown)

    print("\n" + "=" * 60)
    print(f"نجح: {len(PASSED)}  ·  فشل: {len(FAILED)}")
    print("=" * 60)
    if FAILED:
        for f in FAILED:
            print("  ❌ " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
