"""
معالجة الصور — تصغير + تحويل لـWebP + توليد مصغّرة.

علاش WebP؟
الصورة نفسها بجودة بصرية متطابقة تقريبًا، لكن حجمها أقل بـ25-35% من JPEG.
هذا يعني الموقع يفتح أسرع، خصوصًا فالجزائر وين الأنترنت ماشي دائمًا سريع،
وبزاف من الزبائن يدخلو من الهاتف بـ3G.

الدعم: كل المتصفحات الحديثة تدعم WebP (Chrome, Firefox, Safari 14+, Edge).
نسبة الدعم عالميًا فوق 97%. المتصفحات القديمة بزاف (كيما IE) ما توصلش أصلاً
لمتجر عصري كيما هذا.

كل صورة نولّدو منها نسختين:
  - النسخة الكبيرة (1200px)  → لصفحة المنتج والمعرض
  - المصغّرة (450px)          → لبطاقات المنتجات فالشبكة

السبب: بطاقة المنتج تعرض الصورة بـ~250 بكسل. تحميل صورة 1200 بكسل باش تعرضها
فـ250 هو هدر خالص — الزائر يحمّل 10 أضعاف اللي يحتاجو.
"""

import gc
import os

from PIL import Image, ImageOps

# ⚠️ حد أمان: يمنع "قنبلة الصور" (ملف صغير يتفكك لصورة ضخمة تستهلك كل الذاكرة).
# 50 ميغابكسل = صورة 7000×7000 — أكبر بكثير من أي صورة هاتف عادية.
Image.MAX_IMAGE_PIXELS = 50_000_000


def get_upload_dir(subfolder):
    """
    مسار مجلد الصور — يحترم UPLOAD_ROOT إذا كان محدد.

    ⚠️ علاش مركزية؟ باش نبدّلو مكان التخزين من مكان واحد. على الاستضافة
    نوجّهو لقرص دائم، ومحليًا يبقى static/img.
    """
    from flask import current_app

    try:
        root = current_app.config.get("UPLOAD_ROOT", "")
    except RuntimeError:
        root = os.environ.get("UPLOAD_ROOT", "")  # خارج سياق التطبيق (سكريبت)

    if root:
        return os.path.join(root, "img", subfolder)

    return os.path.join("static", "img", subfolder)

# الأبعاد القصوى (الصورة تتصغّر بنسبة متساوية، ما تتشوّهش)
MAX_WIDTH = 1200
MAX_HEIGHT = 1200
THUMB_SIZE = 450

# الجودة: 82 نقطة التوازن — العين ما تفرّقش، والحجم ينقص بزاف
QUALITY_MAIN = 82
QUALITY_THUMB = 78

THUMB_SUFFIX = "-thumb"


class ImageProcessError(Exception):
    """خطأ فمعالجة الصورة — رسالتو تتعرض للمستخدم بالعربية."""
    pass


def _prepare(im):
    """
    يحضّر الصورة قبل الحفظ:
      - يصحّح الدوران حسب بيانات EXIF (صور الهاتف كثير تجي مقلوبة)
      - يحوّل الوضع لـRGB/RGBA حسب الحاجة
    """
    # ⚠️ هاد السطر مهم: صور الأندرويد والآيفون تجي فيها معلومة "دوّرني 90 درجة"
    # مخبية فـEXIF. بلاه، الصورة تبان مقلوبة فالموقع رغم أنها صحيحة فالهاتف.
    im = ImageOps.exif_transpose(im)

    if im.mode in ("RGBA", "LA", "P"):
        # نحافظو على الشفافية (مهمة للوغوهات وصور PNG)
        im = im.convert("RGBA")
    else:
        im = im.convert("RGB")

    return im


def _save_webp(im, path, quality):
    """يحفظ الصورة كـWebP. method=6 أبطأ شوية لكن يعطي أصغر حجم."""
    im.save(path, "WEBP", quality=quality, method=6)


def _open_optimized(source, max_size):
    """
    يفتح الصورة بأقل استهلاك ذاكرة ممكن.

    ⚠️ الحيلة المهمة هنا: `draft()`.
    صورة هاتف 3024×4032 تاخذ ~36 ميغا فالذاكرة كي تتفك كاملة. لكن JPEG يقدر
    يتفك **مباشرة بحجم أصغر** (نصف/ربع/ثمن) بلا ما يمر بالحجم الكامل.
    هذا يقلل الاستهلاك بـ90%+ وهو أسرع كذلك.

    بلا هذا، استيراد 143 صورة على سيرفر بذاكرة محدودة يطيح بـ"out of memory".
    """
    im = Image.open(source)

    # draft يخدم غير مع JPEG — والباقي يتفك عادي
    if im.format == "JPEG":
        im.draft("RGB", (max_size, max_size))

    im.load()
    return im


def process_image(source, target_dir, base_name, make_thumb=True):
    """
    يعالج صورة ويحفظها كـWebP (+ مصغّرة).

    source     : مسار ملف، أو كائن ملف مفتوح (مثلاً FileStorage من الفورم)
    target_dir : المجلد الهدف (مثلاً static/img/products)
    base_name  : اسم الملف بلا امتداد (لازم يكون فريد)
    make_thumb : واش نولّدو مصغّرة

    يرجع (main_filename, thumb_filename). thumb_filename يكون None إذا make_thumb=False.
    يرمي ImageProcessError إذا الملف ماشي صورة صالحة.
    """
    im = None
    main = None
    thumb = None

    try:
        try:
            im = _open_optimized(source, MAX_WIDTH)
        except Exception as e:
            raise ImageProcessError(f"ما قدرناش نقراو الصورة: {e}")

        try:
            im = _prepare(im)
        except Exception as e:
            raise ImageProcessError(f"ما قدرناش نعالجو الصورة: {e}")

        os.makedirs(target_dir, exist_ok=True)

        # ---- النسخة الرئيسية ----
        # thumbnail يعدّل الصورة نفسها (in-place) ويحافظ على النسبة.
        # ⚠️ نصغّرو الأصل أولاً، وبعدها ناخذو نسخة للمصغّرة — هكذا النسخة
        # تكون من صورة صغيرة ماشي من الأصل الضخم.
        im.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.LANCZOS)

        main_filename = f"{base_name}.webp"
        _save_webp(im, os.path.join(target_dir, main_filename), QUALITY_MAIN)

        # ---- المصغّرة ----
        thumb_filename = None
        if make_thumb:
            thumb = im.copy()
            thumb.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
            thumb_filename = f"{base_name}{THUMB_SUFFIX}.webp"
            _save_webp(thumb, os.path.join(target_dir, thumb_filename), QUALITY_THUMB)

        return main_filename, thumb_filename

    finally:
        # نحرّرو الذاكرة فورًا — ما نستناوش الجامع التلقائي.
        # مهم بزاف كي نعالجو 143 صورة وحدة بوحدة.
        for img in (thumb, main, im):
            if img is not None:
                try:
                    img.close()
                except Exception:
                    pass
        gc.collect()


def thumb_name(filename):
    """
    يحوّل اسم صورة لاسم مصغّرتها: 'abc.webp' → 'abc-thumb.webp'.

    إذا الصورة ماشي WebP (صورة قديمة قبل التحويل)، نرجعو نفس الاسم —
    باش الموقع يبقى خدام حتى قبل ما تشغّل سكريبت التحويل.
    """
    if not filename:
        return filename

    base, ext = os.path.splitext(filename)

    if ext.lower() != ".webp":
        return filename  # صورة قديمة، ما عندهاش مصغّرة

    if base.endswith(THUMB_SUFFIX):
        return filename  # هي أصلاً مصغّرة

    return f"{base}{THUMB_SUFFIX}.webp"


def delete_image_files(filename, target_dir):
    """
    يمسح الصورة ومصغّرتها من القرص.
    ما يرميش خطأ إذا ماكانوش موجودين — الهدف (أن الملف ما يبقاش) محقق فالحالتين.
    """
    if not filename:
        return

    for name in {filename, thumb_name(filename)}:
        path = os.path.join(target_dir, os.path.basename(name))
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def get_size_info(path):
    """معلومات عن ملف صورة — نستعملوها فسكريبت التحويل باش نوريو الفرق."""
    try:
        size = os.path.getsize(path)
        with Image.open(path) as im:
            return size, im.size
    except Exception:
        return 0, (0, 0)
