"""
رفع الصور من لوحة التحكم — بأمان.

المخاطر اللي نحميو منها هنا:
1. رفع ملف خطير (سكريبت PHP/HTML) متنكر كصورة → نتحققو من الامتداد + المحتوى الفعلي
2. اسم ملف خبيث ("../../etc/passwd") → نستعملو secure_filename + اسم عشوائي
3. تصادم الأسماء → كل ملف ياخذ اسم فريد
"""

import os
import uuid

from werkzeug.utils import secure_filename

from services.images import process_image, delete_image_files, get_upload_dir, ImageProcessError

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
# ⚠️ هذا حد **أمان** ماشي حد استعمال: المتصفح يصغّر الصورة قبل الرفع
# (شوف shrinkImage فـproduct_form.html)، والسيرفر يعاود يصغّرها ويحوّلها
# WebP. الحد الكبير هنا يمنع رفع ملف ضخم بالغلط، بلا ما يرفض صورة هاتف
# عادية — الحد القديم (5 ميغا) كان يرفض صور 8–12 ميغا بلا فايدة.
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 ميغا

# التوقيعات الأولى (magic bytes) للصور الحقيقية — نتحققو أن المحتوى فعلاً صورة
MAGIC_SIGNATURES = [
    b"\xff\xd8\xff",           # JPEG
    b"\x89PNG\r\n\x1a\n",      # PNG
    b"GIF87a",                 # GIF
    b"GIF89a",                 # GIF
    b"RIFF",                   # WebP (يليه WEBP فالبايت 8-12)
]


class UploadError(Exception):
    """خطأ فرفع الملف — رسالتو تتعرض مباشرة للمستخدم بالعربية."""
    pass


def _looks_like_image(file_storage):
    """يقرا أول بايتات الملف ويتأكد أنها توقيع صورة حقيقية."""
    head = file_storage.stream.read(12)
    file_storage.stream.seek(0)  # مهم: نرجعو للبداية باش الحفظ يخدم

    if not head:
        return False

    for sig in MAGIC_SIGNATURES:
        if head.startswith(sig):
            # WebP: RIFF....WEBP
            if sig == b"RIFF":
                return head[8:12] == b"WEBP"
            return True
    return False


def save_image(file_storage, subfolder, base_name=None, make_thumb=True):
    """
    يحفظ صورة مرفوعة فـ static/img/<subfolder>/ ويرجع اسم الملف المحفوظ.

    ⚠️ الصورة تتحوّل تلقائيًا لـ WebP وتتصغّر لأقصى 1200px، وتتولّد منها مصغّرة.
    يعني حتى لو الأدمن رفع صورة 4000×3000 بحجم 8 ميغا من هاتفو، الموقع يخزّن
    نسخة محسّنة صغيرة — بلا ما يحتاج يفهم فالضغط ولا يستعمل أدوات خارجية.

    subfolder : "products" / "categories" / "offers"
    base_name : جزء من الاسم للتوضيح (اختياري) — الاسم النهائي يبقى فريد دائمًا

    يرمي UploadError برسالة عربية واضحة إذا الملف مرفوض.
    """
    if not file_storage or not file_storage.filename:
        raise UploadError("ما اخترتش حتى ملف")

    # 🔴 خلل كان هنا: الامتداد كان يتقرا **بعد** `secure_filename`.
    # لكن `secure_filename` تحذف الحروف العربية كليًا، حتى النقطة:
    #     "لاد كادر متعدد الالوان.png"  →  "png"   (بلا نقطة!)
    # فـ`splitext` ما تلقى حتى امتداد، وأي صورة باسم عربي تترفض.
    #
    # الحل: نقراو الامتداد من الاسم **الأصلي** قبل التنظيف. الأمان
    # ما يتأثرش — الاسم النهائي المحفوظ يتولّد عندنا (سطر 95 تحت)،
    # واسم المستخدم ما يوصلش أبدًا لنظام الملفات.
    ext = os.path.splitext(file_storage.filename or "")[1].lower().strip()

    if ext not in ALLOWED_EXTENSIONS:
        allowed = "، ".join(sorted(ALLOWED_EXTENSIONS))
        raise UploadError(f"نوع الملف غير مسموح. المسموح: {allowed}")

    # فحص الحجم بلا ما نقراو الملف كامل فالذاكرة
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)

    if size > MAX_FILE_SIZE:
        raise UploadError(f"حجم الصورة كبير بزاف (الحد الأقصى {MAX_FILE_SIZE // (1024*1024)} ميغا)")

    if size == 0:
        raise UploadError("الملف فارغ")

    if not _looks_like_image(file_storage):
        raise UploadError("هذا الملف ماشي صورة حقيقية")

    # اسم فريد: نتجنبو تصادم الأسماء وكشف أسماء الملفات الأصلية.
    # بلا امتداد — المعالج هو اللي يحدد الامتداد النهائي (.webp).
    unique_part = uuid.uuid4().hex[:10]

    # ⚠️ secure_filename تحذف الحروف العربية كليًا، فاسم عربي يولّي "---" فقط.
    # فهاد الحالة نستعملو بادئة عامة بدل اسم بلا معنى.
    prefix = secure_filename(base_name or "")[:40].strip("-_")
    if not prefix or not any(c.isalnum() for c in prefix):
        prefix = "img"

    base = f"{prefix}-{unique_part}"

    target_dir = get_upload_dir(subfolder)

    try:
        main_filename, _ = process_image(
            file_storage.stream, target_dir, base, make_thumb=make_thumb
        )
    except ImageProcessError as e:
        raise UploadError(str(e))

    return main_filename


def delete_image(filename, subfolder):
    """
    يمسح صورة **ومصغّرتها** من القرص.
    ما يرميش خطأ إذا الملفات ماكانوش موجودين.
    """
    if not filename:
        return

    target_dir = get_upload_dir(subfolder)
    delete_image_files(secure_filename(filename), target_dir)
