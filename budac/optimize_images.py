"""
يحوّل كل الصور الموجودة فالموقع لـWebP محسّنة، ويولّد مصغّرات.

تشغيل:
    python optimize_images.py            # معاينة فقط، ما يبدّل والو
    python optimize_images.py --apply    # التحويل الفعلي

⚠️ ليش المعاينة أولاً؟
السكريبت يبدّل ملفات على القرص **ويحدّث قاعدة البيانات**. تشوف واش غادي يصرا
قبل ما يصرا، بدل ما تكتشف مشكل بعد فوات الأوان.

الأمان:
- الصور الأصلية تتنقل لمجلد `static/img/_originals_backup/` — ما تتمسحش
- إذا فشل تحويل صورة، السكريبت يتخطاها ويكمل (ما يوقفش كل شي بسبب صورة وحدة)
- تقدر تشغّلو أكثر من مرة: الصور المحوّلة من قبل يتخطاها
"""

import argparse
import os
import shutil
import sys

from app import create_app
from models import db, Product, ProductImage, ProductVariant, Category, Offer
from services.images import process_image, ImageProcessError, get_size_info

BACKUP_DIR = os.path.join("static", "img", "_originals_backup")

# (المجلد، الموديل، اسم الحقل) — كل الأماكن اللي فيها صور
IMAGE_SOURCES = [
    ("products", Product, "image_filename"),
    ("products", ProductImage, "image_filename"),
    ("products", ProductVariant, "image_filename"),
    ("categories", Category, "image_filename"),
    ("offers", Offer, "banner_filename"),
]


def human(size_bytes):
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024*1024):.1f} م"
    return f"{size_bytes / 1024:.0f} ك"


def backup_original(subfolder, filename):
    """ينقل الصورة الأصلية لمجلد النسخ الاحتياطي."""
    src = os.path.join("static", "img", subfolder, filename)
    if not os.path.isfile(src):
        return

    dest_dir = os.path.join(BACKUP_DIR, subfolder)
    os.makedirs(dest_dir, exist_ok=True)
    shutil.move(src, os.path.join(dest_dir, filename))


def run(apply_changes):
    app = create_app()

    with app.app_context():
        print("🔍 نفحصو الصور الموجودة...\n")

        jobs = []          # (subfolder, record, field, old_filename)
        seen_files = set()  # نتجنبو معالجة نفس الملف مرتين

        for subfolder, model, field in IMAGE_SOURCES:
            for record in model.query.all():
                filename = getattr(record, field, None)
                if not filename:
                    continue

                # محوّلة من قبل؟ نتخطاها
                if filename.lower().endswith(".webp"):
                    continue

                path = os.path.join("static", "img", subfolder, filename)
                if not os.path.isfile(path):
                    print(f"   ⚠️  ملف مفقود (نتخطاه): {subfolder}/{filename}")
                    continue

                jobs.append((subfolder, record, field, filename))
                seen_files.add((subfolder, filename))

        if not jobs:
            print("✅ كل الصور محوّلة من قبل — ماكاين والو نديروه.")
            return

        total_before = sum(
            get_size_info(os.path.join("static", "img", sf, fn))[0]
            for sf, fn in seen_files
        )

        print(f"📦 لقينا {len(jobs)} صورة تحتاج تحويل")
        print(f"   الحجم الحالي: {human(total_before)}\n")

        if not apply_changes:
            print("   أمثلة على اللي غادي يصرا:")
            for subfolder, record, field, filename in jobs[:5]:
                size, dims = get_size_info(os.path.join("static", "img", subfolder, filename))
                print(f"     {filename}  ({human(size)}, {dims[0]}×{dims[1]})  →  .webp + مصغّرة")
            if len(jobs) > 5:
                print(f"     ... و{len(jobs) - 5} صورة أخرى")

            print("\n⚠️  هذي معاينة فقط — ما تبدّل والو.")
            print("   باش تحوّل فعليًا:  python optimize_images.py --apply")
            return

        # ---------- التحويل الفعلي ----------
        print("🔄 نبداو التحويل...\n")
        converted, failed = 0, 0
        total_after = 0

        for subfolder, record, field, filename in jobs:
            target_dir = os.path.join("static", "img", subfolder)
            source_path = os.path.join(target_dir, filename)
            base_name = os.path.splitext(filename)[0]

            size_before, _ = get_size_info(source_path)

            try:
                new_filename, thumb = process_image(source_path, target_dir, base_name)
            except ImageProcessError as e:
                print(f"   ❌ {filename}: {e}")
                failed += 1
                continue

            new_size, _ = get_size_info(os.path.join(target_dir, new_filename))
            total_after += new_size

            # نحدّثو قاعدة البيانات **قبل** ما ننقلو الأصل، باش لو صار خطأ
            # ما نبقاوش بقاعدة بيانات تشير لملف انتقل
            setattr(record, field, new_filename)
            db.session.commit()

            backup_original(subfolder, filename)

            saved_pct = 100 - (new_size * 100 // size_before) if size_before else 0
            print(f"   ✅ {filename} → {new_filename}  ({human(size_before)} → {human(new_size)}, -{saved_pct}%)")
            converted += 1

        # ---------- التقرير ----------
        print(f"\n{'='*52}")
        summary = f"🎉 تم تحويل {converted} صورة"
        if failed:
            summary += f" | فشل {failed}"
        print(summary)

        if total_before and total_after:
            saved = total_before - total_after
            pct = saved * 100 // total_before
            print(f"   قبل:  {human(total_before)}")
            print(f"   بعد:  {human(total_after)}")
            print(f"   وفّرنا: {human(saved)}  ({pct}%)")

        print(f"\n📁 الصور الأصلية محفوظة فـ: {BACKUP_DIR}")
        print("   شوف الموقع أولاً، وإذا كلشي مليح تقدر تمسح هاد المجلد.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="تحويل صور الموقع لـWebP")
    parser.add_argument("--apply", action="store_true", help="التحويل الفعلي (بلاه = معاينة فقط)")
    args = parser.parse_args()

    run(args.apply)
