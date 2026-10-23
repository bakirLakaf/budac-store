"""تشغيل تجريبي مستقل للأداة (بلا المتجر): دخول بكلمة سر + المنتجات من ملف Excel.
في الإنتاج ما تحتاجوش — تركّب الأداة داخل لوحة تحكم Budac (شوف README)."""
import os
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, redirect, request, session

load_dotenv()

from social_publisher import init_social_publisher
from social_publisher.adapter import ExcelDemoAdapter

app = Flask(__name__)
app.secret_key = os.getenv("SP_SECRET", "")


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect("/login")
        return fn(*a, **kw)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    pwd = os.getenv("ADMIN_PASSWORD", "")
    if request.method == "POST" and pwd and request.form.get("password") == pwd:
        session["admin"] = True
        return redirect("/admin/social/")
    return ('<meta charset="utf-8"><form method="post" style="font-family:sans-serif;margin:60px auto;max-width:300px">'
            '<h3>دخول</h3><input type="password" name="password" placeholder="كلمة السر" style="width:100%;padding:8px">'
            '<button style="margin-top:10px;padding:8px 16px">دخول</button></form>')


xlsx = os.getenv("PRODUCTS_XLSX", "budac-products.xlsx")
adapter = ExcelDemoAdapter(xlsx) if os.path.exists(xlsx) else None
if adapter is None:
    print(f"تنبيه: ملف المنتجات {xlsx} ما لقيتوش — البحث على المنتجات راح يكون فارغ")

    class _Empty:
        def search_products(self, q, limit=12): return []
        def get_product(self, pid): return None
    adapter = _Empty()

init_social_publisher(app, admin_required=admin_required, adapter=adapter)


@app.route("/")
def home():
    return redirect("/admin/social/")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
