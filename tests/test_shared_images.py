# -*- coding: utf-8 -*-
"""
اختبار الصور المشتركة بين المنتج ونسخته.

🔴 الخلل اللي هاد الملف موجود علاه:
«زر نسخة» ينسخ سطور الصور بلا ما ينسخ الملفات (قرار مقصود: 20 صورة
= 20 ميغا زايدة). لكن مسار الحذف كان يمسح الملفات من القرص على طول،
فحذف **النسخة** كان يمحي صور **الأصل** — الأصل يبقى يشير لملفات ماكانوش.

القاعدة الجديدة: الملف يتمسح من القرص غير كي ما يبقى حتى سطر يستعملو.

التشغيل: python tests/test_shared_images.py
"""
import os, sys, io, tempfile, atexit, shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "test-key-for-testing-only")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

# ⚠️ نوجّهو الرفع لمجلد مؤقت باش ما نلوّثوش static/img/products
_TMP = tempfile.mkdtemp(prefix="budac-test-shared-")
os.environ["UPLOAD_ROOT"] = _TMP
atexit.register(shutil.rmtree, _TMP, True)

from PIL import Image

from app import create_app
from models import db, Product, Category, AdminUser

PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name
          + ("  → " + str(detail) if not ok and detail != "" else ""))


def fake_png():
    """صورة PNG حقيقية صغيرة — الرفع يمرّ على Pillow فلازم تكون سليمة."""
    buf = io.BytesIO()
    Image.new("RGB", (60, 60), (200, 120, 40)).save(buf, format="PNG")
    buf.seek(0)
    return buf


def build():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True

    with app.app_context():
        db.create_all()
        cat = Category(name="مقابض", slug="handles")
        db.session.add(cat)
        admin = AdminUser(username="admin")
        admin.set_password("pass1234")
        db.session.add(admin)
        db.session.commit()

        p = Product(name="مقبض تجربة", slug="test-handle", price=450,
                    category_id=cat.id, stock_quantity=0, description="<p>x</p>")
        db.session.add(p)
        db.session.commit()
        pid = p.id

    return app, pid


def disk_path(app, filename):
    with app.app_context():
        from services.images import get_upload_dir
        return os.path.join(get_upload_dir("products"), filename)


def main():
    app, pid = build()
    client = app.test_client()
    client.post("/admin/login", data={"username": "admin", "password": "pass1234"})

    print("\n" + "=" * 60)
    print("الصور المشتركة بين المنتج ونسخته")
    print("=" * 60)

    # ───────── نرفعو صورتين حقيقيتين للمنتج الأصلي ─────────
    filenames = []
    for i in range(2):
        r = client.post(
            f"/admin/products/{pid}/images/upload",
            data={"image": (fake_png(), f"pic{i}.png")},
            content_type="multipart/form-data",
        )
        filenames.append(r.get_json()["filename"])

    check("الصورتين اترفعو وموجودين فالقرص",
          all(os.path.isfile(disk_path(app, f)) for f in filenames))

    # ───────── ننسخو المنتج ─────────
    client.post(f"/admin/products/{pid}/duplicate", follow_redirects=True)

    with app.app_context():
        copy = Product.query.filter(Product.id != pid).first()
        copy_id = copy.id if copy else None
        copy_files = sorted(i.image_filename for i in copy.images) if copy else []

    check("النسخة اتصنعات", copy_id is not None)
    check("النسخة تشارك نفس ملفات الصور", copy_files == sorted(filenames), copy_files)

    # ───────── 🔴 الفحص الأساسي: حذف النسخة ما يمسّش صور الأصل ─────────
    client.post(f"/admin/products/{copy_id}/delete", follow_redirects=True)

    still_there = [f for f in filenames if os.path.isfile(disk_path(app, f))]
    check("بعد حذف النسخة: ملفات الأصل باقية فالقرص",
          len(still_there) == 2, f"باقي {len(still_there)}/2")

    with app.app_context():
        orig = db.session.get(Product, pid)
        check("سطور صور الأصل ما تمسّاتش", len(orig.images) == 2)
        check("الصورة الرئيسية للأصل باقية", orig.image_filename in filenames)

    # ───────── حذف صورة وحدة من نسخة تانية ما يمسّش الأصل ─────────
    client.post(f"/admin/products/{pid}/duplicate", follow_redirects=True)
    with app.app_context():
        copy2 = Product.query.filter(Product.id != pid).first()
        copy2_id = copy2.id
        img_id = copy2.images[0].id
        shared_name = copy2.images[0].image_filename

    client.post(f"/admin/products/{copy2_id}/images/{img_id}/delete",
                follow_redirects=True)

    check("حذف صورة وحدة من النسخة ما يمسحش الملف المشترك",
          os.path.isfile(disk_path(app, shared_name)))

    with app.app_context():
        orig = db.session.get(Product, pid)
        check("الأصل باقي عندو صورتين", len(orig.images) == 2)

    # ───────── وكي يبقى الأصل وحدو، الملف يتمسح فعلاً (ما نكدسوش زبالة) ─────────
    client.post(f"/admin/products/{copy2_id}/delete", follow_redirects=True)
    client.post(f"/admin/products/{pid}/delete", follow_redirects=True)

    left = [f for f in filenames if os.path.isfile(disk_path(app, f))]
    check("بعد حذف آخر منتج: الملفات اتمسحو من القرص", left == [], left)

    print("\n" + "=" * 60)
    print(f"نجح: {len(PASSED)}  ·  فشل: {len(FAILED)}")
    print("=" * 60)
    if FAILED:
        for f in FAILED:
            print("  ❌ " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
