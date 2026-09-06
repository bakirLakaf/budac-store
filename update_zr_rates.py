# -*- coding: utf-8 -*-
"""
يحدّث أسعار ZR Express من ورقة الأسعار الرسمية.

═══════════ المشكل ═══════════

26 ولاية من 53 كانو غالطين فقاعدة البيانات، ومعظمهم **أرخص من
الحقيقة** — يعني خسارة فكل طلبية:

    قسنطينة : عندنا 600  ·  الحقيقة 700   ← خسارة 100 دج
    خنشلة   : عندنا 600  ·  الحقيقة 700   ← خسارة 100 دج
    الوادي  : عندنا 850  ·  الحقيقة 950   ← خسارة 100 دج

═══════════ التشغيل ═══════════

    python update_zr_rates.py --dry-run    # يوريك واش راح يتبدّل
    python update_zr_rates.py              # يطبّق

⚠️ يمسّ **ZR Express برك**. World Express ما يتلمسش.

⚠️ الأسعار هنا **خام** كيما اتفقت مع ZR. المتجر يقرّبها للمئة
   الأعلى وقت العرض والشراء (round_up_to_100)، ففي الجدول العمومي
   تبان 700 دج لسعر 670.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from models import db, Carrier, ShippingRate


# ═══════════════════════════════════════════════════════════
#  أسعار ZR Express الرسمية — (منزل، مكتب)
#
#  office = None  →  التوصيل للمكتب **غير متوفر** فهاد الولاية.
#  ZR يعرضها بـ0 دج، وهذا معناه ماكانش مكتب ماشي مجاني.
#  لو خلّيناها 0، الزبون يختار المكتب ويدفع 0 دج توصيل.
# ═══════════════════════════════════════════════════════════

ZR_RATES = {
    "01": (1400, 970),    # أدرار
    "02": (800, 520),     # الشلف
    "03": (950, 670),     # الأغواط
    "05": (700, 520),     # باتنة
    "06": (750, 520),     # بجاية
    "07": (850, 670),     # بسكرة
    "08": (1100, 720),    # بشار
    "09": (750, 520),     # البليدة
    "10": (750, 520),     # البويرة
    "11": (1600, 1120),   # تمنراست
    "12": (800, 520),     # تبسة
    "13": (900, 570),     # تلمسان
    "14": (800, 520),     # تيارت
    "15": (700, 520),     # تيزي وزو
    "16": (600, 520),     # الجزائر
    "17": (900, 670),     # الجلفة
    "18": (750, 520),     # جيجل
    "19": (800, 520),     # سطيف
    "20": (800, 570),     # سعيدة
    "21": (750, 520),     # سكيكدة
    "22": (800, 520),     # سيدي بلعباس
    "23": (700, 520),     # عنابة
    "24": (700, 520),     # قالمة
    "25": (700, 520),     # قسنطينة
    "26": (800, 520),     # المدية
    "27": (800, 520),     # مستغانم
    "28": (800, 570),     # المسيلة
    "29": (800, 520),     # معسكر
    "30": (950, 670),     # ورقلة
    "31": (800, 520),     # وهران
    "32": (1100, 670),    # البيض
    "34": (750, 520),     # برج بوعريريج
    "35": (750, 520),     # بومرداس
    "36": (750, 520),     # الطارف
    "38": (800, 520),     # تيسمسيلت
    "39": (950, 670),     # الوادي
    "40": (700, 450),     # خنشلة
    "41": (700, 520),     # سوق أهراس
    "42": (800, 520),     # تيبازة
    "43": (750, 520),     # ميلة
    "44": (750, 520),     # عين الدفلى
    "45": (1100, 670),    # النعامة
    "46": (800, 520),     # عين تموشنت
    "47": (950, 670),     # غرداية
    "48": (800, 520),     # غليزان
    "49": (1400, 970),    # تيميمون
    "51": (950, 670),     # أولاد جلال
    "52": (1200, 970),    # بني عباس
    "53": (1600, 1120),   # عين صالح
    "54": (1600, None),   # عين قزام — ماكانش مكتب
    "55": (950, 670),     # تقرت
    "57": (950, None),    # المغير — ماكانش مكتب
    "58": (1000, 670),    # المنيعة
}


class C:
    OK = "\033[92m"
    WARN = "\033[93m"
    ERR = "\033[91m"
    INFO = "\033[96m"
    OFF = "\033[0m"


def fmt(value):
    return "—" if value is None else str(value)


def run():
    parser = argparse.ArgumentParser(description="تحديث أسعار ZR Express")
    parser.add_argument("--dry-run", action="store_true", help="تجربة بلا كتابة")
    args = parser.parse_args()

    app = create_app()

    with app.app_context():
        # ⚠️ الرمز الصحيح «zrexpress» ماشي «zr» — باقي المشروع كامل
        # يستعملو (services/zrexpress.py · routes/admin.py). نجرّبو
        # البدائل باش السكريبت ما يطيحش لو تبدّل الرمز.
        carrier = None
        for code in ("zrexpress", "zr", "zr_express"):
            carrier = Carrier.query.filter_by(code=code).first()
            if carrier:
                break

        if carrier is None:
            print(f"{C.ERR}[X]{C.OFF} ما لقيناش شركة ZR Express.")
            print("    الشركات الموجودة عندك:")
            for c in Carrier.query.all():
                print(f"      {c.code}  —  {c.name}")
            return 1

        print("=" * 66)
        print(f"  الشركة : {carrier.name}")
        print(f"  الوضع  : {'تجربة (بلا كتابة)' if args.dry_run else 'تحديث'}")
        print("=" * 66)
        print()

        changed = []
        same = 0
        missing = []

        for code, (home, office) in sorted(ZR_RATES.items()):
            rate = ShippingRate.query.filter_by(
                carrier_id=carrier.id, wilaya_code=code
            ).first()

            if rate is None:
                missing.append(code)
                continue

            if rate.home_price == home and rate.office_price == office:
                same += 1
                continue

            changed.append((code, rate.wilaya_name,
                            rate.home_price, rate.office_price, home, office))

            if not args.dry_run:
                rate.home_price = home
                rate.office_price = office

        # ---------- التقرير ----------
        if changed:
            print(f"{'الولاية':<20} {'قديم':<16} {'جديد':<16}")
            print("─" * 66)
            for code, name, old_h, old_o, new_h, new_o in changed:
                old = f"{fmt(old_h)}/{fmt(old_o)}"
                new = f"{fmt(new_h)}/{fmt(new_o)}"
                label = f"{code} {name or ''}"[:19]
                print(f"{label:<20} {old:<16} {new:<16}")

        # ⚠️ commit واحد فالآخر: يا كل الأسعار تتحدّث يا والو. نصف
        # تحديث أسوأ من بلا تحديث — تبقى ما تعرفش وين وقف.
        if args.dry_run:
            db.session.rollback()
        else:
            db.session.commit()

        print()
        print("=" * 66)
        print(f"  اتبدّلو : {len(changed)}")
        print(f"  كيما هي : {same}")
        if missing:
            print(f"  ماكانتش فقاعدة البيانات: {len(missing)} → {', '.join(missing)}")
        print("=" * 66)

        # ---------- الولايات اللي ماشي فورقة ZR ----------
        others = (
            ShippingRate.query
            .filter_by(carrier_id=carrier.id)
            .filter(~ShippingRate.wilaya_code.in_(list(ZR_RATES)))
            .all()
        )

        if others:
            print(f"\n{C.WARN}[!]{C.OFF} ولايات ما بانتش فورقة أسعار ZR — "
                  "ما لمسناهاش:")
            for rate in sorted(others, key=lambda r: r.wilaya_code):
                print(f"    {rate.wilaya_code} {rate.wilaya_name or '':<16}"
                      f" منزل {fmt(rate.home_price)} · مكتب {fmt(rate.office_price)}")
            print("    راجعهم بنفسك: يا ZR ما يخدمهمش، يا الورقة ناقصة.")

        if args.dry_run:
            print("\nما اتكتب والو. للتطبيق: python update_zr_rates.py")
        elif changed:
            print("\nالخطوة الجاية: افتح صفحة «أسعار التوصيل» فالمتجر")
            print("وتأكد أن الأرقام الجديدة تبان.")

    return 0


if __name__ == "__main__":
    sys.exit(run())
