from datetime import datetime, timezone


def utcnow():
    """
    الوقت الحالي بتوقيت UTC، بلا معلومة المنطقة.

    ⚠️ علاش دالة خاصة؟ `datetime.utcnow()` **مهجورة** فـPython 3.12
    وراح تتمسح فإصدار جاي — ساعتها كل أعمدة التواريخ توقف.

    البديل الرسمي `datetime.now(timezone.utc)` يرجّع وقتًا **بمنطقة**،
    وأعمدة `db.DateTime` تاعنا بلا منطقة. خلط الاثنين يطيّح أي مقارنة
    بـ«can't compare offset-naive and offset-aware datetimes».

    فنحيّدو المنطقة بـ`.replace(tzinfo=None)` — نفس السلوك القديم
    بالضبط، بلا إهمال.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class VariantPriceTier(db.Model):
    """
    سعر الحبة حسب الكمية — عرض الجملة.

    مثال لموديل "دوري كبير":
        من 1   إلى 99   → 500 دج للحبة
        من 100 إلى 199  → 420 دج للحبة
        من 200 وفوق      → 380 دج للحبة

    ⚠️ الحساب يصرا **فالسيرفر دائمًا**. الزبون يشوف السعر يتبدّل وهو يزيد
    الكمية، بصح اللي يتحسب فالطلبية هو اللي نحسبوه حنا — ماشي اللي بعثو
    المتصفح.
    """

    __tablename__ = "variant_price_tiers"

    id = db.Column(db.Integer, primary_key=True)
    variant_id = db.Column(db.Integer, db.ForeignKey("product_variants.id"),
                           nullable=False, index=True)

    min_quantity = db.Column(db.Integer, nullable=False)          # من (شامل)
    max_quantity = db.Column(db.Integer, nullable=True)           # إلى (شامل)، فارغ = بلا حد
    unit_price = db.Column(db.Integer, nullable=False)            # سعر الحبة فهاد المجال

    variant = db.relationship("ProductVariant", back_populates="price_tiers")

    @property
    def range_display(self):
        """وصف المجال للعرض: «100 – 199 حبة» ولا «200 حبة وفوق»."""
        if self.max_quantity:
            return f"{self.min_quantity} – {self.max_quantity} حبة"
        return f"{self.min_quantity} حبة وفوق"

    @property
    def unit_price_display(self):
        return f"{self.unit_price:,} دج / الحبة"

    def matches(self, quantity):
        if quantity < self.min_quantity:
            return False
        if self.max_quantity is not None and quantity > self.max_quantity:
            return False
        return True

    def __repr__(self):
        return f"<Tier {self.min_quantity}+ = {self.unit_price}>"



class Category(db.Model):
    """
    تصنيف للمنتجات (مثلاً: مرايا، إكسسوارات، ديكور...).
    """

    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    slug = db.Column(db.String(170), nullable=False, unique=True)
    image_filename = db.Column(db.String(255), nullable=True)  # اسم الصورة فـ static/img/categories/
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    products = db.relationship("Product", back_populates="category", lazy="select")

    def __repr__(self):
        return f"<Category {self.id} {self.name}>"


class ProductImage(db.Model):
    """
    صورة من صور المنتج (منتج واحد يقدر يكون عندو بزاف صور).

    فيه نوعين من الصور، والفرق بيناتهم مهم:

    1) **صور المنتج** (`is_extra = False`) — الصور الحقيقية للسلعة.
       هاذو المربوطين بالموديلات، وترتيبهم يحدّد ترتيب الموديلات
       والافتراضي منهم.

    2) **صور أخرى** (`is_extra = True`) — توضيحية برك: كيفاش يتستعمل،
       وين يتحط، مقاسات... ما تتربطش بموديل، وما تدخلش فالترتيب.
       تبان تحت المعرض الرئيسي فصفحة المنتج.
    """

    __tablename__ = "product_images"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    image_filename = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)  # ترتيب الظهور فالمعرض

    # ⚠️ `default=False` + `server_default` باش الصفوف القديمة تولّي
    # كلها صور منتج عادية بعد الهجرة — ماشي NULL.
    is_extra = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )

    # وصف قصير يبان تحت الصورة التوضيحية («كيفاش تركّبها»)
    caption = db.Column(db.String(200))

    product = db.relationship("Product", back_populates="images")

    def __repr__(self):
        return f"<ProductImage {self.id} product={self.product_id}>"


class ProductVariant(db.Model):
    """
    تنويعة من تنويعات المنتج (مثلاً: اللون=دوري، أو المقاس=كبير).
    كل تنويعة تقدر يكون عندها سعرها الخاص وكميتها الخاصة فالمخزون.
    """

    __tablename__ = "product_variants"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    label = db.Column(db.String(150), nullable=False)  # مثلاً: "دوري" أو "أرجنتي / كبير"
    price = db.Column(db.Integer, nullable=True)  # لو null، نستعملو سعر المنتج الأساسي
    compare_at_price = db.Column(db.Integer, nullable=True)  # السعر قبل التخفيض (للعروض)
    stock_quantity = db.Column(db.Integer, nullable=False, default=0)
    sku = db.Column(db.String(100), nullable=True)
    image_filename = db.Column(db.String(255), nullable=True)  # صورة خاصة بهاد التنويعة (اختياري)
    is_default = db.Column(db.Boolean, nullable=False, default=False)

    # ⚠️ توفّر يدوي — منفصل تمامًا على المخزون.
    # المتجر ما يتبّعش المخزون بالأرقام (`is_stock_tracked = False`)،
    # لكن أحيانًا موديل معيّن يخلص من عند المورّد. هاد العلم يخلّي
    # الأدمن يعطّلو بضغطة، بلا ما يمسحو ويخسر أسعارو وعروضو.
    is_available = db.Column(db.Boolean, nullable=False, default=True,
                             server_default="1")

    product = db.relationship("Product", back_populates="variants")

    price_tiers = db.relationship(
        "VariantPriceTier",
        back_populates="variant",
        cascade="all, delete-orphan",
        order_by="VariantPriceTier.min_quantity",
    )

    def price_for_quantity(self, quantity):
        """
        سعر الحبة حسب الكمية المطلوبة.

        ⚠️ هاد الدالة هي **المرجع الوحيد** للسعر. السلة، صفحة المنتج،
        والطلبية كلهم يستعملوها — باش ما يصراش اختلاف بين السعر اللي
        شاف الزبون واللي اتحسب فعلاً.

        إذا ماكانش مجال يوافق الكمية، نرجعو السعر العادي.
        """
        quantity = max(1, int(quantity or 1))

        for tier in sorted(self.price_tiers, key=lambda t: t.min_quantity, reverse=True):
            if tier.matches(quantity):
                return tier.unit_price

        return self.effective_price

    def tiers_payload(self):
        """المجالات بصيغة JSON — يستعملها JavaScript فصفحة المنتج."""
        return [
            {
                "min": t.min_quantity,
                "max": t.max_quantity,
                "price": t.unit_price,
                "range": t.range_display,
            }
            for t in sorted(self.price_tiers, key=lambda t: t.min_quantity)
        ]

    def packs_payload(self):
        """
        العروض الجاهزة بصيغة JSON — لصفحة المنتج.

        ⚠️ نبعثو السعر الإجمالي والعدد. الحساب النهائي يتم فالسلة على
        السيرفر، وهاذي غير للعرض والاختيار.
        """
        return [
            {
                "id": pk.id,
                "quantity": pk.quantity,
                "label": pk.label,
                "full_label": pk.full_label,
                "price": pk.price,
                "unit_price": pk.unit_price,
            }
            for pk in sorted(self.packs, key=lambda pk: pk.sort_order)
        ]

    @property
    def effective_price(self):
        return self.price if self.price is not None else self.product.price

    @property
    def price_display(self):
        return f"{self.effective_price:,} دج"

    @property
    def is_in_stock(self):
        """
        فالنظام المفتوح، كل التنويعات متوفرة دائمًا.

        ⚠️ كان مكتوبًا `Product.is_stock_tracked` (على الصنف مباشرة)
        بينما باقي الكود يستعمل `self.product.is_stock_tracked`.
        درك ما يبانش الفرق لأن العلم `False` فكل مكان، لكن اليوم اللي
        ترجّع فيه تتبّع المخزون، هاد السطر يخدم بمنطق مختلف عن الباقي
        ويولّي مصدر بق صعيب يتلقى.

        ونحميو كذلك من الحالة اللي التنويعة ما عندهاش منتج (بيانات
        يتيمة) — نعتبروها متوفرة بدل ما نطيّحو الصفحة.
        """
        # ⚠️ التعطيل اليدوي يغلب كلش — حتى إذا المخزون ماشي متتبّعًا
        if not self.is_available:
            return False

        product = self.product
        if product is None or not product.is_stock_tracked:
            return True
        return (self.stock_quantity or 0) > 0

    @property
    def has_discount(self):
        return self.compare_at_price is not None and self.compare_at_price > self.effective_price

    @property
    def discount_percent(self):
        if not self.has_discount:
            return 0
        return round(100 - (self.effective_price / self.compare_at_price * 100))

    def __repr__(self):
        return f"<ProductVariant {self.id} {self.label}>"


class Product(db.Model):
    """
    منتج فالمتجر.
    """

    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(220), nullable=False, unique=True, index=True)  # يستعمل فالرابط: /product/<slug>

    description = db.Column(db.Text, nullable=True)  # نص واضح (بلا وسوم HTML) — يستعمل للعرض
    description_html = db.Column(db.Text, nullable=True)  # النسخة الأصلية بالـHTML (احتياط/مرجع)

    price = db.Column(db.Integer, nullable=False)  # بالدينار الجزائري، رقم صحيح (بلا فواصل)
    compare_at_price = db.Column(db.Integer, nullable=True)  # السعر قبل التخفيض — يبين شارة "عرض"

    # وحدة البيع: "piece" · "meter" · أي نص مخصّص («رولو»، «علبة»…)
    # ⚠️ `server_default` باش المنتجات القديمة تولّي «حبة» بعد الهجرة
    unit = db.Column(db.String(40), nullable=False, default="piece",
                     server_default="piece")

    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True, index=True)
    color_variant = db.Column(db.String(120), nullable=True)  # قديم — يبقى للتوافق، الأفضل نستعملو variants

    # ---------- حاسبة الكمية ----------
    # بعض المنتجات تتباع بالمتر (أشرطة، ورق لاصق)، والزبون ما يعرفش شحال يحتاج.
    # الحاسبة تسولو على أبعاد الحاجة اللي حاب يزيّن، وتحسبلو الكمية.
    #   None       → بلا حاسبة (منتج عادي)
    #   "perimeter"→ حاسبة محيط: للأشرطة اللي تدور حول مرآة/إطار
    #   "area"     → حاسبة مساحة: للورق اللاصق اللي يغطي سطح
    calculator_type = db.Column(db.String(20), nullable=True)

    # ⚠️ عمود مهجور — حاسبة المحيط ولّات تعطي أمتار برك، ما بقاش
    # فيها منطق «عدد اللفات». خلّيناه فقاعدة البيانات باش ما نحتاجوش
    # ترحيلاً، لكن **ما يتقراش وما يتكتبش** من أي مكان.
    roll_length_m = db.Column(db.Float, nullable=True)

    # للورق اللاصق: عرض اللفة بالمتر (مثلاً 1.0 أو 1.2)
    roll_width_m = db.Column(db.Float, nullable=True)

    # هامش أمان % — نزيدو على الحساب باش الزبون ما يبقاش ناقصو
    calculator_margin = db.Column(db.Integer, nullable=False, default=10)

    stock_quantity = db.Column(db.Integer, nullable=False, default=0)
    image_filename = db.Column(db.String(255), nullable=True)  # الصورة الرئيسية (تبقى للتوافق مع القديم)
    is_active = db.Column(db.Boolean, nullable=False, default=True)  # يبان فالمتجر ولا لا

    # يبان فقسم "الأكثر طلبًا" باختيار يدوي.
    # ⚠️ هذا حل مؤقت ذكي: المتجر جديد وماكانش مبيعات، فالأدمن يختار بيدو.
    # كي توصل المبيعات الحقيقية للحد الأدنى، القسم يتبدّل أوتوماتيكيًا
    # للأرقام الحقيقية وهاد الخانة تولّي بلا مفعول (شوف _find_best_sellers).
    is_featured = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    category = db.relationship("Category", back_populates="products")
    # ⚠️ لازم `id` كمفتاح ثانٍ: المنتجات القديمة كل `sort_order` فيها
    # صفر، فبلاه الترتيب يولّي عشوائيًا حسب مزاج قاعدة البيانات —
    # ويتبدّل من طلب لطلب. باقي الكود يستعمل `(sort_order, id)` أصلاً.
    images = db.relationship(
        "ProductImage",
        back_populates="product",
        order_by="ProductImage.sort_order, ProductImage.id",
        cascade="all, delete-orphan",
    )
    variants = db.relationship(
        "ProductVariant",
        back_populates="product",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Product {self.id} {self.name}>"

    # ═══════════════════════════════════════════════════════════════
    # المخزون: النظام درك "مفتوح" — ما نتبعوش الكميات.
    #
    # القرار: متجرك يشتغل على الطلب، والكميات تتبدّل بسرعة فالمحل الحقيقي.
    # تتبّع مخزون غير دقيق أسوأ من بلا تتبّع: يمنع بيعة موجودة فعلاً.
    #
    # الحقول stock_quantity باقية فقاعدة البيانات (ما مسحناهمش) باش:
    #   1. الطلبيات القديمة تبقى صحيحة
    #   2. إذا حبيت ترجّع التتبّع بعدين، ترجعو بتبديل is_stock_tracked لـTrue
    # ═══════════════════════════════════════════════════════════════

    # بدّلها لـTrue إذا حبيت ترجّع تتبّع المخزون
    is_stock_tracked = False

    # ══════════════════════════════════════════════════════════
    # وحدة البيع
    #
    # ⚠️ ماشي كل شي ينباع بالحبة. الشريط والسفيفة ينباعو **بالمتر**،
    # فـ«سعر الحبة» تولّي غالطة ومحيّرة للزبون.
    #
    # `unit` تقبل: "piece" (حبة) · "meter" (متر) · أي نص آخر = مخصّص
    # (الأدمن يكتب «رولو»، «علبة»، «كيلو»...).
    # ══════════════════════════════════════════════════════════

    UNIT_PIECE = "piece"
    UNIT_METER = "meter"

    UNIT_LABELS = {
        UNIT_PIECE: "الحبة",
        UNIT_METER: "المتر",
    }

    @property
    def available_quantity(self):
        """
        الكمية المتوفرة. فالنظام المفتوح نرجعو رقم كبير — يعني "متوفر دائمًا".
        """
        if not self.is_stock_tracked:
            return 9999
        if self.variants:
            return sum(v.stock_quantity for v in self.variants)
        return self.stock_quantity

    @property
    def is_in_stock(self):
        if not self.is_stock_tracked:
            return True
        return self.available_quantity > 0

    def consume_stock(self, quantity):
        """يخصم من المخزون. فالنظام المفتوح ما يديرش والو ويرجع True دائمًا."""
        if not self.is_stock_tracked or quantity <= 0:
            return True

        if self.available_quantity < quantity:
            return False

        if not self.variants:
            self.stock_quantity -= quantity
            return True

        remaining = quantity
        for variant in self.variants:
            if remaining <= 0:
                break
            take = min(variant.stock_quantity, remaining)
            variant.stock_quantity -= take
            remaining -= take
        return True

    def restore_stock(self, quantity):
        """يرجّع كمية للمخزون. فالنظام المفتوح ما يديرش والو."""
        if not self.is_stock_tracked or quantity <= 0:
            return

        if not self.variants:
            self.stock_quantity += quantity
        else:
            target = self.default_variant or self.variants[0]
            target.stock_quantity += quantity

    @property
    def short_description(self):
        """
        وصف قصير لمحركات البحث ومشاركات فيسبوك.

        ⚠️ جوجل يقصّ الوصف عند ~155 حرف. نبنيوه من وصف المنتج، وإذا
        ماكانش، نركّبو وحد من الاسم والتصنيف — أحسن من وصف فارغ يخلّي
        جوجل ياخذ أول نص عشوائي من الصفحة.
        """
        text = " ".join((self.description or "").split())

        if text:
            if len(text) > 155:
                text = text[:152].rsplit(" ", 1)[0] + "..."
            return text

        parts = [self.name]
        if self.category:
            parts.append(self.category.name)
        parts.append("توصيل لكل ولايات الجزائر · الدفع عند الاستلام")
        return " — ".join(parts)

    @property
    def average_rating(self):
        """معدّل التقييمات المعتمدة. None إذا ماكانش تقييم."""
        approved = [r for r in self.reviews if r.is_approved]
        if not approved:
            return None
        return round(sum(r.rating for r in approved) / len(approved), 1)

    # ══════════════════════════════════════════════════════════
    # السعر المعروض
    #
    # 🔴 كان خلل حقيقي: العنوان يعرض `self.price` (سعر المنتج)، بينما
    # الإجمالي تحت يحسب بسعر **الموديل الافتراضي**. الزبون يشوف رقمين
    # مختلوين لنفس السلعة فنفس الشاشة.
    #
    # الحل: كي يكون فيه موديلات، السعر المعروض = سعر الموديل الافتراضي.
    # حقل «السعر» فلوحة التحكم يبقى موجود كـ**سعر احتياطي** للموديلات
    # اللي ما عندهاش سعر خاص (variant.price = None).
    #
    # ⚠️ ماكانش دوران لانهائي: `variant.effective_price` يقرا العمود
    # `self.price` مباشرة، ماشي هاد الخاصية.
    # ══════════════════════════════════════════════════════════

    @property
    def display_price(self):
        """السعر اللي يشوفو الزبون — سعر الموديل الافتراضي إذا كان."""
        v = self.default_variant
        if v is not None:
            return v.effective_price
        return self.price

    @property
    def price_display(self):
        """سعر منسق للعرض: 1200 → '1,200 دج'"""
        return f"{self.display_price:,} دج"

    @property
    def compare_at_price_display(self):
        if self.compare_at_price:
            return f"{self.compare_at_price:,} دج"
        return None

    @property
    def has_discount(self):
        return (self.compare_at_price is not None
                and self.compare_at_price > self.display_price)

    @property
    def discount_percent(self):
        if not self.has_discount:
            return 0
        return round(100 - (self.display_price / self.compare_at_price * 100))

    @property
    def gallery_images(self):
        """
        قائمة صور المنتج للمعرض. إذا عندو صور فـProductImage، نستعملها.
        وإلا نرجعو للصورة الرئيسية القديمة (image_filename) باش التوافق يبقى خدام
        حتى للمنتجات اللي زدناها قبل ميزة الصور المتعددة.

        ⚠️ الصور التوضيحية **مستثناة** — عندها قسمها الخاص تحت المعرض.
        """
        if self.main_images:
            return [img.image_filename for img in self.main_images]
        if self.image_filename:
            return [self.image_filename]
        return []

    # ══════════════════════════════════════════════════════════
    # ترتيب الموديلات = ترتيب صورهم
    #
    # القاعدة: **الصور هي المرجع.** الموديل اللي صورتو أولى فالمعرض
    # يبان أول فقائمة «اختر الموديل»، والموديل المربوط بالصورة
    # الرئيسية (★) هو الافتراضي.
    #
    # علاش؟ باش الأدمن يرتّب حاجة وحدة برك (الصور) والباقي يتبع
    # لوحدو، بدل ما يرتّب الصور والموديلات كل واحد بوحدو ويتناقضو.
    #
    # الموديلات بلا صورة يمشيو **فالآخر**، بترتيبهم الأصلي.
    # الصور بلا موديل تبقى كيما هي فالمعرض — ما تأثّر على والو.
    # ══════════════════════════════════════════════════════════

    @property
    def unit_label(self):
        """اسم الوحدة معرّفًا: «الحبة» · «المتر» · «الرولو»…"""
        raw = (self.unit or "").strip()
        if not raw:
            return self.UNIT_LABELS[self.UNIT_PIECE]
        return self.UNIT_LABELS.get(raw, raw)

    @property
    def unit_short(self):
        """اسم الوحدة بلا «ال» — للاستعمال بعد رقم: «12 متر»."""
        label = self.unit_label
        return label[2:] if label.startswith("ال") else label

    @property
    def main_images(self):
        """صور المنتج الحقيقية — هي اللي فالمعرض وتتربط بالموديلات."""
        return [i for i in self.images if not i.is_extra]

    @property
    def extra_images(self):
        """
        صور توضيحية — كيفاش يتستعمل، وين يتحط.
        تبان تحت المعرض الرئيسي، وما تدخلش فترتيب الموديلات.
        """
        return [i for i in self.images if i.is_extra]

    @property
    def _image_rank(self):
        """
        خريطة: اسم ملف الصورة → ترتيبها فالمعرض.

        ⚠️ الصور التوضيحية مستثناة — ما تأثّرش على ترتيب الموديلات.
        """
        return {img.image_filename: i for i, img in enumerate(self.main_images)}

    @property
    def ordered_variants(self):
        """الموديلات مرتّبين حسب صورهم. استعملها بدل `variants` فالعرض."""
        if not self.variants:
            return []

        rank = self._image_rank
        far = len(rank) + 1000   # الموديلات بلا صورة تمشي للآخر

        def key(v):
            if v.image_filename and v.image_filename in rank:
                return (rank[v.image_filename], v.id)
            return (far, v.id)

        return sorted(self.variants, key=key)

    @property
    def default_variant(self):
        """
        الموديل المختار من البداية.

        الأولوية:
          1) الموديل المربوط بالصورة الرئيسية (★)
          2) الموديل المعلّم `is_default` فلوحة التحكم
          3) أول موديل فالترتيب الجديد
        """
        if not self.variants:
            return None

        if self.image_filename:
            for v in self.variants:
                if v.image_filename == self.image_filename:
                    return v

        for v in self.variants:
            if v.is_default:
                return v

        return self.ordered_variants[0]


# ==================== العروض الخاصة (Bundles / Special Offers) ====================

class Offer(db.Model):
    """
    عرض خاص: مجموعة منتجات تتباع مع بعض بسعر رمزي أقل من مجموع أسعارهم.
    مثلاً: "باقة تزيين المرايا" = 3 منتجات بـ2000 دج بدل 2800 دج.
    """

    __tablename__ = "offers"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(220), nullable=False, unique=True)
    subtitle = db.Column(db.String(300), nullable=True)  # سطر وصف قصير يبان تحت العنوان
    description = db.Column(db.Text, nullable=True)

    bundle_price = db.Column(db.Integer, nullable=False)  # السعر الرمزي للباقة كاملة
    banner_filename = db.Column(db.String(255), nullable=True)  # صورة البانر فـ static/img/offers/

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    # ---------- قسم "عرض خاص" بالصورة الكبيرة ----------
    # "none"   → ما يبانش فهاد القسم (عادي)
    # "top"    → فوق الصفحة، أول حاجة يشوفها الزبون
    # "bottom" → تحت الصفحة، فوق الفوتر
    # ⚠️ عرض واحد فقط يبان — إذا اخترت أكثر من واحد، ياخذ الأول حسب sort_order.
    spotlight_position = db.Column(db.String(10), nullable=False, default="none")

    # نص حر يبان جنب الصورة (اختياري — إذا خلّيتو فارغ يستعمل description)
    spotlight_text = db.Column(db.Text, nullable=True)

    starts_at = db.Column(db.DateTime, nullable=True)  # اختياري: بداية العرض
    ends_at = db.Column(db.DateTime, nullable=True)    # اختياري: نهاية العرض
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    items = db.relationship(
        "OfferItem",
        back_populates="offer",
        cascade="all, delete-orphan",
        order_by="OfferItem.id",
    )

    def __repr__(self):
        return f"<Offer {self.id} {self.title}>"

    @property
    def original_total(self):
        """مجموع أسعار المنتجات لو تشراو وحدة بوحدة (قبل تخفيض الباقة)."""
        return sum(item.line_total for item in self.items)

    @property
    def savings(self):
        """كم يربح الزبون بالدينار إذا شرا الباقة."""
        return max(0, self.original_total - self.bundle_price)

    @property
    def savings_percent(self):
        if self.original_total <= 0:
            return 0
        return round(self.savings / self.original_total * 100)

    @property
    def bundle_price_display(self):
        return f"{self.bundle_price:,} دج"

    @property
    def original_total_display(self):
        return f"{self.original_total:,} دج"

    @property
    def is_running(self):
        """
        واش العرض شغال درك: مفعّل + داخل المدة الزمنية (إذا محددة).
        التواريخ اختيارية — عرض بلا تواريخ يعتبر شغال دائمًا ما دام مفعّل.
        """
        if not self.is_active:
            return False
        now = utcnow()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True

    @property
    def is_available(self):
        """
        واش الباقة قابلة للبيع: كل منتج فيها عندو الكمية المطلوبة **بالضبط**.

        ⚠️ ماشي غير is_in_stock — باقة تطلب 3 قطع من منتج فيه قطعة وحدة
        ماشي متوفرة، حتى لو المنتج "فيه مخزون".
        """
        if not self.items:
            return False
        if not Product.is_stock_tracked:
            return True
        return all(
            item.product.available_quantity >= item.quantity for item in self.items
        )

    @property
    def max_orderable(self):
        """أقصى عدد باقات نقدرو نبيعو درك — مفيد باش نمنعو طلب أكثر من المتاح."""
        if not self.items:
            return 0
        if not Product.is_stock_tracked:
            return 9999
        return min(
            item.product.available_quantity // item.quantity
            for item in self.items
            if item.quantity > 0
        )


class OfferItem(db.Model):
    """منتج ضمن باقة عرض، مع الكمية المطلوبة منو."""

    __tablename__ = "offer_items"

    id = db.Column(db.Integer, primary_key=True)
    offer_id = db.Column(db.Integer, db.ForeignKey("offers.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)

    offer = db.relationship("Offer", back_populates="items")
    product = db.relationship("Product")

    @property
    def line_total(self):
        return self.product.price * self.quantity

    def __repr__(self):
        return f"<OfferItem offer={self.offer_id} product={self.product_id} x{self.quantity}>"


# ==================== شركات وأسعار التوصيل ====================

class Carrier(db.Model):
    """شركة توصيل (ZR Express, World Express, Yalidine...)."""

    __tablename__ = "carriers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(60), nullable=False, unique=True)  # zrexpress / worldexpress / yalidine
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    rates = db.relationship(
        "ShippingRate",
        back_populates="carrier",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Carrier {self.code}>"


class ShippingRate(db.Model):
    """
    سعر التوصيل لولاية معينة عند شركة معينة.
    home_price / office_price = null معناها الخدمة غير متوفرة لهاد الولاية عند هاد الشركة.
    """

    __tablename__ = "shipping_rates"
    __table_args__ = (
        db.UniqueConstraint("carrier_id", "wilaya_code", name="uq_carrier_wilaya"),
    )

    id = db.Column(db.Integer, primary_key=True)
    carrier_id = db.Column(db.Integer, db.ForeignKey("carriers.id"), nullable=False)
    wilaya_code = db.Column(db.String(4), nullable=False)   # "01", "16", "58"
    wilaya_name = db.Column(db.String(120), nullable=False)  # "أدرار"، "الجزائر"
    home_price = db.Column(db.Integer, nullable=True)   # التوصيل للمنزل
    office_price = db.Column(db.Integer, nullable=True)  # التوصيل للمكتب (Stop Desk)

    # ⚠️ معرّف الولاية عند الشركة (ZR يسميه territoryId).
    # كل شركة عندها ترقيمها الخاص وما يوافقش ترقيم الولايات الجزائري.
    # بلا هاد المعرّف، الطرد يتبعث بلا ولاية ويترفض عند الشركة.
    # يتعمّر تلقائيًا من زر "زامن الولايات" فصفحة التكاملات.
    carrier_territory_id = db.Column(db.String(60), nullable=True)

    carrier = db.relationship("Carrier", back_populates="rates")

    @property
    def wilaya_display(self):
        return f"{self.wilaya_code} ~ {self.wilaya_name}"

    def price_for(self, delivery_type):
        """delivery_type: 'home' أو 'office'. يرجع None إذا غير متوفر."""
        return self.office_price if delivery_type == "office" else self.home_price

    def __repr__(self):
        return f"<ShippingRate {self.wilaya_code} carrier={self.carrier_id}>"


# ==================== مستخدم لوحة التحكم ====================

class Commune(db.Model):
    """
    بلدية جزائرية — القائمة الرسمية الكاملة (1541 بلدية × 58 ولاية).

    ⚠️ ليش مستقلة عن شركة التوصيل؟
    البلديات تاع الجزائر ثابتة، ماشي ملك شركة. لو ربطناها بـZR، كنا:
      • نخسرو البلديات اللي ZR ما يوصلش ليها (والزبون ما يلقاش بلديتو)
      • نعاودو نجيبوها من الصفر لكل شركة جديدة

    ربط الشركة يصرا فجدول منفصل (CarrierCommune) — كل شركة تقول واش
    توصل لهاد البلدية وواش معرّفها عندها.

    المصدر: github.com/othmanus/algeria-cities
    """

    __tablename__ = "communes"
    __table_args__ = (
        db.UniqueConstraint("wilaya_code", "name_ar", name="uq_wilaya_commune"),
    )

    id = db.Column(db.Integer, primary_key=True)

    wilaya_code = db.Column(db.String(4), nullable=False, index=True)   # "01".."58"
    name_ar = db.Column(db.String(150), nullable=False)
    name_fr = db.Column(db.String(150), nullable=True)
    daira = db.Column(db.String(150), nullable=True)                    # الدائرة

    carrier_links = db.relationship(
        "CarrierCommune", back_populates="commune", cascade="all, delete-orphan"
    )

    @property
    def display_name(self):
        return self.name_ar or self.name_fr or "—"

    def __repr__(self):
        return f"<Commune {self.wilaya_code} {self.name_ar}>"


class CarrierCommune(db.Model):
    """
    ربط بلدية بشركة توصيل.

    فيها المعرّف عند الشركة (territoryId عند ZR) وواش الشركة توصل فعلاً
    لهاد البلدية. بلا سطر هنا = الشركة ما توصلش (ولا مازال ما زامنّاش).
    """

    __tablename__ = "carrier_communes"
    __table_args__ = (
        db.UniqueConstraint("carrier_id", "commune_id", name="uq_carrier_commune_link"),
    )

    id = db.Column(db.Integer, primary_key=True)
    carrier_id = db.Column(db.Integer, db.ForeignKey("carriers.id"), nullable=False, index=True)
    commune_id = db.Column(db.Integer, db.ForeignKey("communes.id"), nullable=False, index=True)

    # المعرّف عند الشركة — هو اللي يتبعث فطلب إنشاء الطرد
    external_id = db.Column(db.String(80), nullable=True)

    has_home_delivery = db.Column(db.Boolean, nullable=False, default=True)
    has_pickup_point = db.Column(db.Boolean, nullable=False, default=False)
    can_send = db.Column(db.Boolean, nullable=False, default=True)

    carrier = db.relationship("Carrier")
    commune = db.relationship("Commune", back_populates="carrier_links")

    def __repr__(self):
        return f"<CarrierCommune {self.carrier_id}:{self.commune_id}>"


class StoreSetting(db.Model):
    """
    إعدادات المتجر — جدول بسيط: مفتاح ← قيمة.

    ⚠️ ليش هكذا بدل عمود لكل إعداد؟ باش نزيدو إعدادات جديدة بلا ما نبدلو
    بنية قاعدة البيانات فكل مرة. القيم كلها نصوص، والدوال تحت تحوّلها.
    """

    __tablename__ = "store_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), nullable=False, unique=True, index=True)
    value = db.Column(db.Text, nullable=True)

    # ---------- القيم الافتراضية ----------
    # كي ما يكونش المفتاح فقاعدة البيانات، نستعملو هاذي.
    DEFAULTS = {
        "show_coupon_field": "1",   # خانة كود الخصم فالسلة
    }

    @staticmethod
    def get(key, default=None):
        """يجيب قيمة إعداد كنص."""
        row = StoreSetting.query.filter_by(key=key).first()
        if row is not None and row.value is not None:
            return row.value
        return StoreSetting.DEFAULTS.get(key, default)

    @staticmethod
    def get_bool(key):
        """يجيب إعداد كـTrue/False."""
        return StoreSetting.get(key) == "1"

    @staticmethod
    def set(key, value):
        """يحفظ إعداد. الحفظ فقاعدة البيانات يصرا مع commit برّا."""
        row = StoreSetting.query.filter_by(key=key).first()
        if row is None:
            row = StoreSetting(key=key)
            db.session.add(row)
        row.value = str(value)
        return row


class AdminUser(db.Model):
    """
    حساب أدمن للوحة التحكم.
    ⚠️ كلمة السر تتخزن مهشّرة (hash) فقط — أبدًا نص صريح.
    """

    __tablename__ = "admin_users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, raw_password):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash, raw_password)

    def __repr__(self):
        return f"<AdminUser {self.username}>"


# ==================== كوبونات الخصم ====================

class Coupon(db.Model):
    """
    كوبون خصم. نوعين:
      - percent : خصم بالنسبة المئوية (مثلاً 10%)
      - fixed   : خصم بمبلغ ثابت (مثلاً 500 دج)
      - shipping: توصيل مجاني
    """

    __tablename__ = "coupons"

    TYPE_PERCENT = "percent"
    TYPE_FIXED = "fixed"
    TYPE_FREE_SHIPPING = "shipping"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), nullable=False, unique=True)  # يتخزن بحروف كبيرة دائمًا
    discount_type = db.Column(db.String(20), nullable=False, default=TYPE_PERCENT)
    value = db.Column(db.Integer, nullable=False, default=0)  # نسبة (1-100) أو مبلغ بالدج

    min_order_amount = db.Column(db.Integer, nullable=False, default=0)  # الحد الأدنى للطلبية
    max_discount = db.Column(db.Integer, nullable=True)  # سقف الخصم (للنسبة المئوية)

    usage_limit = db.Column(db.Integer, nullable=True)  # عدد مرات الاستعمال الكلي (null = بلا حد)
    used_count = db.Column(db.Integer, nullable=False, default=0)

    starts_at = db.Column(db.DateTime, nullable=True)
    ends_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def __repr__(self):
        return f"<Coupon {self.code}>"

    @property
    def type_label(self):
        return {
            self.TYPE_PERCENT: "نسبة مئوية",
            self.TYPE_FIXED: "مبلغ ثابت",
            self.TYPE_FREE_SHIPPING: "توصيل مجاني",
        }.get(self.discount_type, self.discount_type)

    @property
    def value_display(self):
        if self.discount_type == self.TYPE_PERCENT:
            return f"{self.value}%"
        if self.discount_type == self.TYPE_FIXED:
            return f"{self.value:,} دج"
        return "توصيل مجاني"

    @property
    def is_exhausted(self):
        return self.usage_limit is not None and self.used_count >= self.usage_limit

    def check_validity(self, subtotal):
        """
        يتحقق واش الكوبون صالح لهاد الطلبية.
        يرجع (True, None) أو (False, "سبب الرفض بالعربية").

        نرجعو السبب باش الزبون يفهم واش لازمو يدير (مثلاً: زيد منتجات باش توصل للحد الأدنى)
        بدل رسالة عامة "كوبون غير صالح".
        """
        if not self.is_active:
            return False, "هذا الكوبون غير مفعّل"

        now = utcnow()
        if self.starts_at and now < self.starts_at:
            return False, "هذا الكوبون ما بدأش بعد"
        if self.ends_at and now > self.ends_at:
            return False, "انتهت صلاحية هذا الكوبون"

        if self.is_exhausted:
            return False, "هذا الكوبون استُعمل بالكامل"

        if subtotal < self.min_order_amount:
            missing = self.min_order_amount - subtotal
            return False, f"هذا الكوبون يحتاج طلبية بـ{self.min_order_amount:,} دج على الأقل (باقي {missing:,} دج)"

        return True, None

    def compute_discount(self, subtotal, shipping_price=0):
        """
        يحسب قيمة الخصم. يرجع (discount_on_products, discount_on_shipping).
        فصلناهم باش الفاتورة تبان واضحة للزبون.

        🔴 **الخصم ما يكونش سالبًا أبدًا.**
        فورم اللوحة يمنع القيم السالبة، لكن الكوبون يقدر يجي من
        مكان آخر (استيراد، سكربت، تعديل مباشر فقاعدة البيانات). ساعتها
        نسبة -20% كانت ترجّع خصمًا سالبًا — يعني **يزيد** فالفاتورة
        بدل ما ينقّص، والزبون يخلّص أكثر. هاد الفحص يمنعها نهائيًا.

        نفس الشي لـ`max_discount` السالب.
        """
        # ⚠️ قيم سالبة ولا فارغة = بلا خصم، ماشي خصم عكسي
        value = max(0, self.value or 0)
        subtotal = max(0, subtotal or 0)
        shipping_price = max(0, shipping_price or 0)

        if self.discount_type == self.TYPE_FREE_SHIPPING:
            return 0, shipping_price

        if self.discount_type == self.TYPE_FIXED:
            return min(value, subtotal), 0  # ما يتعداش قيمة الطلبية

        # نسبة مئوية
        discount = int(subtotal * value / 100)

        if self.max_discount is not None:
            discount = min(discount, max(0, self.max_discount))

        # الحد النهائي: بين 0 وقيمة الطلبية
        return max(0, min(discount, subtotal)), 0


# ==================== الطلبيات ====================

class Order(db.Model):
    """
    طلبية. ⚠️ ملاحظة تصميم مهمة:
    نخزّنو نسخة (snapshot) من اسم المنتج وسعره وقت الطلب — ماشي غير مرجع للمنتج.
    السبب: لو بدّلت السعر أو مسحت المنتج بعدين، الفاتورة القديمة تبقى صحيحة كما كانت.
    """

    __tablename__ = "orders"

    STATUS_PENDING = "pending"        # جديدة، تحتاج تأكيد
    STATUS_CONFIRMED = "confirmed"    # اتأكدت مع الزبون
    STATUS_PREPARING = "preparing"    # قاعدين نجهّزوها
    STATUS_PREPARED = "prepared"      # جاهزة، تستنى الشركة
    STATUS_SHIPPED = "shipped"        # اتسلّمت لشركة التوصيل
    STATUS_AT_OFFICE = "at_office"    # وصلت لمكتب الاستلام، تستنى الزبون
    STATUS_DELIVERED = "delivered"    # وصلت للزبون
    STATUS_RETURNED = "returned"      # رجعات لينا (ما تسلّمتش)
    STATUS_CANCELLED = "cancelled"    # ملغاة

    # ⚠️ الترتيب مهم — يستعملوه شريط التقدّم فصفحة «طلبياتي»
    # وقائمة الحالات فاللوحة.
    STATUS_FLOW = [
        STATUS_PENDING, STATUS_CONFIRMED, STATUS_PREPARING,
        STATUS_PREPARED, STATUS_SHIPPED, STATUS_AT_OFFICE, STATUS_DELIVERED,
    ]

    # ⚠️ «فالمكتب» تخص غير التوصيل للمكتب. فتوصيل الدار، الطرد يمشي
    # من «فالطريق» لـ«تم التسليم» مباشرة — فما نوريوش للزبون مرحلة
    # ما تجيش عمرها.
    STATUS_OFFICE_ONLY = {STATUS_AT_OFFICE}

    # حالات نهائية برّا المسار — الطلبية واقفة، ماشي قدّامة
    STATUS_TERMINAL = {STATUS_CANCELLED, STATUS_RETURNED}

    STATUS_LABELS = {
        STATUS_PENDING: "جديدة",
        STATUS_CONFIRMED: "مؤكدة",
        STATUS_PREPARING: "قيد التجهيز",
        STATUS_PREPARED: "تم التجهيز",
        STATUS_SHIPPED: "فالطريق",
        STATUS_AT_OFFICE: "فالمكتب",
        STATUS_DELIVERED: "تم التسليم",
        STATUS_RETURNED: "راجعة",
        STATUS_CANCELLED: "ملغاة",
    }

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), nullable=False, unique=True)  # رقم يعطى للزبون

    customer_name = db.Column(db.String(150), nullable=False)
    customer_phone = db.Column(db.String(30), nullable=False)

    wilaya_code = db.Column(db.String(4), nullable=False)
    wilaya_name = db.Column(db.String(120), nullable=False)
    commune = db.Column(db.String(150), nullable=True)

    # معرّف البلدية عند الشركة — يتبعث كـterritoryId عند إنشاء الطرد.
    # نخزّنوه مع الطلبية باش الإرسال يخدم حتى لو تبدّلت البيانات بعدين.
    commune_external_id = db.Column(db.String(80), nullable=True)

    # نقطة الاستلام (للتوصيل للمكتب) — تُختار من قائمة الشركة
    pickup_point_id = db.Column(db.String(80), nullable=True)

    # ⚠️ UUID تاع المكتب عند ZR (من `POST /hubs/search`). منفصل على
    # `pickup_point_id` اللي هو **الاسم** اللي يشوفو الزبون.
    # ZR يستنّى المعرّف، والاسم كان يترفض بـ400 فارغ.
    hub_external_id = db.Column(db.String(64), nullable=True)

    # ⚠️ UUID تاع **الولاية** عند ZR (`cityTerritoryId`). البلدية
    # محفوظة فـ`commune_external_id` (`districtTerritoryId`).
    wilaya_external_id = db.Column(db.String(64), nullable=True)

    # ⚠️ معرّف الزبون عند ZR — الطرد لازم يشير لزبون موجود عندهم
    # («CustomerId is required»)
    customer_external_id = db.Column(db.String(64), nullable=True)
    pickup_point_name = db.Column(db.String(200), nullable=True)

    address_note = db.Column(db.Text, nullable=True)

    carrier_code = db.Column(db.String(60), nullable=True)
    carrier_name = db.Column(db.String(120), nullable=True)
    delivery_type = db.Column(db.String(20), nullable=False, default="home")  # home / office

    subtotal = db.Column(db.Integer, nullable=False, default=0)          # مجموع المنتجات
    shipping_price = db.Column(db.Integer, nullable=False, default=0)
    discount_amount = db.Column(db.Integer, nullable=False, default=0)   # خصم على المنتجات
    shipping_discount = db.Column(db.Integer, nullable=False, default=0) # خصم على التوصيل
    total = db.Column(db.Integer, nullable=False, default=0)

    coupon_code = db.Column(db.String(50), nullable=True)

    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    customer_note = db.Column(db.Text, nullable=True)
    admin_note = db.Column(db.Text, nullable=True)

    # ---------- التتبّع عند شركة التوصيل ----------
    tracking_number = db.Column(db.String(80), nullable=True)   # رقم التتبّع
    carrier_parcel_id = db.Column(db.String(80), nullable=True)  # معرّف الطرد عندهم

    # ══════════════════════════════════════════════════════════
    # حالة الإرسال لشركة التوصيل — حماية من الإرسال المزدوج
    #
    # ⚠️ المشكل اللي تحلّ: الكود القديم كان يفحص `if tracking_number`
    # ثم ينادي API ZR (ثواني)، ثم يحفظ الرقم. نقرة مزدوجة (ولا
    # اتصال بطيء على الجوال) = طلبين متوازيين، الاثنين يشوفو الرقم
    # فارغًا، الاثنين يخلقو طردًا. طردان حقيقيان، تخليص مرتين.
    #
    # الحل: نحجزو الطلبية ذريًا (UPDATE مشروط) قبل ما نمسّو ZR.
    #
    # علاش حقل حالة وماشي نكتبو "PENDING" فـtracking_number؟
    # لأن API يقدر يفشل ولا العامل يموت وسط العملية — ساعتها
    # الطلبية تبقى عالقة للأبد بحالة وهمية. بحقل منفصل + وقت البداية،
    # نقدرو نعرفو أن الحجز قديم ونسمحو بإعادة المحاولة.
    # ══════════════════════════════════════════════════════════

    ZR_IDLE = "idle"            # ما تبعثاتش بعد
    ZR_SENDING = "sending"      # محجوزة درك، طلب فالطريق
    ZR_SENT = "sent"            # نجحت — عندها رقم تتبّع
    ZR_FAILED = "failed"        # فشلت — يقدر يعاود

    # الحجز اللي قعد أكثر من هاد المدة نعتبروه ميتًا ونسمحو بإعادة
    # المحاولة (العامل طاح ولا الشبكة انقطعت وسط العملية).
    ZR_CLAIM_TIMEOUT_SECONDS = 180

    send_state = db.Column(db.String(20), nullable=False, default=ZR_IDLE)
    send_started_at = db.Column(db.DateTime, nullable=True)
    send_error = db.Column(db.String(300), nullable=True)
    carrier_state = db.Column(db.String(120), nullable=True)     # آخر حالة معروفة

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    items = db.relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Order {self.order_number}>"

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status)

    @property
    def delivery_type_label(self):
        return "التوصيل للمكتب" if self.delivery_type == "office" else "التوصيل للمنزل"

    @property
    def total_display(self):
        return f"{self.total:,} دج"

    @property
    def item_count(self):
        return sum(item.quantity for item in self.items)

    # ══════════════════════════════════════════════════════════
    # التواصل مع الزبون عبر الواتساب
    #
    # ⚠️ علاش فالنموذج ماشي فالقالب؟
    # الرسالة تحتاج بيانات الطلبية (المنتجات، المجموع، التتبّع).
    # بناؤها فالقالب يخلّي Jinja معقّدًا وصعيب يتصلّح. وهنا كذلك
    # نقدرو نختبروها.
    # ══════════════════════════════════════════════════════════

    def recalculate(self):
        """
        يعيد حساب المجاميع من العناصر الحالية.

        ⚠️ لازم تتنادى بعد **أي** تعديل على عناصر الطلبية (زيادة، حذف،
        تبديل كمية). بلاها، `subtotal` و`total` يبقاو على القيم القديمة
        والفاتورة تولّي غالطة.

        الخصم: إذا كان بنسبة مئوية، نعيدو حسابو على المجموع الجديد —
        وإلا زبون يزيد منتجًا ويبقى الخصم محسوبًا على المجموع القديم.
        الخصم الثابت يبقى كيما هو، لكن ما يتعدّاش المجموع الجديد.
        """
        self.subtotal = sum(item.line_total for item in self.items)

        # نعيدو حساب الخصم إذا كاين كوبون
        if self.coupon_code:
            coupon = Coupon.query.filter_by(code=self.coupon_code).first()
            if coupon:
                discount, ship_discount = coupon.compute_discount(
                    self.subtotal, self.shipping_price or 0
                )
                self.discount_amount = discount
                self.shipping_discount = ship_discount

        # الخصم ما يتعدّاش المجموع مهما كان
        self.discount_amount = max(0, min(self.discount_amount or 0, self.subtotal))
        self.shipping_discount = max(
            0, min(self.shipping_discount or 0, self.shipping_price or 0)
        )

        self.total = max(
            0,
            self.subtotal
            - (self.discount_amount or 0)
            + (self.shipping_price or 0)
            - (self.shipping_discount or 0),
        )

        return self.total

    @property
    def progress_steps(self):
        """
        مراحل الطلبية للشريط اللي يشوفو الزبون.

        كل مرحلة: (المفتاح، الاسم، واش تعدّات، واش هي الحالية).
        الطلبية الملغاة ما عندهاش تقدّم — نرجعو قائمة فارغة والقالب
        يوري رسالة إلغاء بدلها.
        """
        if self.status in self.STATUS_TERMINAL:
            return []

        # نحيّدو المراحل اللي ما تخصّش نوع التوصيل تاع هاد الطلبية
        flow = [
            key for key in self.STATUS_FLOW
            if key not in self.STATUS_OFFICE_ONLY or self.delivery_type == "office"
        ]

        try:
            current = flow.index(self.status)
        except ValueError:
            current = 0

        return [
            {
                "key": key,
                "label": self.STATUS_LABELS[key],
                "done": i < current,
                "active": i == current,
            }
            for i, key in enumerate(flow)
        ]

    @property
    def customer_can_edit(self):
        """
        واش الزبون بنفسو يقدر يعدّل الطلبية؟

        ⚠️ أضيق من `is_editable` تاع الأدمن: الزبون يوقف عند «فالطريق».
        بعد ما تخرج للتوصيل، التعديل يحتاج اتصال — ما نخلّيوهش يبدّل
        حاجة وحنا ما نقدروش نلحقو.
        """
        return self.status in (self.STATUS_PENDING, self.STATUS_CONFIRMED)

    @property
    def is_editable(self):
        """
        واش نقدرو نعدّلو الطلبية؟

        ⚠️ بعد ما تتبعث لشركة التوصيل، التعديل عندنا ما يوصلهمش —
        المبلغ اللي يحوّزو من الزبون يبقى القديم. نمنعو التعديل باش
        ما يبانش المتجر وZR بمبلغين مختلوين.
        """
        if self.status == self.STATUS_DELIVERED or self.status in self.STATUS_TERMINAL:
            return False
        return not self.tracking_number

    @property
    def whatsapp_phone(self):
        """
        رقم الزبون بالصيغة الدولية اللي يقبلها الواتساب.

        الأرقام الجزائرية تتكتب `0555...` محليًا، والواتساب يحتاج
        `213555...` بلا صفر ولا مسافات ولا رموز.
        """
        digits = "".join(ch for ch in (self.customer_phone or "") if ch.isdigit())

        if not digits:
            return ""

        if digits.startswith("00213"):
            return digits[2:]
        if digits.startswith("213"):
            return digits
        if digits.startswith("0"):
            return "213" + digits[1:]

        return "213" + digits

    def _items_lines(self, with_links=False, base_url=""):
        """
        سطر لكل منتج فالطلبية — يستعملوه أكثر من قالب رسالة.

        ⚠️ `with_links`: نزيدو رابط صفحة المنتج تحت كل سطر.

        علاش روابط ماشي صور؟ رابط `wa.me` يبعث **نصًا برك** — ما
        يقدرش يرفق صورًا، هذا حد فالواتساب ماشي فينا. لكن الواتساب
        يوري **معاينة** لأول رابط فالرسالة (صورة + عنوان)، فالزبون
        يشوف المنتج بلا ما يخرج.
        """
        lines = []
        for item in self.items:
            name = item.item_name
            if item.variant_label:
                name += f" ({item.variant_label})"
            lines.append(f"• {name} × {item.quantity}")

            # ⚠️ `OrderItem` ما عندوش علاقة `product` — نجيبو المنتج
            # بالـid. وممكن يكون انمحى، فنتحققو قبل.
            if with_links and base_url and item.product_id:
                product = db.session.get(Product, item.product_id)
                if product is not None and product.slug:
                    lines.append(f"  {base_url}/product/{product.slug}")

        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════
    # الإيموجي بترميز ASCII (\U0001F44B) ماشي بالحرف المباشر.
    #
    # 🔴 علاش؟ الإيموجي المكتوب مباشرة يتخرّب بسهولة: محرّر يحفظ
    # بترميز غالط، رفع لـGitHub، نسخ ولصق… والنتيجة «�» عند الزبون.
    # الهروب ASCII يخلّي الملف نظيفًا مهما مرّ عليه، وPython يفكّو
    # للحرف الصحيح عند التشغيل.
    # ══════════════════════════════════════════════════════════
    # ⚠️ كل إيموجي هنا من النطاق الأساسي (BMP) = **3 بايت** فـUTF-8،
    # نفس حجم الحرف العربي اللي يوصل مليح.
    #
    # 🔴 علاش؟ الإيموجي المعروف (\U0001F44B وإخوانو) ياخذ **4 بايت**،
    # والطريق بين رابط wa.me والواتساب يقطعو ويوريه «\u25C6». جرّبناه
    # بالحرف المباشر وبهروب ASCII وتخرّب فالجوج — المشكل فالحجم ماشي
    # فطريقة الكتابة. العربية (بايتين) توصل، فالـ3 بايت تعدّي معاها.
    #
    # ⚠️ قبل ما تزيد أي إيموجي هنا: تأكد أنو 3 بايت.
    #     len("X".encode("utf-8")) == 3
    # ⚠️ هاذو رموز **نصّية**، ماشي إيموجي. الفرق مهم:
    #
    # 🔴 الدليل من رسالة حقيقية: ⚑ (\u2691) بانت مليح، و☑ (\u2611)
    #     و✔ (\u2714) بانو «\u25C6» — والثلاثة 3 بايت. فالمشكل ماشي
    #     فالترميز (هادي كانت نظرية غالطة تاعي).
    #
    # السبب الحقيقي: الرموز اللي عندها **نسخة إيموجي** فيونيكود
    # (☑ ✔ ✅ ⭐ ❤ ⏰ ⚠ 👋 📦...) الواتساب يبدّلها بصورة من مكتبتو.
    # كي الصورة ما تتحمّلش، تبان معيّنة. أما الرموز النصّية (★ ♥ ✓ ✆ ⚑)
    # يتكتبو بالخط العادي كيما الحروف — وما عمرهم يطيحو.
    #
    # ⚠️ قبل ما تزيد أي رمز هنا: تأكد أنو **ماكانش عندو نسخة إيموجي**.
    #     القاعدة البسيطة: إذا يبان ملوّنًا فأي تطبيق = ممنوع.
    #     المسموح: النطاقات 2190-27BF النصّية + 2600-26FF بلا إيموجي.
    EMOJI = {
        "check": "\u2713",         # ✓ صح (ماشي \u2714 ولا \u2705)
        "box": "\u25AA",           # ▪ طلبية
        "truck": "\u27A4",         # ➤ فالطريق
        "pin": "\u2691",           # ⚑ العنوان — مجرّبة وواصلة
        "phone": "\u2706",         # ✆ اتصال
        "clock": "\u00BB",         # » وقت
        "money": "\u25C9",         # ◉ مبلغ
        # ⚠️ حتى ♥ (\u2665) ممنوعة — الواتساب يلوّنها. ✿ نصّية 100%.
        "heart": "\u273F",         # ✿ شكر
        "star": "\u2605",          # ★ تقييم (ماشي \u2B50)
        "warn": "\u2731",          # ✱ تنبيه (ماشي \u26A0)
        "back": "\u00AB",          # « رجوع (ماشي \u21A9)
        "sparkles": "\u2736",      # ✶
        "write": "\u270E",         # ✎ كتابة
    }



    def whatsapp_message(self, kind, store_name="Budac", base_url=""):
        """
        يبني نص رسالة جاهزة حسب الحاجة.

        الأنواع:
          confirm  — تأكيد الطلبية مع الزبون قبل الإرسال
          shipped  — الطلبية مشات، مع رقم التتبّع
          arrived  — وصلت لمكتب الاستلام
          returned — الطرد رجع لينا، نتفاهمو على إعادة الإرسال
          modify   — نبغيو نبدّلو شي فالطلبية
          missing  — منتج ما بقاش موجود
          noreply  — ما جاوبش على الهاتف
          thanks   — شكر بعد التسليم
          review   — طلب رأي/تقييم للمتجر (يحتاج base_url باش يبني الرابط)

        ⚠️ الإيموجي كلو من `EMOJI` (3 بايت) — شوف الشرح فوق القائمة.
        وعمدًا قليل: إيموجي واحد ولا زوج فالرسالة. الرسالة المزيّنة
        بزاف تبان إعلانًا آليًا والزبون ما يردّش عليها.
        """
        e = self.EMOJI
        name = self.customer_name or "خويا"
        num = self.order_number
        total = self.total_display

        # ⚠️ الروابط غير فرسائل المراجعة (تأكيد/تعديل) — فيهم الزبون
        # يحتاج يشوف واش طلب. فرسالة الشحن يزيدو ضجيجًا برك.
        items = self._items_lines(with_links=bool(base_url), base_url=base_url)

        greeting = f"سلام {name}،\nمعاك {store_name}."

        if kind == "confirm":
            return (
                f"{greeting}\n\n"
                f"{e['box']} وصلاتنا طلبيتك رقم *{num}*:\n\n"
                f"{items}\n\n"
                f"{e['money']} المجموع مع التوصيل: *{total}*\n"
                f"{e['pin']} {self.delivery_type_label} - {self.wilaya_name}\n\n"
                f"نأكّدو الطلبية؟"
            )

        if kind == "shipped":
            tracking = (
                f"\nرقم التتبّع: *{self.tracking_number}*"
                if self.tracking_number else ""
            )
            return (
                f"{greeting}\n\n"
                f"{e['truck']} طلبيتك *{num}* خرجات اليوم مع "
                f"{self.carrier_name or 'شركة التوصيل'}، بإذن الله."
                f"{tracking}\n\n"
                f"{e['clock']} توصلك فحدود 24 إلى 48 ساعة، خلّي هاتفك "
                f"مفتوح باش يتصل بيك الموزّع.\n\n"
                f"{e['money']} المبلغ عند الاستلام: *{total}*"
            )

        if kind == "arrived":
            where = self.pickup_point_name or self.wilaya_name
            return (
                f"{greeting}\n\n"
                f"{e['check']} طلبيتك *{num}* وصلات لـ*{where}*.\n\n"
                f"تقدر تروح تحوّزها - جيب معاك بطاقة التعريف.\n"
                f"{e['money']} المبلغ: *{total}*\n\n"
                f"نستنّاوك."
            )

        if kind == "returned":
            # ⚠️ رسالة حسّاسة — الزبون ما حوّزش الطرد. النبرة تبقى
            # محايدة بلا لوم، والهدف نعرفو واش راح ونعاودو نبعثو.
            return (
                f"{greeting}\n\n"
                f"{e['back']} طلبيتك *{num}* رجعات لينا بلا ما تتسلّم.\n\n"
                f"إذا مازال تحبها، قوللي ونعاودو نبعثوها - نتأكدو غير "
                f"من العنوان والوقت المناسب.\n\n"
                f"وإذا بدّلت رأيك، ما كاين والو، غير عرّفني."
            )

        if kind == "modify":
            return (
                f"{greeting}\n\n"
                f"{e['box']} بخصوص طلبيتك *{num}*:\n\n"
                f"{items}\n\n"
                f"{e['money']} المجموع: *{total}*\n\n"
                f"حبيت نسقسيك - تحب تزيد ولا تنقّص شي حاجة قبل ما نبعثوها؟"
            )

        if kind == "missing":
            return (
                f"{greeting}\n\n"
                f"{e['warn']} بخصوص طلبيتك *{num}* - نعتذرلك، كاين منتج "
                f"ما بقاش موجود درك.\n\n"
                f"عندك خيارين:\n"
                f"1. نبدّلوه بحاجة أخرى تعجبك\n"
                f"2. نبعثو الباقي ونحيّدو ثمنو من المجموع\n\n"
                f"واش تفضّل؟"
            )

        if kind == "noreply":
            return (
                f"{greeting}\n\n"
                f"{e['phone']} حاولنا نتصلو بيك على طلبيتك *{num}* "
                f"وما لقيناكش.\n\n"
                f"قوللي وقتاش يريحك ونعاودو نتصلو، ولا جاوبني هنا "
                f"ونأكّدوها مباشرة."
            )

        if kind == "thanks":
            return (
                f"سلام {name}،\n\n"
                f"{e['heart']} يعطيك الصحة على ثقتك فينا، ونتمنى المنتج "
                f"عجبك.\n\n"
                f"إذا عندك أي ملاحظة، قوللي - رأيك يهمّنا.\n\n"
                f"ونفرحو بيك مرة أخرى فـ{store_name}."
            )

        if kind == "review":
            # ⚠️ الرابط لازم يكون كاملاً — الواتساب ما يخدمش رابطًا نسبيًا،
            # وإذا ما وصلش base_url نستعملو الدومين الرسمي بدل رابط مكسور.
            link = f"{base_url.rstrip('/')}/reviews" if base_url else "https://budacdz.com/reviews"
            return (
                f"{greeting}\n\n"
                f"{e['check']} طلبيتك رقم *{num}* وصلاتك، ونتمنى تكون "
                f"كيما تستنّى.\n\n"
                f"{e['star']} إذا عندك دقيقة، رأيك على المنتج والخدمة "
                f"يهمّنا - يفيد الزبائن الجدد ويعاوننا نتحسّنو:\n"
                f"{link}\n\n"
                f"وإذا كاينة شي ملاحظة، ابعثهالي هنا مباشرة ونتكفّلو بيها.\n\n"
                f"{e['heart']} شكرًا على ثقتك."
            )

        # نوع مجهول — رسالة عامة أحسن من خطأ
        return f"{greeting}\n\nبخصوص طلبيتك *{num}*..."

    @property
    def is_open(self):
        """واش الطلبية مازالت نشطة (ماشي مسلّمة ولا ملغاة)."""
        return (self.status != self.STATUS_DELIVERED
                and self.status not in self.STATUS_TERMINAL)


class OrderItem(db.Model):
    """سطر فالطلبية — بنسخة محفوظة من بيانات المنتج وقت الشراء."""

    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)

    # مراجع اختيارية (تولي null إذا انمسح المنتج) — البيانات المحفوظة تحت هي المصدر الموثوق
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=True)
    variant_id = db.Column(db.Integer, db.ForeignKey("product_variants.id"), nullable=True)
    offer_id = db.Column(db.Integer, db.ForeignKey("offers.id"), nullable=True)

    # نسخة محفوظة وقت الطلب
    item_name = db.Column(db.String(300), nullable=False)
    variant_label = db.Column(db.String(150), nullable=True)
    unit_price = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    image_filename = db.Column(db.String(255), nullable=True)

    order = db.relationship("Order", back_populates="items")

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    @property
    def line_total_display(self):
        return f"{self.line_total:,} دج"

    def __repr__(self):
        return f"<OrderItem {self.item_name} x{self.quantity}>"


# ==================== السلات المتروكة ====================

class AbandonedCart(db.Model):
    """
    سلة متروكة: زبون بدا يعمّر بياناتو فالـcheckout وما كملش الطلب.

    ⚠️ الفرق عن YouCan: هما يبعثو إيميل. فالجزائر الإيميل ضعيف —
    نحنا نسجّلو **رقم الهاتف** والأدمن يتصل ولا يبعث واتساب مباشرة.
    نسبة الاستجابة أعلى بكثير.

    نسجّل السطر بمجرد ما الزبون يكتب اسمو وهاتفو (قبل ما يضغط "أكّد")،
    ونحيّدوه تلقائيًا إذا كمّل الطلب.
    """

    __tablename__ = "abandoned_carts"

    STATUS_NEW = "new"                # جديدة، ما تواصلناش
    STATUS_CONTACTED = "contacted"    # تواصلنا مع الزبون
    STATUS_RECOVERED = "recovered"    # كمّل الطلب بعد التواصل
    STATUS_LOST = "lost"              # ما ردّش / رفض

    STATUS_LABELS = {
        STATUS_NEW: "جديدة",
        STATUS_CONTACTED: "تواصلنا",
        STATUS_RECOVERED: "استُرجعت",
        STATUS_LOST: "ضائعة",
    }

    id = db.Column(db.Integer, primary_key=True)

    customer_name = db.Column(db.String(150), nullable=True)
    customer_phone = db.Column(db.String(30), nullable=False, index=True)

    wilaya_name = db.Column(db.String(120), nullable=True)
    commune = db.Column(db.String(150), nullable=True)

    cart_json = db.Column(db.Text, nullable=False)   # نسخة من محتوى السلة
    cart_total = db.Column(db.Integer, nullable=False, default=0)
    item_count = db.Column(db.Integer, nullable=False, default=0)

    status = db.Column(db.String(20), nullable=False, default=STATUS_NEW)
    admin_note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status)

    @property
    def items(self):
        """محتوى السلة كقائمة — من الـJSON المحفوظ."""
        import json
        try:
            return json.loads(self.cart_json)
        except Exception:
            return []

    @property
    def total_display(self):
        return f"{self.cart_total:,} دج"

    @property
    def whatsapp_link(self):
        """
        رابط واتساب برسالة جاهزة — الأدمن ينقر وينطلق التواصل فورًا.
        نحوّلو الرقم المحلي (0XXX) لصيغة دولية (213XXX).
        """
        from urllib.parse import quote

        phone = "".join(c for c in (self.customer_phone or "") if c.isdigit())
        if phone.startswith("0"):
            phone = "213" + phone[1:]
        elif not phone.startswith("213"):
            phone = "213" + phone

        names = "، ".join(item.get("name", "") for item in self.items[:3])
        greeting = f"سلام {self.customer_name}،" if self.customer_name else "سلام،"
        message = (
            f"{greeting} لاحظنا أنك كنت بصدد طلب: {names}. "
            "واش تحتاج مساعدة باش تكمّل طلبيتك؟"
        )
        return f"https://wa.me/{phone}?text={quote(message)}"

    def __repr__(self):
        return f"<AbandonedCart {self.customer_phone}>"


# ==================== تقييمات المنتجات ====================

class Review(db.Model):
    """
    تقييم زبون — لمنتج معيّن ولا للمتجر كامل.

    ⚠️ كل تقييم يمر على موافقة الأدمن قبل ما يبان (is_approved).
    السبب: بلا هذا، أي واحد يقدر يكتب سبام أو إساءة ويبان مباشرة فمتجرك.

    ⚠️ `product_id = None` معناها **تقييم للمتجر** ماشي لمنتج.
    علاش نفس الجدول بدل جدول ثانٍ؟ الحقول واحدة (اسم، نجوم، تعليق،
    موافقة)، ولوحة المراجعة تبقى وحدة. جدول ثانٍ يعني تكرار الكود
    والقوالب بلا فائدة.
    """

    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True)
    # فارغ = تقييم للمتجر كامل
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"),
                           nullable=True)

    author_name = db.Column(db.String(120), nullable=False)
    rating = db.Column(db.Integer, nullable=False)   # من 1 لـ5
    comment = db.Column(db.Text, nullable=True)

    is_approved = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    product = db.relationship("Product", backref=db.backref("reviews", cascade="all, delete-orphan"))

    @property
    def stars(self):
        """نجوم للعرض: ★★★★☆"""
        return "★" * self.rating + "☆" * (5 - self.rating)

    @property
    def is_store_review(self):
        """واش تقييم للمتجر كامل ماشي لمنتج؟"""
        return self.product_id is None

    @property
    def target_label(self):
        """اسم اللي اتقيّم — للوحة المراجعة."""
        if self.is_store_review:
            return "المتجر"
        return self.product.name if self.product else "منتج محذوف"

    # ---------- إحصائيات المتجر ----------

    @staticmethod
    def store_stats():
        """
        (المعدّل، العدد) لتقييمات المتجر المقبولة.

        نرجّعو (0, 0) إذا ماكانش — القالب يخفي القسم بدل ما يوري
        «0 من 5» وهذا يضرّ أكثر ما ينفع.
        """
        rows = Review.query.filter(
            Review.product_id.is_(None), Review.is_approved.is_(True)
        ).all()
        if not rows:
            return 0, 0
        return round(sum(r.rating for r in rows) / len(rows), 1), len(rows)

    def __repr__(self):
        return f"<Review {self.rating}★ product={self.product_id}>"


# ==================== الـUpsell (منتجات مقترحة) ====================

class UpsellRule(db.Model):
    """
    قاعدة اقتراح: "كي يشري المنتج X، اقترح عليه Y".

    ⚠️ الفرق عن YouCan: هما يعرضو الاقتراح **بعد** الشراء.
    نحنا نعرضوه **فالسلة** — الزبون مازال فوضع شراء، فاحتمال القبول أعلى،
    وما نخربوش عليه فرحة إتمام الطلب.
    """

    __tablename__ = "upsell_rules"

    id = db.Column(db.Integer, primary_key=True)

    # المنتج المُشغِّل. إذا None، القاعدة تنطبق على كل السلات (اقتراح عام)
    trigger_product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=True)
    suggested_product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)

    discount_percent = db.Column(db.Integer, nullable=False, default=0)  # خصم تحفيزي (اختياري)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    trigger_product = db.relationship("Product", foreign_keys=[trigger_product_id])
    suggested_product = db.relationship("Product", foreign_keys=[suggested_product_id])

    @property
    def is_general(self):
        return self.trigger_product_id is None

    def __repr__(self):
        return f"<UpsellRule {self.trigger_product_id} → {self.suggested_product_id}>"


class LandingPage(db.Model):
    """
    صفحة هبوط لحملة إعلانية — منتج واحد + فورم طلب مباشر.

    ⚠️ ليش منفصلة عن صفحة المنتج العادية؟
    صفحة المنتج فيها هيدر، تصنيفات، منتجات مقترحة، سلة... كلها تشتّت
    الزائر اللي جاي من إعلان. صفحة الهبوط تحذف كلش وتخلي هدف واحد:
    يعمّر الفورم. هذا يرفع نسبة التحويل بشكل ملحوظ.

    الفورم يُنشئ الطلبية مباشرة — بلا مرور بالسلة.
    """
    __tablename__ = "landing_pages"

    id = db.Column(db.Integer, primary_key=True)

    # الرابط: /lp/<slug>  — قصير وواضح باش يتحط فالإعلان
    slug = db.Column(db.String(160), nullable=False, unique=True, index=True)
    internal_name = db.Column(db.String(160), nullable=False)   # للأدمن فقط

    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    variant_id = db.Column(db.Integer, db.ForeignKey("product_variants.id"), nullable=True)

    # ---------- المحتوى ----------
    headline = db.Column(db.String(200), nullable=False)
    subheadline = db.Column(db.String(300), nullable=True)
    body = db.Column(db.Text, nullable=True)          # وصف مطوّل — سطر لكل فقرة
    benefits = db.Column(db.Text, nullable=True)      # مميزات — سطر لكل وحدة
    cta_text = db.Column(db.String(80), nullable=False, default="اطلب الآن")

    # صورة الغلاف. إذا فارغة، ناخذو صورة المنتج
    hero_image = db.Column(db.String(255), nullable=True)

    # ---------- السعر ----------
    # سعر خاص بالحملة. إذا None، ناخذو سعر المنتج العادي
    campaign_price = db.Column(db.Integer, nullable=True)
    compare_at_price = db.Column(db.Integer, nullable=True)   # السعر المشطوب

    # ---------- عناصر الإقناع ----------
    show_countdown = db.Column(db.Boolean, nullable=False, default=False)
    countdown_hours = db.Column(db.Integer, nullable=False, default=24)
    stock_warning = db.Column(db.String(120), nullable=True)   # "باقي 7 قطع فقط"
    free_shipping = db.Column(db.Boolean, nullable=False, default=False)

    # ---------- إعدادات ----------
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    max_quantity = db.Column(db.Integer, nullable=False, default=10)

    # ---------- إحصائيات ----------
    view_count = db.Column(db.Integer, nullable=False, default=0)
    order_count = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    product = db.relationship("Product", foreign_keys=[product_id])
    variant = db.relationship("ProductVariant", foreign_keys=[variant_id])

    @property
    def unit_price(self):
        """السعر المعتمد: سعر الحملة، وإلا سعر التنويعة، وإلا سعر المنتج."""
        if self.campaign_price:
            return self.campaign_price
        if self.variant and self.variant.price:
            return self.variant.price
        return self.product.price if self.product else 0

    @property
    def image_filename(self):
        """صورة الغلاف، وإلا صورة المنتج."""
        if self.hero_image:
            return self.hero_image
        return self.product.image_filename if self.product else None

    @property
    def benefit_lines(self):
        """المميزات كقائمة — سطر فارغ يتجاهل."""
        if not self.benefits:
            return []
        return [ln.strip() for ln in self.benefits.splitlines() if ln.strip()]

    @property
    def body_paragraphs(self):
        """فقرات الوصف."""
        if not self.body:
            return []
        return [ln.strip() for ln in self.body.splitlines() if ln.strip()]

    @property
    def has_discount(self):
        return bool(self.compare_at_price and self.compare_at_price > self.unit_price)

    @property
    def discount_percent(self):
        if not self.has_discount:
            return 0
        return round((1 - self.unit_price / self.compare_at_price) * 100)

    @property
    def conversion_rate(self):
        """نسبة التحويل — كم زائر ولّى زبون."""
        if not self.view_count:
            return 0
        return round(self.order_count / self.view_count * 100, 1)

    def __repr__(self):
        return f"<LandingPage {self.slug}>"


class SiteVisit(db.Model):
    """
    زوّار الموقع — مجمّعة باليوم.

    ⚠️ ليش هاد التصميم وماشي سطر لكل زيارة؟
    سطر لكل زيارة يعمّر قاعدة البيانات بسرعة (100 زائر × 10 صفحات =
    1000 سطر فاليوم). هنا **سطر واحد لكل زائر فاليوم**، وعدد صفحاتو
    يتزاد فنفس السطر. يعني:
        عدد الزوار الفريدين = عدد الأسطر تاع هاد اليوم
        عدد الزيارات        = مجموع views

    ⚠️ الخصوصية: ما نخزّنوش IP ولا أي شي يعرّف الزائر. نخزّنو بصمة
    مشفّرة (hash) تتبدّل كل يوم — كافية باش نفرقو بين الزوار فنفس
    اليوم، وما تسمحش نتبعو زائر عبر الأيام.
    """
    __tablename__ = "site_visits"

    id = db.Column(db.Integer, primary_key=True)

    day = db.Column(db.Date, nullable=False, index=True)
    visitor_key = db.Column(db.String(40), nullable=False)

    views = db.Column(db.Integer, nullable=False, default=1)
    first_page = db.Column(db.String(300), nullable=True)   # من وين دخل
    referrer = db.Column(db.String(300), nullable=True)     # منين جا

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("day", "visitor_key", name="uq_visit_day_key"),
        db.Index("ix_visit_day_key", "day", "visitor_key"),
    )

    def __repr__(self):
        return f"<SiteVisit {self.day} views={self.views}>"


class VariantPack(db.Model):
    """
    عرض جاهز (باقة) للموديل — طريقة تسعير ثانية جنب أسعار الكمية.

    ⚠️ الفرق بين الطريقتين:

      • **أسعار الكمية** (VariantPriceTier): الزبون يختار العدد، والسعر
        ينزل تلقائيًا. مثال: من 100 لـ199 بـ420 دج للحبة.

      • **العروض الجاهزة** (هاد الموديل): وحدات بيع لها اسم وسعر ثابت.
        مثال: «رولو (فيه 12 متر)» بـ700 دج · «ساشي (فيه 200 حبة)» بـ350 دج.

    الفرق الجوهري: العرض الجاهز **وحدة بيع بذاتها** — الزبون ما يشتريش
    «12 متر»، يشتري «رولو». الاسم والسعر مرتبطين، والعدد يقول كم وحدة
    يشمل هاد العرض.

    الاثنان يقدرو يخدمو مع بعض على نفس الموديل: الزبون يختار واش يشري
    بالحبة (بأسعار الكمية) ولا يشري عرضًا جاهزًا.
    """
    __tablename__ = "variant_packs"

    id = db.Column(db.Integer, primary_key=True)
    variant_id = db.Column(db.Integer, db.ForeignKey("product_variants.id"), nullable=False)

    quantity = db.Column(db.Integer, nullable=False, default=1)   # كم وحدة فالعرض
    label = db.Column(db.String(160), nullable=False)             # «رولو (فيه 12 متر)»
    price = db.Column(db.Integer, nullable=False)                 # السعر الإجمالي للعرض

    sort_order = db.Column(db.Integer, nullable=False, default=0)

    variant = db.relationship(
        "ProductVariant",
        backref=db.backref("packs", cascade="all, delete-orphan",
                           order_by="VariantPack.sort_order"),
    )

    @property
    def price_display(self):
        return f"{self.price:,} دج"

    @property
    def unit_price(self):
        """
        سعر الوحدة داخل العرض — للمقارنة فقط.

        ⚠️ ماشي هو اللي يتحسب فالسلة: السلة تاخذ السعر الإجمالي كيما هو،
        باش ما يصراش فرق قروش من التقريب.
        """
        if not self.quantity:
            return self.price
        return round(self.price / self.quantity)

    @property
    def full_label(self):
        """«2 × رولو (فيه 12 متر)» ولا «رولو (فيه 12 متر)» إذا الكمية 1."""
        if (self.quantity or 1) > 1:
            return f"{self.quantity} × {self.label}"
        return self.label

    def __repr__(self):
        return f"<VariantPack {self.label} {self.price}>"


# ==================== سجل أفعال الأدمن ====================

class AdminAction(db.Model):
    """
    سجل تدقيق للعمليات الحساسة فلوحة التحكم.

    ⚠️ علاش نحتاجوه؟
    كي تلقى منتجًا ناقصًا ولا طلبية حالتها تبدّلت، السؤال الأول هو
    «شكون دارها وامتى؟». بلا سجل، الجواب هو التخمين. وإذا صرا
    اختراق يومًا ما، هذا هو الشي الوحيد اللي يقولك واش صرا فعلاً.

    ⚠️ ماشي سجل لكل شي — الضجيج يقتل السجل. غير العمليات اللي
    تبدّل ولا تمسح ولا تكلّف فلوس (شوف ACTION_* تحت).

    🔴 ما نسجّلوش أبدًا: كلمات السر، الكوكيز، توكنات API، ولا بيانات
    الزبون الكاملة. `details` نص قصير للسياق برك.
    """

    __tablename__ = "admin_actions"

    # العمليات المسجّلة — الحساسة برك
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    DELETE = "delete"
    BULK_DELETE = "bulk_delete"
    ORDER_STATUS = "order_status"
    SHIPMENT_SENT = "shipment_sent"
    COUPON_CHANGE = "coupon_change"
    SETTINGS_CHANGE = "settings_change"
    IMPORT = "import"
    EXPORT = "export"
    INTEGRATION = "integration"
    REVIEW_EDIT = "review_edit"

    id = db.Column(db.Integer, primary_key=True)

    # ⚠️ نخزّنو الاسم ماشي غير المعرّف: لو تمسح الحساب، السجل يبقى
    # مقروءًا. سجل تدقيق يشير لحساب ممسوح ما ينفع بوالو.
    admin_username = db.Column(db.String(80), nullable=True)
    admin_id = db.Column(db.Integer, nullable=True)

    action = db.Column(db.String(40), nullable=False)
    resource = db.Column(db.String(60), nullable=True)      # "product" / "order" ...
    resource_id = db.Column(db.String(60), nullable=True)
    details = db.Column(db.String(300), nullable=True)       # سياق قصير وآمن
    success = db.Column(db.Boolean, nullable=False, default=True)

    ip = db.Column(db.String(60), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    def __repr__(self):
        return f"<AdminAction {self.action} {self.resource}:{self.resource_id}>"

    @property
    def action_label(self):
        return {
            self.LOGIN: "دخول",
            self.LOGIN_FAILED: "محاولة دخول فاشلة",
            self.LOGOUT: "خروج",
            self.DELETE: "حذف",
            self.BULK_DELETE: "حذف جماعي",
            self.ORDER_STATUS: "تبديل حالة طلبية",
            self.SHIPMENT_SENT: "إرسال لشركة التوصيل",
            self.COUPON_CHANGE: "تعديل كوبون",
            self.SETTINGS_CHANGE: "تعديل إعدادات",
            self.IMPORT: "استيراد",
            self.EXPORT: "تصدير",
            self.INTEGRATION: "تكامل خارجي",
            self.REVIEW_EDIT: "تعديل نجوم تقييم",
        }.get(self.action, self.action)
