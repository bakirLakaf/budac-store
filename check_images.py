# -*- coding: utf-8 -*-
"""
يجاوب على سؤال واحد: واش `static/img/products/` مازال لازم؟

═══════════ علاه هاد السكريبت ═══════════

22 صورة قديمة قاعدين فالمستودع. قلنا «لازمين» بناءً على أن أسماء
الملفات تطابق روابط المنتجات (slugs). **بصح هذا استنتاج ماشي
حقيقة** — ملف التصدير تاع Excel ما فيهش عمود الصور، فما تأكدناش
أبدًا من قاعدة البيانات الحقيقية.

هاد السكريبت يسقسي قاعدة البيانات مباشرة ويقطع الشك.

═══════════ التشغيل ═══════════

    python check_images.py

ما يبدّل والو — قراءة برك.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from models import Product, ProductImage, ProductVariant
from services.images import get_upload_dir


def run():
    app = create_app()

    with app.app_context():
        static_dir = os.path.join(app.root_path, "static", "img", "products")
        volume_dir = get_upload_dir("products")

        print("=" * 60)
        print(f"  static : {static_dir}")
        print(f"  القرص  : {volume_dir}")
        print("=" * 60)

        if os.path.abspath(static_dir) == os.path.abspath(volume_dir):
            print("\n[!] UPLOAD_ROOT ماشي معمّر — راك تخدم محليًا.")
            print("    شغّل هاد السكريبت على Railway.")
            return 0

        # ⚠️ ثلاث جداول تشير للصور. لو قريت واحد برك، تحيّد صورة
        #    مازالت مربوطة بموديل ولا بالمعرض.
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

        on_volume = []
        needs_static = []
        missing = []

        for name in sorted(names):
            if os.path.isfile(os.path.join(volume_dir, name)):
                on_volume.append(name)
            elif os.path.isdir(static_dir) and os.path.isfile(os.path.join(static_dir, name)):
                needs_static.append(name)
            else:
                missing.append(name)

        print(f"\nصور مذكورة فقاعدة البيانات : {len(names)}")
        print(f"  موجودة فالقرص الدائم      : {len(on_volume)}")
        print(f"  موجودة فـstatic برك       : {len(needs_static)}")
        if missing:
            print(f"  ما لقيناهاش فحتى بلاصة    : {len(missing)}  ← مكسورة ديجا")

        if needs_static:
            print("\nهاذو مازالو محتاجين static:")
            for name in needs_static[:15]:
                print(f"    • {name}")
            if len(needs_static) > 15:
                print(f"    ... و{len(needs_static) - 15} أخرى")

        if missing:
            print("\nصور مكسورة (مذكورة وماكانتش):")
            for name in missing[:10]:
                print(f"    • {name}")

        print("\n" + "=" * 60)
        if needs_static:
            print("  الحكم: شغّل migrate_static_images.py أولاً،")
            print("         ومن بعد احيّد static/img/products/")
        else:
            print("  الحكم: احيّد static/img/products/ مباشرة.")
            print("         ماكانش منتج محتاجها.")
        print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(run())
