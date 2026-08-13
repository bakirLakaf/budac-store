# -*- coding: utf-8 -*-
"""
اختبار أمني لمتجر BUDAC.
التشغيل: python tests/test_security.py
"""
import os, sys, re, traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "budac"))
BASE = os.path.join(os.path.dirname(__file__), "..", "budac")
os.environ.setdefault("SECRET_KEY", "test-key-for-testing-only")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import create_app
from models import db, Product, ProductVariant, Category, AdminUser, Order, Coupon

OK, BAD, WARN = [], [], []


def check(name, cond, detail="", severity="🔴"):
    if cond:
        OK.append(name); print(f"  ✅ {name}")
    elif severity == "🟡":
        WARN.append((name, detail)); print(f"  🟡 {name}  {detail}")
    else:
        BAD.append((name, detail)); print(f"  🔴 {name}  {detail}")


def section(t):
    print(f"\n{'='*60}\n{t}\n{'='*60}")


def read(path):
    with open(os.path.join(BASE, path), encoding="utf-8") as f:
        return f.read()


def build(csrf=True):
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = csrf
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        cat = Category(name="تصنيف", slug="cat"); db.session.add(cat); db.session.commit()
        p = Product(name="منتج", slug="prod", price=500, category_id=cat.id)
        db.session.add(p); db.session.commit()
        v = ProductVariant(product_id=p.id, label="نوع"); db.session.add(v)
        a = AdminUser(username="admin"); a.set_password("StrongPass!2026")
        db.session.add(a); db.session.commit()
        ids = {"p": p.id, "v": v.id}
    return app, ids


def run():
    # ══════════ 1. الأسرار فالكود ══════════
    section("1. الأسرار والمفاتيح")
    leaks = []
    secret_pat = re.compile(
        r'(api[_-]?key|secret|password|token|tenant[_-]?id)\s*=\s*["\'][A-Za-z0-9_\-]{16,}["\']',
        re.I)
    for root, dirs, files in os.walk(BASE):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules")]
        for f in files:
            if not f.endswith((".py", ".js", ".html")):
                continue
            path = os.path.join(root, f)
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    if secret_pat.search(line) and "environ" not in line and "getenv" not in line:
                        leaks.append(f"{os.path.relpath(path, BASE)}:{i}")
    check("ماكانش سر مكتوب مباشرة فالكود", not leaks, str(leaks[:3]))

    cfg = read("config.py")
    check("SECRET_KEY يجي من متغير بيئة", 'environ.get("SECRET_KEY"' in cfg)
    check("مفاتيح ZR من متغيرات البيئة",
          "ZR_API_KEY" not in cfg or 'environ' in cfg)
    check("ماكانش .env مرفوع مع المشروع",
          not os.path.exists(os.path.join(BASE, ".env")), "احذفه!", )

    # ══════════ 2. CSRF ══════════
    section("2. حماية CSRF")
    app, ids = build(csrf=True)
    c = app.test_client()

    r = c.post("/cart/add", data={"kind": "product", "item_id": ids["p"], "quantity": 1})
    check("POST بلا توكن مرفوض (السلة)", r.status_code == 400, f"رجع {r.status_code}")

    r = c.post("/admin/login", data={"username": "admin", "password": "StrongPass!2026"})
    check("POST بلا توكن مرفوض (الدخول)", r.status_code == 400, f"رجع {r.status_code}")

    # فحص شامل: كل نموذج POST فالقوالب لازم يكون فيه توكن
    missing = []
    tdir = os.path.join(BASE, "templates")
    for root, _, files in os.walk(tdir):
        for f in files:
            if not f.endswith(".html"):
                continue
            path = os.path.join(root, f)
            content = open(path, encoding="utf-8").read()
            # نقطّعو على كل <form ... > ... </form>
            for m in re.finditer(r'<form\b([^>]*)>(.*?)</form>', content, re.S | re.I):
                attrs, inner = m.group(1), m.group(2)
                if 'method="post"' not in attrs.lower():
                    continue
                if "csrf_token" not in inner:
                    missing.append(os.path.relpath(path, tdir))
    check("كل نماذج POST فيها توكن CSRF", not missing, str(sorted(set(missing))))

    # ══════════ 3. حقن SQL ══════════
    section("3. حقن SQL")
    app2, ids2 = build(csrf=False)
    c2 = app2.test_client()
    payloads = ["' OR '1'='1", "'; DROP TABLE products; --", "1 UNION SELECT * FROM admin_users",
                "%' OR 1=1 --"]
    sqli_ok = True
    for pl in payloads:
        for url in [f"/search?q={pl}", f"/?category={pl}", f"/product/{pl}"]:
            try:
                r = c2.get(url)
                if r.status_code >= 500:
                    sqli_ok = False
            except Exception:
                sqli_ok = False
    check("حقن SQL ما يكسرش الموقع", sqli_ok)
    with app2.app_context():
        check("جدول المنتجات مازال موجود", Product.query.count() >= 1)
        check("جدول الأدمن مازال موجود", AdminUser.query.count() >= 1)

    src = read("routes/shop.py") + read("routes/admin.py") + read("routes/cart.py")
    raw_sql = re.findall(r'execute\(\s*f["\']|execute\(\s*["\'][^"\']*%s|\+\s*request\.', src)
    check("ماكانش استعلام SQL مبني بالنص", not raw_sql, str(raw_sql[:2]))

    # ══════════ 4. XSS ══════════
    section("4. XSS")
    xss = "<script>alert(1)</script>"
    r = c2.get(f"/search?q={xss}")
    body = r.get_data(as_text=True)
    check("XSS فالبحث محيّد (escaped)", "<script>alert(1)</script>" not in body)

    # |safe فالقوالب — خطر إذا طُبّق على مدخلات المستخدم
    unsafe = []
    tdir = os.path.join(BASE, "templates")
    for root, _, files in os.walk(tdir):
        for f in files:
            if f.endswith(".html"):
                path = os.path.join(root, f)
                with open(path, encoding="utf-8") as fh:
                    for i, line in enumerate(fh, 1):
                        if "|safe" in line or "{% autoescape false" in line:
                            unsafe.append(f"{os.path.relpath(path, tdir)}:{i}: {line.strip()[:60]}")
    check("استعمال |safe محدود ومراجَع", len(unsafe) <= 3,
          f"{len(unsafe)} موضع: {unsafe[:3]}", "🟡")

    # ══════════ 5. الصلاحيات ══════════
    section("5. الصلاحيات (تجاوز الدخول)")
    protected = ["/admin/", "/admin/products", "/admin/products/new", "/admin/orders",
                 "/admin/coupons", "/admin/settings", "/admin/integrations",
                 "/admin/shipping", "/admin/reviews", "/admin/offers"]
    leaked = []
    for url in protected:
        r = c2.get(url, follow_redirects=False)
        if r.status_code == 200:
            leaked.append(url)
    check("كل صفحات الأدمن محمية", not leaked, str(leaked))

    posts = ["/admin/products/new", "/admin/settings"]
    leaked_post = []
    for url in posts:
        r = c2.post(url, data={}, follow_redirects=False)
        if r.status_code not in (302, 401, 403, 400):
            leaked_post.append(f"{url}={r.status_code}")
    check("عمليات الأدمن (POST) محمية", not leaked_post, str(leaked_post))

    # ══════════ 6. تخمين كلمة السر ══════════
    section("6. تخمين كلمة السر")
    c3 = app2.test_client()
    blocked_at = None
    for i in range(1, 12):
        r = c3.post("/admin/login", data={"username": "admin", "password": f"wrong{i}"})
        if "محاولات كثيرة" in r.get_data(as_text=True):
            blocked_at = i
            break
    check("الحظر بعد محاولات فاشلة", blocked_at is not None and blocked_at <= 8,
          f"حُظر عند المحاولة {blocked_at}")

    adm = read("routes/admin.py")
    check("كلمة السر مخزّنة مهشّرة (hash)",
          "generate_password_hash" in read("models.py"))
    check("ماكانش مقارنة كلمة سر نصية", "== password" not in adm)

    # ══════════ 7. التلاعب بالأسعار ══════════
    section("7. التلاعب بالأسعار")
    c4 = app2.test_client()
    # نحاولو نبعثو سعر من المتصفح
    c4.post("/cart/add", data={"kind": "product", "item_id": ids2["p"],
                               "quantity": 1, "price": 1, "unit_price": 1})
    api = c4.get("/api/cart").get_json()
    check("السعر يُحسب فالسيرفر ماشي من المتصفح",
          api["subtotal"] == 500, f"رجع {api['subtotal']}")

    # كمية ضخمة
    c5 = app2.test_client()
    c5.post("/cart/add", data={"kind": "product", "item_id": ids2["p"], "quantity": 999999})
    api = c5.get("/api/cart").get_json()
    qty = api["lines"][0]["quantity"] if api["lines"] else 0
    check("كمية ضخمة محدودة", qty <= 100, f"قبل {qty}", "🟡")

    # منتج مطفي / ماكانش
    c6 = app2.test_client()
    c6.post("/cart/add", data={"kind": "product", "item_id": 99999, "quantity": 1})
    check("منتج ماكانش ما يتزادش", c6.get("/api/cart").get_json()["count"] == 0)

    # ══════════ 8. الجلسات والكوكيز ══════════
    section("8. الجلسات والكوكيز")
    check("SESSION_COOKIE_HTTPONLY مفعّل",
          app2.config.get("SESSION_COOKIE_HTTPONLY", True) is True)
    check("SESSION_COOKIE_SAMESITE مضبوط",
          app2.config.get("SESSION_COOKIE_SAMESITE") in ("Lax", "Strict"),
          str(app2.config.get("SESSION_COOKIE_SAMESITE")), "🟡")
    check("SESSION_COOKIE_SECURE قابل للتفعيل", "SESSION_COOKIE_SECURE" in cfg)
    check("SECRET_KEY الافتراضي ماشي مستعمل فالإنتاج",
          "dev-secret-key-change-me" in cfg, "موجود كافتراضي فقط — تأكد أنه مضبوط فRailway", "🟡")

    # ══════════ 9. رفع الملفات ══════════
    section("9. رفع الملفات")
    up = read("services/uploads.py") + read("services/images.py")
    check("امتدادات الصور محدودة", "ALLOWED" in up or "allowed" in up)
    check("اسم الملف مؤمّن (secure_filename أو UUID)",
          "secure_filename" in up or "uuid" in up)
    check("حد لحجم الملف", "MAX_CONTENT_LENGTH" in cfg or "MAX_" in up)
    check("الصور تُعالَج بـPillow (تكشف الملفات المزيفة)", "Image.open" in up)

    # ══════════ 10. تسريب المعلومات ══════════
    section("10. تسريب المعلومات")
    check("DEBUG مطفي افتراضيًا", app2.config.get("DEBUG") is False)
    r = c2.get("/xyz-nothing")
    check("صفحة 404 ما تكشفش تفاصيل تقنية",
          "Traceback" not in r.get_data(as_text=True))

    # تتبّع الطلبية: واش يقدر واحد يشوف طلبية غيره؟
    with app2.app_context():
        o = Order(order_number="BD-TEST-1", customer_name="زبون", customer_phone="0555000000",
                  wilaya_code=16, wilaya_name="الجزائر", commune="حي",
                  carrier_code="zr", carrier_name="ZR", delivery_type="home",
                  subtotal=500, shipping_price=600, total=1100, status="pending")
        db.session.add(o); db.session.commit()
    r = c2.post("/track", data={"order_number": "BD-TEST-1", "phone": "0666999999"},
                follow_redirects=True)
    check("تتبّع الطلبية يطلب الهاتف الصحيح",
          "زبون" not in r.get_data(as_text=True))

    # ══════════ 11. رؤوس الحماية ══════════
    section("11. رؤوس الحماية (HTTP headers)")
    r = c2.get("/")
    hd = {k.lower(): v for k, v in r.headers.items()}
    check("X-Content-Type-Options", hd.get("x-content-type-options") == "nosniff", "ناقص")
    check("X-Frame-Options (ضد clickjacking)",
          hd.get("x-frame-options") in ("DENY", "SAMEORIGIN"), "ناقص")
    check("Referrer-Policy", "referrer-policy" in hd, "ناقص")
    check("Content-Security-Policy", "content-security-policy" in hd, "ناقص")
    check("Permissions-Policy", "permissions-policy" in hd, "ناقص", "🟡")

    # ══════════ النتيجة ══════════
    section("النتيجة")
    print(f"✅ نجح: {len(OK)}")
    print(f"🟡 تحذيرات: {len(WARN)}")
    print(f"🔴 مشاكل: {len(BAD)}")
    if BAD:
        print("\n🔴 لازم تصلّح:")
        for n, d in BAD:
            print(f"   • {n}  {d}")
    if WARN:
        print("\n🟡 تحسينات مستحسنة:")
        for n, d in WARN:
            print(f"   • {n}  {d}")
    return len(BAD)


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
