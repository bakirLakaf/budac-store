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


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        # ⚠️ ما نوقفوش الإقلاع: أحسن يخدم الموقع ونشوفو الخطأ فالسجلات،
        # من أن يبقى معطّل كليًا بسبب خطوة تجهيز وحدة
        print(f"❌ خطأ فالتجهيز: {e}", file=sys.stderr)
        print("   الموقع غادي يبدا رغم هذا — راجع السجلات.", file=sys.stderr)
