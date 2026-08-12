"""
يتشغّل تلقائيًا عند كل نشر، قبل ما يبدا الموقع.

ليش نحتاجوه؟
على الاستضافة ما تقدرش تشغّل أوامر بيدك كيما فجهازك. فهاد السكريبت يدير
كل التجهيز الضروري لوحدو:

  1. ينشئ جداول قاعدة البيانات إذا ماكانوش
  2. يعمّر أسعار التوصيل (58 ولاية × 3 شركات) إذا كانت فارغة
  3. ينشئ حساب أدمن من متغيرات البيئة، إذا ماكانش حساب

⚠️ آمن للتشغيل المتكرر: كل خطوة تتحقق أولاً واش خاصها تتنفذ.
ما يمسح ولا يكرّر والو.
"""

import os
import sys


def run():
    from app import create_app
    from models import db, AdminUser, ShippingRate

    app = create_app()

    with app.app_context():
        # ---------- 1) الجداول ----------
        print("🔧 نتحققو من جداول قاعدة البيانات...")
        db.create_all()
        print("   ✅ الجداول جاهزة")

        # ---------- 1.5) الأعمدة الجديدة ----------
        # ⚠️ db.create_all() ينشئ الجداول الناقصة فقط — ما يزيدش أعمدة لجدول
        # موجود من قبل. فنزيدوهم بيدنا هنا. كل خطوة تتحقق أولاً، فالتشغيل
        # المتكرر آمن تمامًا.
        _add_missing_columns(db)

        # ---------- 2) أسعار التوصيل ----------
        if ShippingRate.query.count() == 0:
            print("🚚 نعمّرو أسعار التوصيل...")
            try:
                import seed_shipping
                seed_shipping.run()
            except Exception as e:
                # ما نوقفوش النشر بسبب هذا — الموقع يخدم، والأدمن يعمّرهم يدويًا
                print(f"   ⚠️ ما قدرناش نعمّرو الأسعار: {e}")
        else:
            print(f"🚚 أسعار التوصيل موجودة ({ShippingRate.query.count()} سطر)")

        # ---------- 3) حساب الأدمن ----------
        if AdminUser.query.count() == 0:
            username = os.environ.get("ADMIN_USERNAME", "").strip()
            password = os.environ.get("ADMIN_PASSWORD", "").strip()

            if username and len(password) >= 8:
                user = AdminUser(username=username)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                print(f"👤 تم إنشاء حساب الأدمن: {username}")
            else:
                print(
                    "⚠️  ماكانش حساب أدمن، وما لقيناش ADMIN_USERNAME/ADMIN_PASSWORD.\n"
                    "   زيدهم فمتغيرات البيئة (كلمة السر 8 حروف على الأقل)\n"
                    "   وأعد النشر باش يتخلق الحساب."
                )
        else:
            print("👤 حساب الأدمن موجود")

        print("✅ التجهيز خلص — نشغّلو الموقع\n")


def _add_missing_columns(db):
    """
    يزيد الأعمدة الجديدة للجداول الموجودة (هجرة بسيطة بلا Alembic).

    كل عمود يتحقق أولاً واش موجود. إذا موجود → يتخطاه. إذا لا → يزيدو.
    يخدم على PostgreSQL و SQLite بنفس الطريقة.
    """
    from sqlalchemy import inspect, text

    # (اسم الجدول, اسم العمود, تعريف SQL)
    wanted = [
        ("products", "is_featured", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("offers", "spotlight_position", "VARCHAR(10) NOT NULL DEFAULT 'none'"),
        ("offers", "spotlight_text", "TEXT"),
    ]

    inspector = inspect(db.engine)
    added = 0

    for table, column, ddl in wanted:
        if table not in inspector.get_table_names():
            continue

        existing = {c["name"] for c in inspector.get_columns(table)}
        if column in existing:
            continue

        try:
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            db.session.commit()
            print(f"   ➕ زدنا العمود {table}.{column}")
            added += 1
        except Exception as e:
            db.session.rollback()
            print(f"   ⚠️ ما قدرناش نزيدو {table}.{column}: {e}")

    if added == 0:
        print("   ✅ كل الأعمدة موجودة")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        # ⚠️ ما نوقفوش الإقلاع: أحسن يخدم الموقع ونشوفو الخطأ فالسجلات،
        # من أن يبقى معطّل كليًا بسبب خطوة تجهيز وحدة
        print(f"❌ خطأ فالتجهيز: {e}", file=sys.stderr)
        print("   الموقع غادي يبدا رغم هذا — راجع السجلات.", file=sys.stderr)
