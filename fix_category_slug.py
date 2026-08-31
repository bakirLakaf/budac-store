# -*- coding: utf-8 -*-
"""
يصلّح slug تاع التصنيف الجديد: من عربي → لاتيني.

المشكل:
    السكريبت تاع الاستيراد استعمل slugify() على الاسم العربي، فطلع:
        هدايا-وأدوات-قابلة-للنقش
    وكل تصنيفاتك القدام لاتينية (handles-knobs, ribbons-edges...).

    الرابط العربي يخدم، بصح يولّي مرمّز فالمتصفح:
        /category/%D9%87%D8%AF%D8%A7%D9%8A%D8%A7-%D9%88...
    قبيح فالمشاركة على واتساب وفيسبوك، ويخربق فجوجل.

⚠️ التبديل آمن **درك برك** — التصنيف جديد، ماكانش رابط قديم منشور
   ولا جوجل فهرسو. بعد أسابيع، تبديل الـslug يكسر روابط.

التشغيل:
    python fix_category_slug.py --dry-run
    python fix_category_slug.py
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from models import db, Category, Product


# ═══════════════════════════════════════════════════════════
#  التصحيحات: اسم التصنيف → الـslug اللاتيني الجديد
#
#  اخترنا `engraving-gifts` باش يمشي مع نسق تصنيفاتك:
#     handles-knobs · ribbons-edges · incense-accessories
# ═══════════════════════════════════════════════════════════

FIXES = {
    "هدايا وأدوات قابلة للنقش": "engraving-gifts",
}


class C:
    OK = "\033[92m"
    WARN = "\033[93m"
    ERR = "\033[91m"
    INFO = "\033[96m"
    OFF = "\033[0m"


def is_ascii_slug(value):
    """يتحقق واش الـslug لاتيني كامل."""
    try:
        value.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def run():
    parser = argparse.ArgumentParser(description="تصحيح slug التصنيفات")
    parser.add_argument("--dry-run", action="store_true", help="تجربة بلا كتابة")
    args = parser.parse_args()

    app = create_app()

    with app.app_context():
        print("=" * 60)
        print(f"  الوضع: {'تجربة (بلا كتابة)' if args.dry_run else 'تطبيق'}")
        print("=" * 60)
        print()

        changed = 0

        # ---------- التصحيحات المحدّدة ----------
        for name, new_slug in FIXES.items():
            category = Category.query.filter_by(name=name).first()

            if category is None:
                print(f"{C.WARN}[!]{C.OFF} ما لقيناش تصنيف اسمو: {name}")
                continue

            print(f"{C.INFO}▸ {category.name}{C.OFF}")
            print(f"    القديم : {category.slug}")

            if category.slug == new_slug:
                print(f"    {C.OK}[OK]{C.OFF} مصحّح ديجا")
                continue

            # ⚠️ نتأكدو ماكانش تصنيف آخر واخذ نفس الـslug، وإلا
            #    قاعدة البيانات ترفض (الـslug فريد).
            twin = Category.query.filter(
                Category.slug == new_slug, Category.id != category.id
            ).first()

            if twin:
                print(f"{C.ERR}    [X]{C.OFF} «{new_slug}» مستعمل ديجا من «{twin.name}»")
                continue

            count = Product.query.filter_by(category_id=category.id).count()
            print(f"    الجديد : {new_slug}")
            print(f"    المنتجات: {count}  (ما تتلمسش — مربوطين بالـid ماشي بالـslug)")

            if not args.dry_run:
                category.slug = new_slug

            print(f"    {C.OK}[OK]{C.OFF} {'راح يتبدّل' if args.dry_run else 'اتبدّل'}")
            changed += 1

        # ---------- تنبيه على أي slug عربي آخر ----------
        others = [
            c for c in Category.query.all()
            if not is_ascii_slug(c.slug) and c.name not in FIXES
        ]

        if others:
            print()
            print(f"{C.WARN}[!]{C.OFF} تصنيفات أخرى عندها slug عربي:")
            for c in others:
                print(f"    • {c.name}  →  {c.slug}")
            print("    زيدهم فـFIXES فوق إذا حبيت تصحّحهم.")

        if args.dry_run:
            db.session.rollback()
        else:
            db.session.commit()

        print()
        print("=" * 60)
        print(f"  اتبدّلو: {changed}")
        print("=" * 60)

        if args.dry_run:
            print("\nما اتكتب والو. لتطبيق: python fix_category_slug.py")
        elif changed:
            print("\nالخطوة الجاية — غلاف التصنيف:")
            print("    python set_category_images.py")

    return 0


if __name__ == "__main__":
    sys.exit(run())
