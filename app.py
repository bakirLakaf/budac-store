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
        }

    return app


app = create_app()

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_ENV") == "development"
    app.run(debug=debug_mode, port=5000)
