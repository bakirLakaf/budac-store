"""
تصدير واستيراد المنتجات عبر ملف Excel.

═══════════════ قرار التصميم ═══════════════

**4 صفحات مترابطة بالـslug**، ماشي صفحة لكل منتج.

ليش؟ 26 منتج = 26 صفحة — مستحيل تعدّلهم دفعة وحدة، ولا تبحث فيهم،
ولا تنسخ عمود سعر من واحد لآخر. بصفحة وحدة للمنتجات، تقدر تفلتر
وترتّب وتعدّل 26 سعر فدقيقة.

الصفحات:
  1. المنتجات       — سطر لكل منتج
  2. الموديلات      — سطر لكل موديل، مربوط بـslug المنتج
  3. أسعار الكمية   — مربوطة بـslug + اسم الموديل
  4. العروض الجاهزة — نفس الربط

**الربط بالـslug ماشي بالـid**: الـslug مقروء ويبقى ثابت، والأدمن
يفهمو. الـid يبان كذلك للمرجع لكن الاستيراد يعتمد على الـslug.

═══════════════ قواعد الاستيراد ═══════════════

  • slug موجود  → **تحديث** المنتج
  • slug جديد   → **إنشاء** منتج جديد
  • خانة فارغة  → **ما نلمسوش** القيمة القديمة (ماشي نمسحوها)
  • الصور       → ما تتلمسش إطلاقًا (كيما طلب بكير)
"""

import io
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from models import (
    db, Product, Category, ProductVariant,
    VariantPriceTier, VariantPack,
)
from utils import slugify

# ---------- أسماء الصفحات والأعمدة ----------
SHEET_PRODUCTS = "المنتجات"
SHEET_VARIANTS = "الموديلات"
SHEET_TIERS = "أسعار الكمية"
SHEET_PACKS = "العروض الجاهزة"
SHEET_HELP = "الشرح"

COLS_PRODUCTS = [
    "المعرّف", "الرابط (slug)", "اسم المنتج", "التصنيف",
    "السعر", "السعر قبل التخفيض", "المخزون",
    "مفعّل", "مميّز", "الوصف",
]

COLS_VARIANTS = [
    "رابط المنتج", "المعرّف", "اسم الموديل",
    "السعر", "المخزون", "افتراضي",
]

COLS_TIERS = ["رابط المنتج", "اسم الموديل", "من", "إلى", "سعر الحبة"]

COLS_PACKS = ["رابط المنتج", "اسم الموديل", "عدد", "اسم العرض", "السعر"]

# ---------- ألوان ----------
HEAD_FILL = PatternFill("solid", fgColor="2E1A47")
HEAD_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=11)
KEY_FILL = PatternFill("solid", fgColor="FFF6E5")   # أعمدة الربط
BODY_FONT = Font(name="Arial", size=11)
THIN = Side(style="thin", color="D9D2C5")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _bool_out(value):
    return "نعم" if value else "لا"


def _bool_in(raw, default=None):
    """يقرا نعم/لا بكل الأشكال. يرجع default إذا الخانة فارغة."""
    if raw is None:
        return default

    text = str(raw).strip().lower()
    if not text:
        return default

    if text in ("نعم", "yes", "y", "true", "1", "oui", "✓"):
        return True
    if text in ("لا", "no", "n", "false", "0", "non", "✗"):
        return False
    return default


def _int_in(raw, default=None):
    """يقرا رقمًا. يرجع default إذا فارغة ولا غير صالحة."""
    if raw is None:
        return default
    text = str(raw).strip()
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def _text_in(raw, default=None):
    if raw is None:
        return default
    text = str(raw).strip()
    return text if text else default


def _style_sheet(sheet, headers, key_columns=()):
    """يلبّس الصفحة: رأس بنفسجي، تجميد، عرض أعمدة، حدود."""
    for index, title in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=index, value=title)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER

    sheet.freeze_panes = "A2"
    sheet.sheet_view.rightToLeft = True

    widths = {"الوصف": 60, "اسم المنتج": 34, "اسم الموديل": 26,
              "الرابط (slug)": 24, "رابط المنتج": 24, "اسم العرض": 28,
              "التصنيف": 22}

    for index, title in enumerate(headers, start=1):
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = widths.get(title, 14)

    # نلوّنو أعمدة الربط باش الأدمن ما يبدّلهاش بالغلط
    for title in key_columns:
        if title in headers:
            letter = get_column_letter(headers.index(title) + 1)
            for row in range(2, sheet.max_row + 1):
                sheet[f"{letter}{row}"].fill = KEY_FILL


# ═══════════════════════════════════════════
# التصدير
# ═══════════════════════════════════════════

def export_workbook():
    """يبني ملف Excel بكل المنتجات. يرجع BytesIO جاهز للتحميل."""
    workbook = Workbook()

    # ---------- الشرح ----------
    help_sheet = workbook.active
    help_sheet.title = SHEET_HELP
    help_sheet.sheet_view.rightToLeft = True

    lines = [
        ("كيفاش تستعمل هاد الملف", True),
        ("", False),
        ("1. عدّل اللي تحب فالصفحات، واحفظ الملف.", False),
        ("2. ارفعو من: لوحة التحكم ← المنتجات ← استيراد Excel.", False),
        ("", False),
        ("قواعد مهمة", True),
        ("• عمود «الرابط (slug)» هو المفتاح — ما تبدّلوش إلا إذا بغيت", False),
        ("  تنشئ منتجًا جديدًا. الأعمدة الصفراء هي أعمدة الربط.", False),
        ("• slug موجود = تحديث المنتج · slug جديد = إنشاء منتج جديد.", False),
        ("• خانة فارغة = ما نلمسوش القيمة القديمة (ماشي نمسحوها).", False),
        ("• الصور ما تتلمسش من هنا — عدّلها من لوحة التحكم.", False),
        ("• «مفعّل» و«مميّز» و«افتراضي»: اكتب نعم ولا لا.", False),
        ("• الموديلات مربوطة بـ«رابط المنتج» + «اسم الموديل».", False),
        ("  إذا بدّلت اسم الموديل، يتنشأ موديل جديد بدل ما يتحدّث.", False),
        ("", False),
        ("أسعار الكمية", True),
        ("• «إلى» فارغ = وفوق بلا حد.", False),
        ("• المجالات المتداخلة تترفض — التداخل يخلّي السعر غامضًا.", False),
        ("", False),
        (f"تصدّر فـ{datetime.now().strftime('%Y-%m-%d %H:%M')}", False),
    ]

    for index, (text, is_title) in enumerate(lines, start=1):
        cell = help_sheet.cell(row=index, column=1, value=text)
        cell.font = Font(name="Arial", size=12, bold=is_title,
                         color="2E1A47" if is_title else "000000")
    help_sheet.column_dimensions["A"].width = 70

    # ---------- المنتجات ----------
    products_sheet = workbook.create_sheet(SHEET_PRODUCTS)
    products = Product.query.order_by(Product.name).all()

    for row, product in enumerate(products, start=2):
        values = [
            product.id,
            product.slug,
            product.name,
            product.category.name if product.category else "",
            product.price,
            product.compare_at_price,
            product.stock_quantity,
            _bool_out(product.is_active),
            _bool_out(getattr(product, "is_featured", False)),
            product.description or "",
        ]
        for col, value in enumerate(values, start=1):
            cell = products_sheet.cell(row=row, column=col, value=value)
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=(col == 10))

    _style_sheet(products_sheet, COLS_PRODUCTS, key_columns=["الرابط (slug)"])

    # ---------- الموديلات ----------
    variants_sheet = workbook.create_sheet(SHEET_VARIANTS)
    row = 2
    for product in products:
        for variant in product.variants:
            values = [
                product.slug,
                variant.id,
                variant.label,
                variant.price,
                variant.stock_quantity,
                _bool_out(variant.is_default),
            ]
            for col, value in enumerate(values, start=1):
                cell = variants_sheet.cell(row=row, column=col, value=value)
                cell.font = BODY_FONT
                cell.border = BORDER
            row += 1

    _style_sheet(variants_sheet, COLS_VARIANTS,
                 key_columns=["رابط المنتج", "اسم الموديل"])

    # ---------- أسعار الكمية ----------
    tiers_sheet = workbook.create_sheet(SHEET_TIERS)
    row = 2
    for product in products:
        for variant in product.variants:
            for tier in sorted(variant.price_tiers, key=lambda t: t.min_quantity):
                values = [
                    product.slug, variant.label,
                    tier.min_quantity, tier.max_quantity, tier.unit_price,
                ]
                for col, value in enumerate(values, start=1):
                    cell = tiers_sheet.cell(row=row, column=col, value=value)
                    cell.font = BODY_FONT
                    cell.border = BORDER
                row += 1

    _style_sheet(tiers_sheet, COLS_TIERS,
                 key_columns=["رابط المنتج", "اسم الموديل"])

    # ---------- العروض الجاهزة ----------
    packs_sheet = workbook.create_sheet(SHEET_PACKS)
    row = 2
    for product in products:
        for variant in product.variants:
            for pack in sorted(variant.packs, key=lambda p: p.sort_order):
                values = [
                    product.slug, variant.label,
                    pack.quantity, pack.label, pack.price,
                ]
                for col, value in enumerate(values, start=1):
                    cell = packs_sheet.cell(row=row, column=col, value=value)
                    cell.font = BODY_FONT
                    cell.border = BORDER
                row += 1

    _style_sheet(packs_sheet, COLS_PACKS,
                 key_columns=["رابط المنتج", "اسم الموديل"])

    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


# ═══════════════════════════════════════════
# الاستيراد
# ═══════════════════════════════════════════

def _rows(sheet, headers):
    """
    يقرا الصفحة كقواميس {اسم العمود: القيمة}.

    ⚠️ نقراو الرأس من الملف نفسو ماشي من ثابت — الأدمن ممكن يبدّل
    ترتيب الأعمدة، والاستيراد لازم يبقى يخدم.
    """
    if sheet is None:
        return []

    header_row = [
        str(c.value).strip() if c.value is not None else ""
        for c in sheet[1]
    ]

    out = []
    for row in sheet.iter_rows(min_row=2):
        values = {}
        empty = True
        for index, cell in enumerate(row):
            if index >= len(header_row):
                break
            name = header_row[index]
            if not name:
                continue
            values[name] = cell.value
            if cell.value not in (None, ""):
                empty = False
        if not empty:
            out.append(values)
    return out


def import_workbook(file_storage):
    """
    يستورد المنتجات من ملف Excel.

    يرجع تقريرًا مفصّلاً: شنو تنشأ، شنو تحدّث، وشنو تجاوزناه وليش.
    """
    try:
        workbook = load_workbook(file_storage, data_only=True)
    except Exception as e:
        raise ValueError(f"ما قدرناش نقراو الملف: {e}")

    report = {
        "products_new": 0, "products_updated": 0,
        "variants_new": 0, "variants_updated": 0,
        "tiers": 0, "packs": 0,
        "warnings": [],
    }

    def warn(message):
        if len(report["warnings"]) < 40:
            report["warnings"].append(message)

    def sheet(name):
        return workbook[name] if name in workbook.sheetnames else None

    # ---------- 1) المنتجات ----------
    categories = {c.name.strip().lower(): c for c in Category.query.all()}
    products_by_slug = {}

    for index, row in enumerate(_rows(sheet(SHEET_PRODUCTS), COLS_PRODUCTS), start=2):
        slug = _text_in(row.get("الرابط (slug)"))
        name = _text_in(row.get("اسم المنتج"))

        if not slug and not name:
            continue

        if not slug:
            slug = slugify(name)

        product = Product.query.filter_by(slug=slug).first()
        is_new = product is None

        if is_new:
            if not name:
                warn(f"المنتجات سطر {index}: منتج جديد بلا اسم — تجاوزناه")
                continue

            price = _int_in(row.get("السعر"))
            if price is None:
                warn(f"المنتجات سطر {index}: «{name}» بلا سعر — تجاوزناه")
                continue

            product = Product(slug=slug, name=name, price=price, stock_quantity=0)
            db.session.add(product)
            report["products_new"] += 1
        else:
            report["products_updated"] += 1

        # ⚠️ الخانة الفارغة ما تمسحش القيمة القديمة
        if name:
            product.name = name

        price = _int_in(row.get("السعر"))
        if price is not None:
            product.price = price

        compare = _int_in(row.get("السعر قبل التخفيض"))
        if compare is not None:
            product.compare_at_price = compare or None

        stock = _int_in(row.get("المخزون"))
        if stock is not None:
            product.stock_quantity = stock

        active = _bool_in(row.get("مفعّل"))
        if active is not None:
            product.is_active = active

        featured = _bool_in(row.get("مميّز"))
        if featured is not None and hasattr(product, "is_featured"):
            product.is_featured = featured

        description = _text_in(row.get("الوصف"))
        if description is not None:
            product.description = description

        category_name = _text_in(row.get("التصنيف"))
        if category_name:
            category = categories.get(category_name.lower())
            if category:
                product.category_id = category.id
            else:
                warn(f"المنتجات سطر {index}: التصنيف «{category_name}» ماكانش — تجاوزناه")

        products_by_slug[slug] = product

    db.session.flush()

    # ---------- 2) الموديلات ----------
    def find_product(slug, where, index):
        if not slug:
            warn(f"{where} سطر {index}: بلا «رابط المنتج» — تجاوزناه")
            return None

        product = products_by_slug.get(slug) or Product.query.filter_by(slug=slug).first()
        if not product:
            warn(f"{where} سطر {index}: ماكانش منتج بالرابط «{slug}»")
        return product

    for index, row in enumerate(_rows(sheet(SHEET_VARIANTS), COLS_VARIANTS), start=2):
        product = find_product(_text_in(row.get("رابط المنتج")), SHEET_VARIANTS, index)
        if not product:
            continue

        label = _text_in(row.get("اسم الموديل"))
        if not label:
            warn(f"{SHEET_VARIANTS} سطر {index}: موديل بلا اسم — تجاوزناه")
            continue

        variant = (
            ProductVariant.query
            .filter(
                ProductVariant.product_id == product.id,
                db.func.lower(db.func.trim(ProductVariant.label)) == label.lower(),
            )
            .first()
        )

        if variant is None:
            variant = ProductVariant(product_id=product.id, label=label, stock_quantity=0)
            db.session.add(variant)
            db.session.flush()
            report["variants_new"] += 1
        else:
            report["variants_updated"] += 1

        raw_price = row.get("السعر")
        if raw_price is not None and str(raw_price).strip():
            variant.price = _int_in(raw_price)

        stock = _int_in(row.get("المخزون"))
        if stock is not None:
            variant.stock_quantity = stock

        default = _bool_in(row.get("افتراضي"))
        if default is not None:
            variant.is_default = default

    db.session.flush()

    # ---------- 3) أسعار الكمية ----------
    def find_variant(row, where, index):
        product = find_product(_text_in(row.get("رابط المنتج")), where, index)
        if not product:
            return None

        label = _text_in(row.get("اسم الموديل"))
        if not label:
            warn(f"{where} سطر {index}: بلا «اسم الموديل» — تجاوزناه")
            return None

        # ⚠️ نستعلمو من قاعدة البيانات ماشي من product.variants:
        # الموديلات اللي تنشأت فنفس عملية الاستيراد ما تكونش ظاهرة
        # فالعلاقة المحمّلة، فالأسعار ديالها تتجاهل بصمت.
        variant = (
            ProductVariant.query
            .filter(
                ProductVariant.product_id == product.id,
                db.func.lower(db.func.trim(ProductVariant.label)) == label.lower(),
            )
            .first()
        )
        if not variant:
            warn(f"{where} سطر {index}: ماكانش موديل «{label}» فـ«{product.name}»")
        return variant

    # ⚠️ نمسحو القديم فقط للموديلات اللي ظهرت فالملف — الموديلات
    # اللي ماشي فالملف تبقى كيما هي.
    touched_tiers = set()
    for index, row in enumerate(_rows(sheet(SHEET_TIERS), COLS_TIERS), start=2):
        variant = find_variant(row, SHEET_TIERS, index)
        if not variant:
            continue

        min_q = _int_in(row.get("من"))
        price = _int_in(row.get("سعر الحبة"))
        max_q = _int_in(row.get("إلى"))

        if min_q is None or price is None:
            warn(f"{SHEET_TIERS} سطر {index}: «من» ولا السعر ناقص — تجاوزناه")
            continue

        if min_q < 1 or price < 0 or (max_q is not None and max_q < min_q):
            warn(f"{SHEET_TIERS} سطر {index}: قيم غير منطقية — تجاوزناه")
            continue

        if variant.id not in touched_tiers:
            VariantPriceTier.query.filter_by(variant_id=variant.id).delete(
                synchronize_session=False
            )
            touched_tiers.add(variant.id)

        db.session.add(VariantPriceTier(
            variant_id=variant.id, min_quantity=min_q,
            max_quantity=max_q, unit_price=price,
        ))
        report["tiers"] += 1

    # ---------- 4) العروض الجاهزة ----------
    touched_packs = set()
    for index, row in enumerate(_rows(sheet(SHEET_PACKS), COLS_PACKS), start=2):
        variant = find_variant(row, SHEET_PACKS, index)
        if not variant:
            continue

        label = _text_in(row.get("اسم العرض"))
        price = _int_in(row.get("السعر"))
        quantity = _int_in(row.get("عدد"), 1)

        if not label or price is None:
            warn(f"{SHEET_PACKS} سطر {index}: الاسم ولا السعر ناقص — تجاوزناه")
            continue

        if price < 0 or quantity < 1:
            warn(f"{SHEET_PACKS} سطر {index}: قيم غير منطقية — تجاوزناه")
            continue

        if variant.id not in touched_packs:
            VariantPack.query.filter_by(variant_id=variant.id).delete(
                synchronize_session=False
            )
            touched_packs.add(variant.id)

        db.session.add(VariantPack(
            variant_id=variant.id, quantity=quantity,
            label=label, price=price,
            sort_order=report["packs"],
        ))
        report["packs"] += 1

    db.session.commit()
    return report
