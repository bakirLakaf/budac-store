import os
from datetime import timedelta


class ConfigError(RuntimeError):
    """إعداد ناقص أو خطير — التطبيق ما يقلعش بيه."""
    pass


# ⚠️ كيفاش نعرفو واش راحنا فالتطوير ولا فالإنتاج؟
# المشروع كامل (app.py، .env.example) يستعمل FLASK_ENV، فنبقاو عليه
# باش ما نخلقوش متغيّر ثاني يتنسى. أي قيمة غير "development" =
# إنتاج — يعني **الافتراضي هو الإنتاج**، وهذا مقصود: نسيان المتغيّر
# لازم يعطي السلوك الأصلب، ماشي الأرخى.
IS_DEVELOPMENT = os.environ.get("FLASK_ENV", "").strip().lower() == "development"

# مفاتيح كانت مكتوبة فالكود ولا معروفة — نرفضوها صراحة حتى لو
# حطّهم أحد فمتغيّر البيئة بالغلط.
_BANNED_SECRETS = {
    "dev-secret-key-change-me",
    "change-this-to-a-random-string",
    "secret",
    "changeme",
    "dev",
    "test",
}

MIN_SECRET_LENGTH = 32


def _resolve_secret_key():
    """
    يجيب SECRET_KEY ويتحقق منو.

    ⚠️ هذا المفتاح يوقّع كوكي الجلسة (وفيها admin_id)، وتوكنات CSRF،
    وبصمة الزوار. مفتاح معروف = أي واحد يزوّر كوكي {"admin_id": 1}
    ويدخل للوحة التحكم بلا كلمة سر.
    فالإنتاج ماكانش قيمة احتياطية — الفشل الصاخب أحسن من الاختراق الصامت.
    """
    key = os.environ.get("SECRET_KEY", "").strip()

    if IS_DEVELOPMENT:
        # فالتطوير المحلي نخلّيو مفتاحًا ثابتًا باش الجلسة ما تنقطعش
        # مع كل إعادة تشغيل. ما يوصلش للإنتاج: الشرط فوق يمنعو.
        return key or "budac-local-development-only-do-not-deploy-this"

    if not key:
        raise ConfigError(
            "🔴 SECRET_KEY غير معمّر.\n"
            "   التطبيق ما يقلعش بلاه فالإنتاج — مفتاح معروف يعني\n"
            "   أي واحد يزوّر جلسة أدمن ويدخل للوحة التحكم.\n"
            "   ولّد واحد وحطّو فمتغيّرات البيئة:\n"
            '     python -c "import secrets; print(secrets.token_hex(32))"'
        )

    if key.lower() in _BANNED_SECRETS:
        raise ConfigError(
            "🔴 SECRET_KEY هو قيمة افتراضية معروفة. ولّد واحدًا عشوائيًا:\n"
            '     python -c "import secrets; print(secrets.token_hex(32))"'
        )

    if len(key) < MIN_SECRET_LENGTH:
        raise ConfigError(
            f"🔴 SECRET_KEY قصير بزاف ({len(key)} حرف). "
            f"لازم {MIN_SECRET_LENGTH} على الأقل."
        )

    return key


class Config:
    """إعدادات التطبيق — كلها تجي من .env، ماكاين حتى قيمة حساسة مكتوبة هنا مباشرة."""

    # ⚠️ ما ننادوش _resolve_secret_key() هنا مباشرة: لو رمى استثناء
    # وقت استيراد الوحدة، الرسالة تضيع وسط traceback غامض. نناديوها
    # من create_app() (شوف Config.validate) باش الخطأ يبان واضحًا.
    SECRET_KEY = None

    IS_DEVELOPMENT = IS_DEVELOPMENT

    @staticmethod
    def validate():
        """
        يتحقق من الإعدادات الحرجة ويرجّع قاموسًا بالقيم المحسوبة.
        يرمي ConfigError برسالة عربية واضحة إذا شي حاجة ناقصة.
        """
        return {"SECRET_KEY": _resolve_secret_key()}

    # ⚠️ Railway (وأغلب منصات الاستضافة) تعطي DATABASE_URL يبدا بـ"postgres://"،
    # لكن SQLAlchemy الحديثة تطلب "postgresql://". بلا هاد التصحيح، التطبيق
    # يطيح عند الإقلاع برسالة غامضة عن "dialect غير مدعوم".
    _db_url = os.environ.get("DATABASE_URL", "sqlite:///budac.db")
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)

    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # يعاود يربط الاتصالات المقطوعة تلقائيًا — مهم على الاستضافة، وين
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
    UPLOAD_ROOT = os.environ.get("UPLOAD_ROOT", "").strip()

    # رقم الواتساب بصيغة دولية بلا + ولا مسافات (مثلاً: 213676672334)
    WHATSAPP_NUMBER = os.environ.get("WHATSAPP_NUMBER", "213676672334")

    # ---------- بيكسلات الإعلانات ----------
    FACEBOOK_PIXEL_ID = os.environ.get("FACEBOOK_PIXEL_ID", "")
    TIKTOK_PIXEL_ID = os.environ.get("TIKTOK_PIXEL_ID", "")

    # ---------- ZR Express API ----------
    ZR_TENANT_ID = os.environ.get("ZR_TENANT_ID", "")
    ZR_API_KEY = os.environ.get("ZR_API_KEY", "")

    # ---------- الأمان ----------

    # حد حجم الطلب كامل. بلاه، Flask يقرا أي ملف مرفوع كامل للذاكرة قبل
    # ما يوصل لفحصنا — واحد يقدر يرفع ملف ضخم ويطيّح السيرفر.
    MAX_CONTENT_LENGTH = 30 * 1024 * 1024

    # كوكي الجلسة (فيه السلة وجلسة الأدمن)
    SESSION_COOKIE_HTTPONLY = True   # JavaScript ما يقدرش يقراه (حماية من XSS)
    SESSION_COOKIE_SAMESITE = "Lax"  # ما يتبعثش مع طلبات من مواقع أخرى

    # ⚠️ الافتراضي = آمن. قبل، كان لازم تحط SESSION_COOKIE_SECURE=true
    # بيدك فالإنتاج — ونسيانو يخلّي كوكي الأدمن يمشي على http.
    # درك: التطوير المحلي هو اللي يستثني، ماشي العكس.
    # (يبقى ممكن تجبرو بالمتغيّر إذا احتجت.)
    _secure_override = os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower()
    if _secure_override in ("true", "1", "yes"):
        SESSION_COOKIE_SECURE = True
    elif _secure_override in ("false", "0", "no"):
        SESSION_COOKIE_SECURE = False
    else:
        SESSION_COOKIE_SECURE = not IS_DEVELOPMENT

    # ⚠️ جلسة الأدمن ما كانتش تنتهي أبدًا (كوكي متصفح). درك 12 ساعة —
    # طويلة كفاية ليوم خدمة كامل بلا ما تقطع عليك، وقصيرة كفاية باش
    # كوكي مسروق ما يبقاش صالحًا للأبد.
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    # مدة صلاحية توكن CSRF (ساعة) — كافية لأطول فورم عندنا
    WTF_CSRF_TIME_LIMIT = 3600
