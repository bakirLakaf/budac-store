from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


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
    """

    __tablename__ = "product_images"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    image_filename = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)  # ترتيب الظهور فالمعرض

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

    product = db.relationship("Product", back_populates="variants")

    @property
    def effective_price(self):
        return self.price if self.price is not None else self.product.price

    @property
    def price_display(self):
        return f"{self.effective_price:,} دج"

    @property
    def is_in_stock(self):
        """فالنظام المفتوح، كل التنويعات متوفرة دائمًا."""
        if not Product.is_stock_tracked:
            return True
        return self.stock_quantity > 0

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

    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True, index=True)
    color_variant = db.Column(db.String(120), nullable=True)  # قديم — يبقى للتوافق، الأفضل نستعملو variants

    # ---------- حاسبة الكمية ----------
    # بعض المنتجات تتباع بالمتر (أشرطة، ورق لاصق)، والزبون ما يعرفش شحال يحتاج.
    # الحاسبة تسولو على أبعاد الحاجة اللي بغى يزيّن، وتحسبلو الكمية.
    #   None       → بلا حاسبة (منتج عادي)
    #   "perimeter"→ حاسبة محيط: للأشرطة اللي تدور حول مرآة/إطار
    #   "area"     → حاسبة مساحة: للورق اللاصق اللي يغطي سطح
    calculator_type = db.Column(db.String(20), nullable=True)

    # للأشرطة: طول اللفة الوحدة بالمتر (مثلاً 5 أمتار)
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

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    category = db.relationship("Category", back_populates="products")
    images = db.relationship(
        "ProductImage",
        back_populates="product",
        order_by="ProductImage.sort_order",
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
    # المخزون: النظام دابا "مفتوح" — ما نتبعوش الكميات.
    #
    # القرار: متجرك يشتغل على الطلب، والكميات تتبدّل بسرعة فالمحل الحقيقي.
    # تتبّع مخزون غير دقيق أسوأ من بلا تتبّع: يمنع بيعة موجودة فعلاً.
    #
    # الحقول stock_quantity باقية فقاعدة البيانات (ما مسحناهمش) باش:
    #   1. الطلبيات القديمة تبقى صحيحة
    #   2. إذا بغيت ترجّع التتبّع بعدين، ترجعو بتبديل is_stock_tracked لـTrue
    # ═══════════════════════════════════════════════════════════════

    # بدّلها لـTrue إذا بغيت ترجّع تتبّع المخزون
    is_stock_tracked = False

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
    def price_display(self):
        """سعر منسق للعرض: 1200 → '1,200 دج'"""
        return f"{self.price:,} دج"

    @property
    def compare_at_price_display(self):
        if self.compare_at_price:
            return f"{self.compare_at_price:,} دج"
        return None

    @property
    def has_discount(self):
        return self.compare_at_price is not None and self.compare_at_price > self.price

    @property
    def discount_percent(self):
        if not self.has_discount:
            return 0
        return round(100 - (self.price / self.compare_at_price * 100))

    @property
    def gallery_images(self):
        """
        قائمة كل صور المنتج للمعرض. إذا عندو صور فـProductImage، نستعملها.
        وإلا نرجعو للصورة الرئيسية القديمة (image_filename) باش التوافق يبقى خدام
        حتى للمنتجات اللي زدناها قبل ميزة الصور المتعددة.
        """
        if self.images:
            return [img.image_filename for img in self.images]
        if self.image_filename:
            return [self.image_filename]
        return []

    @property
    def default_variant(self):
        if not self.variants:
            return None
        for v in self.variants:
            if v.is_default:
                return v
        return self.variants[0]


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
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

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
        واش العرض شغال دابا: مفعّل + داخل المدة الزمنية (إذا محددة).
        التواريخ اختيارية — عرض بلا تواريخ يعتبر شغال دائمًا ما دام مفعّل.
        """
        if not self.is_active:
            return False
        now = datetime.utcnow()
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
        """أقصى عدد باقات نقدرو نبيعو دابا — مفيد باش نمنعو طلب أكثر من المتاح."""
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

class AdminUser(db.Model):
    """
    حساب أدمن للوحة التحكم.
    ⚠️ كلمة السر تتخزن مهشّرة (hash) فقط — أبدًا نص صريح.
    """

    __tablename__ = "admin_users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
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
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

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

        نرجعو السبب باش الزبون يفهم واش خاصو يدير (مثلاً: زيد منتجات باش توصل للحد الأدنى)
        بدل رسالة عامة "كوبون غير صالح".
        """
        if not self.is_active:
            return False, "هذا الكوبون غير مفعّل"

        now = datetime.utcnow()
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
        """
        if self.discount_type == self.TYPE_FREE_SHIPPING:
            return 0, shipping_price

        if self.discount_type == self.TYPE_FIXED:
            return min(self.value, subtotal), 0  # ما يتعداش قيمة الطلبية

        # نسبة مئوية
        discount = int(subtotal * self.value / 100)
        if self.max_discount is not None:
            discount = min(discount, self.max_discount)
        return min(discount, subtotal), 0


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
    STATUS_SHIPPED = "shipped"        # اتسلّمت لشركة التوصيل
    STATUS_DELIVERED = "delivered"    # وصلت للزبون
    STATUS_CANCELLED = "cancelled"    # ملغاة

    STATUS_LABELS = {
        STATUS_PENDING: "جديدة",
        STATUS_CONFIRMED: "مؤكدة",
        STATUS_SHIPPED: "فالطريق",
        STATUS_DELIVERED: "تم التسليم",
        STATUS_CANCELLED: "ملغاة",
    }

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), nullable=False, unique=True)  # رقم يعطى للزبون

    customer_name = db.Column(db.String(150), nullable=False)
    customer_phone = db.Column(db.String(30), nullable=False)

    wilaya_code = db.Column(db.String(4), nullable=False)
    wilaya_name = db.Column(db.String(120), nullable=False)
    commune = db.Column(db.String(150), nullable=True)
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
    carrier_state = db.Column(db.String(120), nullable=True)     # آخر حالة معروفة

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

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

    @property
    def is_open(self):
        """واش الطلبية مازالت نشطة (ماشي مسلّمة ولا ملغاة)."""
        return self.status not in (self.STATUS_DELIVERED, self.STATUS_CANCELLED)

    @property
    def is_sent_to_carrier(self):
        """واش تبعثات لشركة التوصيل."""
        return bool(self.tracking_number)


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

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

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
    تقييم زبون لمنتج.

    ⚠️ كل تقييم يمر على موافقة الأدمن قبل ما يبان (is_approved).
    السبب: بلا هذا، أي واحد يقدر يكتب سبام أو إساءة ويبان مباشرة فمتجرك.
    """

    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)

    author_name = db.Column(db.String(120), nullable=False)
    rating = db.Column(db.Integer, nullable=False)   # من 1 لـ5
    comment = db.Column(db.Text, nullable=True)

    is_approved = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    product = db.relationship("Product", backref=db.backref("reviews", cascade="all, delete-orphan"))

    @property
    def stars(self):
        """نجوم للعرض: ★★★★☆"""
        return "★" * self.rating + "☆" * (5 - self.rating)

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
