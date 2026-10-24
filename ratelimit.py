"""
تحديد معدّل الطلبات (Rate Limiting) — مركزي فمكان واحد.

⚠️ علاش نحتاجوه؟
بلاه، كل مسار عام مفتوح على المصراعين:
  • واحد يبعث 5000 طلبية وهمية → لوحة التحكم تولّي غير صالحة للاستعمال
  • واحد يجرّب مليون رقم طلبية → يعدّ كل مبيعاتك
  • واحد يبعث آلاف التقييمات → طابور المراجعة يغرق

════════════════════════════════════════════════════════════════
قرار التصميم: علاش Flask-Limiter وماشي كود منّا؟
────────────────────────────────────────────────────────────────
تحديد المعدّل يبان بسيط («عدّاد فقاموس») لكن التفاصيل صعيبة:
النوافذ المنزلقة، تنظيف الذاكرة، IPv6، والتوافق مع البروكسي.
مكتبة مجرّبة أحسن من 80 سطر مكتوبين بالليل.

والأهم: الانتقال لـRedis لاحقًا = تبديل سطر واحد (RATELIMIT_STORAGE_URI)
بلا ما نلمسو حتى مسار.
════════════════════════════════════════════════════════════════

مصدر عنوان الزبون:
⚠️ ما نقراوش X-Forwarded-For بيدنا — العميل يقدر يزوّرو ويولّي عندو
عنوان جديد فكل طلب، فالحد ما يخدم والو. ProxyFix (فـapp.py) خلاص
حلّ المشكل: ياخذ العنوان الصحيح حسب عدد البروكسيات الموثوقة
(x_for=1 = بروكسي واحد، هو تاع Railway) ويحطّو فـremote_addr.
فنثقو فـremote_addr، ماشي فالهيدر الخام.
"""

import os

from flask import request, jsonify, render_template

# ══════════════════════════════════════════════════════════════
# الحدود — مبنية على منطق الأعمال، ماشي رقم عشوائي لكل مسار
# ══════════════════════════════════════════════════════════════
#
# ملاحظة مهمة عن الجزائر: بزاف من الزبائن يدخلو من بيانات الجوال،
# وعدة زبائن يقدرو يشاركو نفس العنوان (CGNAT). فالحدود مضبوطة
# سخيّة عمدًا — نمنعو الإساءة الآلية، ماشي عائلة تطلب 3 طلبيات.

LIMITS = {
    # طلبية كل ~4 دقايق. زبون حقيقي يدير وحدة ولا اثنين.
    # 15/ساعة يخلّي مجالًا حتى لو شاركو العنوان.
    "checkout": "15 per hour; 5 per minute",

    # نفس الشي لصفحات الهبوط
    "landing_submit": "15 per hour; 5 per minute",

    # صفحة الشكر: الزبون يفتحها مرة ولا مرتين. 40/ساعة يمنع
    # التعداد (كان يحتاج مليون طلب) بلا ما يضايق حد.
    "landing_thanks": "40 per hour",

    # تقييم: 5 فالساعة أكثر من كافي لزبون صادق
    "review": "5 per hour",

    # حفظ التقدم يتنادى تلقائيًا من JavaScript وقت الكتابة —
    # فيحتاج حدًا أوسع، لكن ماشي بلا حد.
    "save_progress": "60 per hour; 10 per minute",

    # البحث يشغّل ILIKE '%...%' على كل الكتالوج — مكلف
    "search": "90 per hour; 20 per minute",

    # البحث الفوري (اقتراحات) يتنادى مع كل حرف — لازم يكون سخيًّا
    "search_suggest": "300 per hour",

    # تفقّد الطلبية: يحتاج الرقم + الهاتف معًا، فالتخمين صعيب أصلاً.
    # الحد هنا يمنع الأتمتة برك.
    "order_lookup": "30 per hour",

    # تعديل/إلغاء الطلبية من طرف الزبون
    "order_modify": "20 per hour",

    # ⚠️ صفحة الدخول عندها حماية خاصة فـroutes/admin.py (حظر 15 دقيقة
    # بعد 6 محاولات). هاد الحد طبقة ثانية فوقها، ماشي بديل عنها.
    "admin_login": "30 per hour",
}

# حد افتراضي عام: يمسك الزحف الآلي بلا ما يضايق تصفّحًا عاديًا
DEFAULT_LIMITS = ["600 per hour"]


def _client_identity():
    """
    من هو صاحب الطلب؟

    ⚠️ نعتمدو على request.remote_addr اللي صلّحو ProxyFix — ماشي على
    X-Forwarded-For الخام. الفرق مهم: الهيدر الخام يتحكم فيه العميل،
    فيقدر يبدّلو فكل طلب ويتخطّى الحد كامل.
    """
    return request.remote_addr or "unknown"


def _rate_limit_response(error):
    """
    رد مفهوم كي يوصل الزبون للحد.

    ⚠️ نرجّعو JSON لمسارات الـAPI وHTML للصفحات — بلا هذا، JavaScript
    يحاول يقرا HTML كـJSON ويطيح برسالة غامضة.
    """
    message = "طلبات كثيرة بزاف من عندك. استنى شوية وعاود جرّب."

    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"ok": False, "error": message}), 429

    return render_template(
        "shop/error.html",
        code=429,
        title="روّق شوية",
        message=message + " إذا كنت تحتاج مساعدة، راسلنا فالواتساب.",
    ), 429


def init_limiter(app):
    """
    يركّب المحدِّد على التطبيق.

    ⚠️ إذا المكتبة ماكانتش مثبّتة، ما نطيّحوش الموقع — نطبعو تحذيرًا
    ونكملو. الأمان ينقص، لكن متجر واقف أسوأ من متجر بلا حد معدّل،
    والمسارات تبقى تخدم عادي (شوف limit() تحت).
    """
    try:
        from flask_limiter import Limiter
    except ImportError:
        app.logger.warning(
            "⚠️ Flask-Limiter ماشي مثبّت — تحديد معدّل الطلبات معطّل. "
            "شغّل: pip install -r requirements.txt"
        )
        app.extensions["budac_limiter"] = None
        return None

    # ⚠️ التخزين: الذاكرة كافية لعامل واحد (WEB_WORKERS=1 فالـProcfile).
    # كي تزيد العمال، حط RATELIMIT_STORAGE_URI=redis://... فمتغيّرات
    # البيئة وخلاص — ما يتبدّل حتى سطر من الكود.
    storage_uri = os.environ.get("RATELIMIT_STORAGE_URI", "memory://").strip()

    limiter = Limiter(
        key_func=_client_identity,
        app=app,
        default_limits=DEFAULT_LIMITS,
        storage_uri=storage_uri,
        strategy="fixed-window",
        headers_enabled=True,
        # ⚠️ لو Redis طاح، ما نسدّوش المتجر — نخلّيو الطلبات تعدّي.
        # فقدان الحد مؤقتًا أهون من موقع واقف.
        swallow_errors=True,
    )

    app.register_error_handler(429, _rate_limit_response)
    app.extensions["budac_limiter"] = limiter

    return limiter


def limit(name):
    """
    مزخرف (decorator) يطبّق حدًا بالاسم من LIMITS.

    الاستعمال:
        @cart_bp.route("/checkout", methods=["GET", "POST"])
        @ratelimit.limit("checkout")
        def checkout(): ...

    ⚠️ علاش طبقة وسيطة بدل ما نستعملو @limiter.limit مباشرة؟
      1. الحدود كلها مجمّعة فوق فقاموس واحد — تشوفهم بضربة عين
         وتعدّلهم بلا ما تدور فالمسارات
      2. المحدِّد يتخلق داخل create_app()، فما نقدروش نستوردو
         وقت تعريف المسارات (المشكلة الكلاسيكية تاع الاستيراد الدائري).
         التأجيل هنا يحلّها.
    """
    rule = LIMITS.get(name)
    if rule is None:
        raise ValueError(f"حد غير معرّف: {name} — زيدو فـLIMITS")

    def decorator(view):
        # ⚠️ نأجّلو البحث على المحدِّد لوقت الطلب: وقت تعريف المسار
        # التطبيق مازال ما تخلقش.
        from functools import wraps

        @wraps(view)
        def wrapped(*args, **kwargs):
            return view(*args, **kwargs)

        # نعلّمو الدالة، وinit_limiter يطبّق الحدود بعد تسجيل كل المسارات
        wrapped._budac_rate_limit = rule
        return wrapped

    return decorator


def apply_limits(app):
    """
    يطبّق الحدود المعلّمة على المسارات.

    ⚠️ يتنادى **بعد** تسجيل كل الـblueprints — ساعتها برك تكون
    app.view_functions معمّرة.
    """
    limiter = app.extensions.get("budac_limiter")
    if limiter is None:
        return 0

    applied = 0
    for endpoint, view in list(app.view_functions.items()):
        rule = getattr(view, "_budac_rate_limit", None)
        if not rule:
            continue

        # ⚠️ لازم نبدّلو الدالة المسجّلة نفسها. لو ناديناها برك
        # (limiter.limit(rule)(view)) الغلاف الجديد يضيع وFlask يبقى
        # ينادي الأصلية — والحد ما يطبّق أبدًا.
        app.view_functions[endpoint] = limiter.limit(rule)(view)
        applied += 1

    return applied
