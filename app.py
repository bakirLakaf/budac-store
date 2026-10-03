import os
from flask import Flask, request
from dotenv import load_dotenv

from config import Config
from flask_wtf.csrf import CSRFProtect

from models import db

load_dotenv()  # يقرا ملف .env قبل أي شي


from VERSION import VERSION as APP_VERSION


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # ⚠️ الإعدادات الحرجة تتفحص هنا، قبل أي شي آخر.
    # Config.validate() ترمي ConfigError برسالة عربية واضحة إذا SECRET_KEY
    # ناقص ولا ضعيف ولا قيمة افتراضية معروفة. التطبيق ما يقلعش —
    # وهذا مقصود: مفتاح معروف = أي واحد يزوّر جلسة أدمن.
    app.config.update(Config.validate())

    db.init_app(app)

    # ⚠️ Railway يخدم وراء بروكسي (reverse proxy). بلا هاذي، Flask يظن أن
    # الاتصال http وليس https، فالروابط المطلقة (og:image, canonical, redirects)
    # تتولّد بـhttp — وهذا يخلي المتصفح يشطب على https فشريط العنوان.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # حماية CSRF: تتحقق تلقائيًا من كل POST/PUT/DELETE.
    # بلاها، أي موقع خارجي يقدر يخلق فورم مخفي يبعث طلب لموقعك باسم أدمن داخل
    # (مثلاً حذف كل المنتجات) بمجرد ما يزور الأدمن ذاك الموقع.
    csrf = CSRFProtect()
    csrf.init_app(app)

    # نستثنيو الـAPI تاع سعر التوصيل — هو GET فقط وما يبدّل والو
    csrf.exempt("routes.cart.api_shipping_price")

    # ⚠️ حفظ التقدم: يبعثو JavaScript كـJSON، والتوكن يتبعث فهيدر X-CSRFToken.
    # Flask-WTF يقراه تلقائيًا من الهيدر، فالحماية باقية شغالة — ما نستثنيوهش.

    # ⚠️ تحديد معدّل الطلبات. بلاه، أي واحد يبعث آلاف الطلبيات الوهمية،
    # ولا يعدّ كل طلبيات المتجر بتجريب الأرقام. شوف services/ratelimit.py
    # للتفاصيل وللانتقال لـRedis كي يكبر المشروع.
    from services.ratelimit import init_limiter
    init_limiter(app)

    # تسجيل الروابط (Blueprints) — كل مجموعة روابط فملفها الخاص، منظم وسهل التوسيع
    from routes.shop import shop_bp
    app.register_blueprint(shop_bp)

    from routes.admin import admin_bp
    app.register_blueprint(admin_bp)

    from routes.cart import cart_bp
    app.register_blueprint(cart_bp)

    from routes.landing import landing_bp
    app.register_blueprint(landing_bp)

    # ⚠️ الحدود تتطبّق **بعد** تسجيل كل المسارات — قبل، app.view_functions
    # مازالت فارغة وما يتطبّق حتى حد.
    from services.ratelimit import apply_limits
    apply_limits(app)

    # ═══════════ ضغط الردود (gzip) ═══════════
    # قبل: style.css (116 KB) وsite.js يتبعثو كاملين — ثقل على 4G الضعيف.
    # دروك كل رد نصي (HTML/CSS/JS/JSON) يتضغط وحدو إذا المتصفح يقبل.
    #
    # ⚠️ ما نضغطوش لوحة التحكم (/admin): فيها بيانات حساسة، والضغط يفتح
    # بابًا نظريًا لهجوم BREACH. وهي ماشي موجّهة للزبائن، فما تفيدش
    # سرعتها فالـCore Web Vitals.
    #
    # ⚠️ نسجّلوه هنا (قبل باقي after_request) لأن Flask يشغّلهم بالعكس:
    # الضغط يصرا فالأخير، بعد ما كل الرؤوس تكمل.
    from flask_compress import Compress

    app.config.setdefault("COMPRESS_REGISTER", False)     # نحن نسجّلوه يدويًا
    app.config.setdefault("COMPRESS_ALGORITHM", ["gzip"])  # الأبسط والمدعوم دايمًا
    app.config.setdefault("COMPRESS_MIN_SIZE", 500)        # الردود الصغيرة ما تستاهلش
    _compress = Compress()
    _compress.init_app(app)

    @app.after_request
    def _compress_public(response):
        if request.path.startswith("/admin"):
            return response

        # ⚠️ الملفات الثابتة (CSS/JS) ترجع كـ"stream" وflask-compress يتجاهل
        # الـstream. نحوّلوها لمحتوى عادي (فالذاكرة) قبل الضغط — برك للملفات
        # النصية الصغيرة. الصور ما تتلمسش (مضغوطة أصلاً).
        if (
            response.status_code == 200
            and request.path.startswith("/static/")
            and response.mimetype in app.config["COMPRESS_MIMETYPES"]
            and response.is_streamed
        ):
            response.direct_passthrough = False
            response.make_sequence()

        return _compress.after_request(response)

    # ═══════════ كاش طويل للملفات الثابتة ═══════════
    # الروابط تجي ببصمة (?v=<وقت تعديل الملف>) من `_static_cache_bust`،
    # يعني الرابط يتبدّل كي الملف يتبدّل — وبالتالي نقدرو نخلّيو المتصفح
    # يحتفظ بالملف سنة كاملة بلا خوف من نسخة قديمة.
    # الصور المرفوعة (القرص الدائم) أسماؤها عشوائية فريدة (uuid) بلا
    # بصمة، فنعطيوها 30 يوم برك (مرونة إذا اتعاد معالجة صورة).
    @app.after_request
    def _static_cache_headers(response):
        if request.method not in ("GET", "HEAD") or response.status_code not in (200, 304):
            return response
        path = request.path
        if path.startswith("/static/"):
            if request.args.get("v"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif path.startswith("/static/img/"):
                response.headers["Cache-Control"] = "public, max-age=2592000"
        return response

    # ⚠️ إذا الصور مخزّنة فقرص دائم (UPLOAD_ROOT)، Flask ما يعرفش يخدمها
    # لأنها برّا مجلد static. نزيدو مسار يخدمها.
    upload_root = app.config.get("UPLOAD_ROOT", "")
    if upload_root:
        import os as _os
        from flask import send_from_directory

        @app.route("/static/img/<path:filename>")
        def uploaded_image(filename):
            """
            يخدم الصور من القرص الدائم.

            نجربو القرص أولاً، وإذا الصورة ماكانتش فيه (مثلاً صور جات مع
            الكود كيما اللوغو)، نرجعو لمجلد static العادي.
            """
            volume_path = _os.path.join(upload_root, "img")
            if _os.path.isfile(_os.path.join(volume_path, filename)):
                return send_from_directory(volume_path, filename)

            return send_from_directory(_os.path.join(app.root_path, "static", "img"), filename)

    # فلتر للقوالب: يحوّل اسم صورة لاسم مصغّرتها.
    # بطاقة المنتج تعرض ~250 بكسل — تحميل صورة 1200 بكسل فيها هدر خالص.
    from services.images import thumb_name
    app.jinja_env.filters["thumb"] = thumb_name

    def _store_settings():
        """
        إعدادات المتجر لكل القوالب.
        ⚠️ نلفّوها فـtry: عند أول نشر الجدول مازال ماكانش، وما نحبّوش
        الموقع كامل يطيح بسبب إعداد واحد.
        """
        from models import StoreSetting

        try:
            return {
                "show_coupon_field": StoreSetting.get_bool("show_coupon_field"),
            }
        except Exception:
            return {"show_coupon_field": True}

    # متغيرات تكون متاحة فكل القوالب (templates) بلا ما نبعتوها فكل route
    # ⚠️ تتبّع الزوار — **بعد** الطلب، ماشي قبلو.
    # السبب: باش نعرفو واش الصفحة موجودة فعلاً (رد 200). روبوتات
    # المسح تطلب /wp-login.php وتاخذ 404 — فما تتعدّش.
    # الدالة تبلع أخطاءها بنفسها باش ما تكسرش صفحة للزبون.
    @app.after_request
    def _track_visitors(response):
        from services.analytics import track_response
        return track_response(response)

    # ⚠️ بصمة الملفات الثابتة — تحلّ مشكل تكرر معنا بزاف:
    # ترفع نسخة جديدة، والمتصفح يبقى يعرض CSS/JS القديم من ذاكرتو،
    # فتظن أن الرفع فشل. البصمة = وقت آخر تعديل للملف. كي يتبدّل
    # الملف، الرابط يتبدّل، والمتصفح مجبور يحمّلو من جديد.
    @app.url_defaults
    def _static_cache_bust(endpoint, values):
        if endpoint != "static" or "filename" not in values:
            return
        try:
            path = os.path.join(app.static_folder, values["filename"])
            values["v"] = int(os.stat(path).st_mtime)
        except Exception:
            pass   # الملف ماكانش — نخليو الرابط كيما هو

    @app.context_processor
    def inject_store_info():
        # عدّاد السلة متاح فكل صفحة (للأيقونة فالهيدر) بلا ما نمرروه فكل route
        from services.cart import get_cart_count
        try:
            cart_count = get_cart_count()
        except Exception:
            cart_count = 0  # مثلاً فسياق بلا session (سكريبت CLI)

        from datetime import datetime

        # رسالة واتساب تتعمّر حسب الصفحة — الزبون ما يعاودش يشرح وين كان
        from flask import request as _req

        wa_message = f"سلام، عندي سؤال بخصوص {app.config['STORE_NAME']}"
        try:
            path = _req.path
            if path.startswith("/product/"):
                wa_message = "سلام، عندي سؤال على هذا المنتج: " + _req.url
            elif path.startswith("/offer/"):
                wa_message = "سلام، عندي سؤال على هذا العرض: " + _req.url
            elif path in ("/cart", "/checkout"):
                wa_message = "سلام، عندي سؤال بخصوص طلبيتي"
        except Exception:
            pass  # خارج سياق طلب (مثلاً سكريبت CLI)

        return {
            "whatsapp_number": app.config["WHATSAPP_NUMBER"],
            "fb_pixel_id": app.config["FACEBOOK_PIXEL_ID"],
            "tiktok_pixel_id": app.config["TIKTOK_PIXEL_ID"],
            "whatsapp_message": wa_message,
            "current_year": datetime.now().year,
            "store_name": app.config["STORE_NAME"],
            "store_tagline": app.config["STORE_TAGLINE"],
            "cart_count": cart_count,
            "settings": _store_settings(),
            # ⚠️ رقم الإصدار متاح فكل صفحة. يبان فأسفل لوحة التحكم باش
            # نتأكدو بضربة عين أن الكود الجديد وصل فعلاً لـRailway —
            # قبل، كان لازم نقلّبو فالسجلات وهذا يضيّع وقتًا.
            "app_version": APP_VERSION,
        }

    # ---------- رؤوس الحماية ----------
    # ⚠️ رؤوس HTTP تحمي الزبون من هجمات فالمتصفح. Railway ما يزيدهمش
    # وحدو، فنزيدوهم هنا. ما يأثروش على السرعة.
    @app.after_request
    def add_security_headers(response):
        # يمنع المتصفح من "تخمين" نوع الملف — يسد ثغرة رفع ملف يبان صورة
        response.headers.setdefault("X-Content-Type-Options", "nosniff")

        # يمنع أي موقع آخر من عرض متجرك داخل إطار (clickjacking):
        # واحد يقدر يعرض متجرك بشفافية فوق أزرار مزيفة ويخلي الزبون يضغط
        # على حاجة ما حبهاش.
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")

        # ⚠️ HSTS: يجبر المتصفح يستعمل https حتى لو الزائر كتب http.
        # نزيدوه **فقط** على اتصال آمن فعلاً — لو زدناه على http محلي،
        # المتصفح يحبس localhost على https ويولّي التطوير مستحيلاً
        # (والمسح ما يكونش سهلاً).
        # ماكانش includeSubDomains: الدومينات الفرعية ماشي كلها https مؤكد.
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000"
            )

        # ما نبعتوش الرابط الكامل لمواقع خارجية (يحمي خصوصية الزبون)
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

        # يمنع المتصفح من طلب الكاميرا/الميكرو/الموقع باسم موقعك
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )

        # ⚠️ CSP: يحدّد من وين يتحمّل الكود والصور.
        # فيه 'unsafe-inline' لأن القوالب فيها <script> و<style> مباشرة،
        # وبيكسل فيسبوك/تيك توك يحتاجوها. حتى هكذا يمنع تحميل سكريبت من
        # نطاق غريب — وهذا أهم حماية ضد XSS.
        response.headers.setdefault("Content-Security-Policy", "; ".join([
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' https://connect.facebook.net "
            "https://analytics.tiktok.com https://www.googletagmanager.com",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com data:",
            "img-src 'self' data: https:",
            "connect-src 'self' https://connect.facebook.net https://analytics.tiktok.com",
            "frame-ancestors 'self'",
            "base-uri 'self'",
            "form-action 'self'",
        ]))

        return response

    # ---------- صفحات الخطأ ----------
    # ⚠️ بلاهم، الزائر يشوف صفحة بيضاء بالإنجليزية "Not Found" — يظن الموقع
    # مكسور ويخرج. صفحة عربية بزر رجوع تحافظ عليه.
    from flask import render_template

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template(
            "shop/error.html",
            code=404,
            title="ما لقيناش هاد الصفحة",
            message="يمكن الرابط قديم ولا فيه غلطة. جرّب من الصفحة الرئيسية.",
        ), 404

    @app.errorhandler(500)
    def server_error(e):
        # ⚠️ نرجّعو الجلسة للوراء: إذا الخطأ صرا فوسط عملية قاعدة بيانات،
        # الجلسة تبقى معطّلة وكل طلب بعدها يفشل حتى نديرو rollback.
        db.session.rollback()
        return render_template(
            "shop/error.html",
            code=500,
            title="صرا مشكل عندنا",
            message="راسلنا على واتساب وحنا نكملو معاك الطلبية يدويًا.",
        ), 500

    @app.errorhandler(413)
    def too_large(e):
        return render_template(
            "shop/error.html",
            code=413,
            title="الملف كبير بزاف",
            message="الحد الأقصى 25 ميغا للصورة.",
        ), 413

    return app


app = create_app()

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_ENV") == "development"
    app.run(debug=debug_mode, port=5000)
