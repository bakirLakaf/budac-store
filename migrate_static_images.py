# -*- coding: utf-8 -*-
"""
ينقل صور المنتجات القديمة من `static/img/products/` للقرص الدائم.

═══════════ المشكل ═══════════

22 منتج قديم صورهم جات **مع الكود** فمجلد static، ماشي مرفوعين من
اللوحة. الكود يعرضهم بمنطق احتياطي:

    الصورة فقرص /data؟  →  اعرضها من تمّة
             ↓ لا
    اعرضها من static/img/   ←  هنا راهم

يعني ما نقدروش نحيّدوهم من المستودع — 22 منتج يوليو بلا صور.

═══════════ الحل ═══════════

ننقلوهم للقرص الدائم كيما المنتجات الجداد. وبما أننا نعالجوهم على
كل حال، نستغلو الفرصة:

  • JPG  →  WebP        (حجم أصغر بكثير)
  • نولّدو المصغّرات    (بطاقة المنتج تعرض ~250 بكسل، وكانت تحمّل
                          الصورة كاملة — هدر فباقة الزبون)
  • نحدّثو أسماء الملفات فقاعدة البيانات

من بعد، `static/img/products/` تتحيّد من المستودع.

═══════════ التشغيل ═══════════

    python migrate_static_images.py --dry-run    # يوريك واش راح يصرا
    python migrate_static_images.py              # ينقل

⚠️ شغّلو **قبل** ما ترفع النسخة اللي بلا الصور. لو عكست الترتيب،
   المنتجات تبقى بلا صور حتى تعاود ترفعهم بيدك.

⚠️ آمن: ما يمسح حتى ملف. الصور القديمة تبقى فمكانها، ونخليك أنت
   تحيّدهم من GitHub كي تتأكد أن كلشي يبان مليح.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from models import db, Product, ProductImage, ProductVariant
from services.images import process_image, get_upload_dir, ImageProcessError


class C:
    OK = "\033[92m"
    WARN = "\033[93m"
    ERR = "\033[91m"
    INFO = "\033[96m"
    OFF = "\033[0m"


def collect_referenced():
    """
    يجمع كل اسم ملف صورة مذكور فقاعدة البيانات.

    ⚠️ ثلاث بلايص تشير للصور، ولازم نبدّلوهم كامل مع بعض:
      • Product.image_filename   — الصورة الرئيسية
      • ProductImage             — المعرض والصور التوضيحية
      • ProductVariant           — صورة كل موديل
    لو نسينا وحدة، الصورة تتحوّل والإشارة تبقى على الاسم القديم →
    صورة مكسورة.
    """
    names = set()

    for product in Product.query.all():
        if product.image_filename:
            names.add(product.image_filename)

    for image in ProductImage.query.all():
        if image.image_filename:
            names.add(image.image_filename)

    for variant in ProductVariant.query.all():
        if variant.image_filename:
            names.add(variant.image_filename)

    return names


def rename_everywhere(old_name, new_name):
    """يبدّل اسم صورة فكل البلايص اللي تشير ليها. يرجع عدد التبديلات."""
    changed = 0

    changed += (
        Product.query.filter_by(image_filename=old_name)
        .update({"image_filename": new_name}, synchronize_session=False)
    )
    changed += (
        ProductImage.query.filter_by(image_filename=old_name)
        .update({"image_filename": new_name}, synchronize_session=False)
    )
    changed += (
        ProductVariant.query.filter_by(image_filename=old_name)
        .update({"image_filename": new_name}, synchronize_session=False)
    )

    return changed


def run():
    parser = argparse.ArgumentParser(description="نقل الصور القديمة للقرص الدائم")
    parser.add_argument("--dry-run", action="store_true", help="تجربة بلا تبديل")
    args = parser.parse_args()

    app = create_app()

    with app.app_context():
        static_dir = os.path.join(app.root_path, "static", "img", "products")
        volume_dir = get_upload_dir("products")

        print("=" * 60)
        print(f"  من  : {static_dir}")
        print(f"  إلى : {volume_dir}")
        print(f"  الوضع: {'تجربة (بلا تبديل)' if args.dry_run else 'نقل'}")
        print("=" * 60)

        if os.path.abspath(static_dir) == os.path.abspath(volume_dir):
            print(f"\n{C.WARN}[!]{C.OFF} المصدر والهدف نفس المجلد.")
            print("    معناها UPLOAD_ROOT ماشي معمّر — راك تخدم محليًا.")
            print("    هاد السكريبت يخدم على Railway برك.")
            return 0

        if not os.path.isdir(static_dir):
            print(f"\n{C.OK}[OK]{C.OFF} ماكانش مجلد الصور القديمة — اتنقلو ديجا.")
            return 0

        referenced = collect_referenced()
        print(f"\nصور مذكورة فقاعدة البيانات: {len(referenced)}")

        moved = 0
        already = 0
        missing = []

        for name in sorted(referenced):
            source = os.path.join(static_dir, name)
            on_volume = os.path.join(volume_dir, name)

            # الصورة راهي ديجا فالقرص → المنتجات الجداد
            if os.path.isfile(on_volume):
                already += 1
                continue

            if not os.path.isfile(source):
                # ما لقيناهاش لا فالقرص لا فـstatic
                missing.append(name)
                continue

            base = os.path.splitext(name)[0]
            print(f"{C.INFO}▸ {name}{C.OFF}")

            if args.dry_run:
                print(f"    → {base}.webp  (+ مصغّرة)")
                moved += 1
                continue

            try:
                new_name, _thumb = process_image(
                    source, volume_dir, base, make_thumb=True
                )
            except ImageProcessError as e:
                print(f"{C.ERR}    [X]{C.OFF} فشلت المعالجة: {e}")
                missing.append(name)
                continue

            count = rename_everywhere(name, new_name)
            size_before = os.path.getsize(source) // 1024
            size_after = os.path.getsize(os.path.join(volume_dir, new_name)) // 1024

            print(f"{C.OK}    [OK]{C.OFF} {new_name}"
                  f"  ({size_before} ك → {size_after} ك)"
                  f"  · {count} إشارة اتحدّثت")
            moved += 1

        if args.dry_run:
            db.session.rollback()
        else:
            # ⚠️ commit واحد فالآخر: يا الكل يتحدّث يا والو. لو فشلنا
            # فالوسط، ما نبقاوش بنصف المنتجات تشير لأسماء جديدة
            # ونصفها للقديمة.
            db.session.commit()

        print()
        print("=" * 60)
        print(f"  اتنقلو      : {moved}")
        print(f"  فالقرص ديجا : {already}")
        if missing:
            print(f"  ما لقيناهمش : {len(missing)}")
        print("=" * 60)

        if missing:
            print(f"\n{C.WARN}[!]{C.OFF} صور مذكورة فقاعدة البيانات وماكانتش:")
            for name in missing[:10]:
                print(f"    • {name}")
            print("    هاذو كانو مكسورين من قبل — النقل ما زادش المشكل.")

        if args.dry_run:
            print("\nما اتبدّل والو. للتنفيذ: python migrate_static_images.py")
        elif moved:
            print("\nالخطوة الجاية:")
            print("  1. افتح المتجر وتأكد أن صور المنتجات تبان مليح")
            print("  2. ملّي تتأكد، احيّد من GitHub:")
            print("       static/img/products/   (الصور القديمة)")
            print("       migrate_static_images.py  (هاد السكريبت)")

    return 0


if __name__ == "__main__":
    sys.exit(run())
