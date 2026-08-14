import os
from flask import Flask
from dotenv import load_dotenv

from config import Config
from flask_wtf.csrf import CSRFProtect

from models import db

load_dotenv()  # يقرا ملف .env قبل أي شي


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

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

    # نستثنيو الـAPI ديال سعر التوصيل — هو GET فقط وما يبدّل والو
    csrf.exempt("routes.cart.api_shipping_price")

    # ⚠️ حفظ التقدم: يبعثو JavaScript كـJSON، والتوكن يتبعث فهيدر X-CSRFToken.
    # Flask-WTF يقراه تلقائيًا من الهيدر، فالحماية باقية شغالة — ما نستثنيوهش.

    # تسجيل الروابط (Blueprints) — كل مجموعة روابط فملفها الخاص، منظم وسهل التوسيع
    from routes.shop import shop_bp
    app.register_blueprint(shop_bp)

    from routes.admin import admin_bp
    app.register_blueprint(admin_bp)

    from routes.cart import cart_bp
    app.register_blueprint(cart_bp)

    from routes.landing import landing_bp
    app.register_blueprint(landing_bp)

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
        ⚠️ نلفّوها فـtry: عند أول نشر الجدول مازال ماكانش، وما نبغيوش
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
    @app.context_processor
    def inject_store_info():
        # عدّاد السلة متاح فكل صفحة (للأيقونة فالهيدر) بلا ما نمرروه فكل route
        from services.cart import get_cart_count
        try:
            cart_count = get_cart_count()
        except Exception:
            cart_count = 0  # مثلاً فسياق بلا session (سكريبت CLI)

        from datetime import datetime

        # رسالة واتساب تتعمّر حسب الصفحة — الزبون ما يعاودش يشرح فين كان
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
        # على حاجة ما بغاهاش.
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")

        # ما نبعتوش الرابط الكامل لمواقع خارجية (يحمي خصوصية الزبون)
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

        # يمنع المتصفح من طلب الكاميرا/الميكرو/الموقع باسم موقعك
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )

        # ⚠️ CSP: يحدّد من فين يتحمّل الكود والصور.
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
            message="الحد الأقصى 5 ميغا للصورة.",
        ), 413

    return app


app = create_app()

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_ENV") == "development"
    app.run(debug=debug_mode, port=5000)
