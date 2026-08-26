"""
ينشئ (أو يبدّل كلمة سر) حساب أدمن للوحة التحكم.

تشغيل: python create_admin.py

⚠️ الأمان:
- كلمة السر تتكتب فالـterminal مخفية (ما تبانش على الشاشة، وما تتسجلش فتاريخ الأوامر)
- تتخزن مهشّرة (hash) فقاعدة البيانات — حتى أنت ما تقدرش تقراها بعد
- إذا نسيتها، عاود شغّل هاد السكريبت بنفس اسم المستخدم باش تبدلها
"""

import getpass
import sys

from app import create_app
from models import db, AdminUser

MIN_PASSWORD_LENGTH = 8


def run():
    app = create_app()
    with app.app_context():
        db.create_all()

        username = input("اسم المستخدم: ").strip()
        if not username:
            print("❌ اسم المستخدم ما يقدرش يكون فارغ")
            sys.exit(1)

        existing = AdminUser.query.filter_by(username=username).first()
        if existing:
            print(f"ℹ️  الحساب '{username}' موجود — راح نبدلو كلمة السر.")

        password = getpass.getpass("كلمة السر: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"❌ كلمة السر قصيرة — لازمها على الأقل {MIN_PASSWORD_LENGTH} حروف")
            sys.exit(1)

        confirm = getpass.getpass("أعد كتابة كلمة السر: ")
        if password != confirm:
            print("❌ كلمتا السر ما تطابقوش")
            sys.exit(1)

        if existing:
            existing.set_password(password)
            action = "تم تبديل كلمة السر"
        else:
            user = AdminUser(username=username)
            user.set_password(password)
            db.session.add(user)
            action = "تم إنشاء الحساب"

        db.session.commit()
        print(f"\n✅ {action} بنجاح للمستخدم '{username}'")
        print("   دخل للوحة التحكم من: http://127.0.0.1:5000/admin/login")


if __name__ == "__main__":
    run()
