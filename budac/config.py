import os


class Config:
    """إعدادات التطبيق — كلها تجي من .env، ماكاين حتى قيمة حساسة مكتوبة هنا مباشرة."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    # ⚠️ Railway (وأغلب منصات الاستضافة) تعطي DATABASE_URL يبدا بـ"postgres://"،
    # لكن SQLAlchemy الحديثة تطلب "postgresql://". بلا هاد التصحيح، التطبيق
    # يطيح عند الإقلاع برسالة غامضة عن "dialect غير مدعوم".
    _db_url = os.environ.get("DATABASE_URL", "sqlite:///budac.db")
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)

    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # يعاود يربط الاتصالات المقطوعة تلقائيًا — مهم على الاستضافة، فين
    # قاعدة البيانات تقطع الاتصالات الخاملة بعد مدة
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
    }

    # اسم المتجر — يستعمل فالـ<title> وفكل مكان يحتاج اسم المتجر
    STORE_NAME = os.environ.get("STORE_NAME", "BUDAC")
    STORE_TAGLINE = os.environ.get("STORE_TAGLINE", "ديكور و إكسسوارات")

    # ---------- مجلد الصور المرفوعة ----------
    # ⚠️ مهم جدًا على الاستضافة: نظام الملفات "مؤقت" — أي حاجة تكتبها
    # تنمسح عند إعادة النشر. فالصور اللي يرفعها الأدمن تضيع.
    #
    # الحل: Railway Volume (قرص دائم) يتركّب على مسار، ونخزّنو فيه.
    # حط UPLOAD_ROOT=/data فمتغيرات البيئة، وركّب Volume على /data.
    #
    # محليًا (بلا المتغير)، يستعمل static/img كيما قبل.
    UPLOAD_ROOT = os.environ.get("UPLOAD_ROOT", "").strip()

    # رقم الواتساب بصيغة دولية بلا + ولا مسافات (مثلاً: 213676672334)
    WHATSAPP_NUMBER = os.environ.get("WHATSAPP_NUMBER", "213676672334")

    # ---------- بيكسلات الإعلانات ----------
    # خليهم فارغين إذا ما تستعملش الإعلانات — ما يتحمّلش حتى سكريبت.
    FACEBOOK_PIXEL_ID = os.environ.get("FACEBOOK_PIXEL_ID", "")
    TIKTOK_PIXEL_ID = os.environ.get("TIKTOK_PIXEL_ID", "")

    # ---------- ZR Express API ----------
    # اختياري. بلاه، تبعث الطلبيات يدويًا كيما قبل.
    ZR_TENANT_ID = os.environ.get("ZR_TENANT_ID", "")
    ZR_API_KEY = os.environ.get("ZR_API_KEY", "")

    # ---------- الأمان ----------

    # حد حجم الطلب كامل (5 ميغا + هامش للفورم).
    # ⚠️ مهم: بلا هذا، Flask يقرا أي ملف مرفوع كامل للذاكرة قبل ما يوصل لفحصنا —
    # واحد يقدر يرفع ملف 2 جيغا ويطيّح السيرفر. هاد الحد يرفضو من البداية.
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024

    # كوكي الجلسة (فيه السلة وجلسة الأدمن)
    SESSION_COOKIE_HTTPONLY = True   # JavaScript ما يقدرش يقراه (حماية من XSS)
    SESSION_COOKIE_SAMESITE = "Lax"  # ما يتبعثش مع طلبات من مواقع أخرى

    # ⚠️ خليها False فالتطوير المحلي (http)، وحطها True فالإنتاج (https).
    # لو حطيتها True محليًا، الجلسة ما تخدمش أصلاً وتوقف عندك السلة والدخول.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"

    # مدة صلاحية توكن CSRF (ساعة) — كافية لأطول فورم عندنا
    WTF_CSRF_TIME_LIMIT = 3600
