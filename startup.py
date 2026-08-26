"""
يتشغّل تلقائيًا عند كل نشر، قبل ما يبدا الموقع.

ليش نحتاجوه؟
على الاستضافة ما تقدرش تشغّل أوامر بيدك كيما فجهازك. فهاد السكريبت يدير
كل التجهيز الضروري لوحدو:

  1. ينشئ جداول قاعدة البيانات إذا ماكانوش
  2. يعمّر أسعار التوصيل (58 ولاية × 3 شركات) إذا كانت فارغة
  3. ينشئ حساب أدمن من متغيرات البيئة، إذا ماكانش حساب

⚠️ آمن للتشغيل المتكرر: كل خطوة تتحقق أولاً واش لازمها تتنفذ.
ما يمسح ولا يكرّر والو.
"""

import os
import sys


def run():
    from app import create_app
    from models import db, AdminUser, ShippingRate

    app = create_app()

    with app.app_context():
        # ⚠️ نطبعو النسخة أول حاجة — باش نعرفو من سجلات Railway
        # واش الكود الجديد وصل فعلاً، بلا ما نفتحو الموقع.
        try:
            from VERSION import VERSION, BUILD_NOTE
            print("=" * 46)
            print(f"  📦 نسخة الكود: {VERSION}")
            print(f"     {BUILD_NOTE}")
            print("=" * 46)
        except Exception:
            print("  ⚠️ ماكانش ملف VERSION.py — نسخة قديمة!")

        # ---------- 1) الجداول ----------
        print("🔧 نتحققو من جداول قاعدة البيانات...")
        db.create_all()
        print("   ✅ الجداول جاهزة")

        # ---------- 1.5) الأعمدة الجديدة ----------
        # ⚠️ db.create_all() ينشئ الجداول الناقصة فقط — ما يزيدش أعمدة لجدول
        # موجود من قبل. فنزيدوهم بيدنا هنا. كل خطوة تتحقق أولاً، فالتشغيل
        # المتكرر آمن تمامًا.
        _repair_stale_communes(db)
        _add_missing_columns(db)
        _fix_null_flags(db)
        _relax_review_product(db)

        # ---------- 1.7) البلديات ----------
        _seed_communes()

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

        # ⚠️ تنظيف سجلات الزوار: القديمة + اللي جات من روبوتات المسح.
        # الفلتر القديم كان يعتمد على User-Agent وحدو، والسكانرات تزوّرو،
        # فدخلو زيارات لـ/wp-admin وشبهها وخرّبو الأرقام.
        try:
            from services.analytics import prune_old, purge_scan_noise

            removed = prune_old()
            if removed:
                print(f"🧹 مسحنا {removed} سطر زيارات قديمة (أكثر من 180 يوم)")

            noise = purge_scan_noise()
            if noise:
                print(f"🤖 مسحنا {noise} زيارة من روبوتات المسح (wp-admin وشبهها)")
        except Exception as e:
            print(f"   ⚠️ تنظيف الزوار ما خدمش: {e}")

        print("✅ التجهيز خلص — نشغّلو الموقع\n")


def _relax_review_product(db):
    """
    يخلّي `reviews.product_id` يقبل NULL — باش تقييمات **المتجر** تخدم.

    ⚠️ علاش دالة خاصة؟ `_add_missing_columns` تزيد أعمدة ناقصة برك،
    ما تبدّلش قيود عمود موجود. والعمود كان `NOT NULL` من أول نسخة.

    SQLite ما عندوش `ALTER COLUMN` — لازم إعادة بناء الجدول.
    PostgreSQL عندو `ALTER ... DROP NOT NULL` مباشرة.

    التشغيل المتكرر آمن: نتحققو أول واش القيد مازال موجودًا.
    """
    from sqlalchemy import text, inspect

    inspector = inspect(db.engine)
    if "reviews" not in set(inspector.get_table_names()):
        return

    columns = {c["name"]: c for c in inspector.get_columns("reviews")}
    col = columns.get("product_id")
    if col is None or col.get("nullable", True):
        return   # مليح أصلاً

    dialect = db.engine.dialect.name

    try:
        if dialect == "sqlite":
            # إعادة بناء: جدول جديد → نسخ → تبديل
            db.session.execute(text("""
                CREATE TABLE reviews_new (
                    id INTEGER PRIMARY KEY,
                    product_id INTEGER REFERENCES products(id),
                    author_name VARCHAR(120) NOT NULL,
                    rating INTEGER NOT NULL,
                    comment TEXT,
                    is_approved BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME
                )
            """))
            db.session.execute(text("""
                INSERT INTO reviews_new
                    (id, product_id, author_name, rating, comment,
                     is_approved, created_at)
                SELECT id, product_id, author_name, rating, comment,
                       is_approved, created_at
                FROM reviews
            """))
            db.session.execute(text("DROP TABLE reviews"))
            db.session.execute(text("ALTER TABLE reviews_new RENAME TO reviews"))
        else:
            db.session.execute(
                text("ALTER TABLE reviews ALTER COLUMN product_id DROP NOT NULL")
            )

        db.session.commit()
        print("   🔧 reviews.product_id ولّى يقبل NULL (تقييمات المتجر)")
    except Exception as e:
        db.session.rollback()
        print(f"   ⚠️ ما قدرناش نرخّيو reviews.product_id: {e}")


def _fix_null_flags(db):
    """
    يصلّح الأعمدة المنطقية اللي بقات NULL.

    ⚠️ علاش لازم؟ إذا عمود انزاد قبل ما نحطّو `DEFAULT FALSE`، الصفوف
    القديمة تولّي NULL. وفـSQL، `NULL == False` ترجّع NULL ماشي True —
    فالصفوف يختفيو من أي فلترة. صرا مع `product_images.is_extra`:
    كل صور المنتجات القديمة كانت راح تختفي من المعرض.

    التشغيل المتكرر آمن — يمسّ غير الصفوف اللي فيها NULL.
    """
    from sqlalchemy import text, inspect

    targets = [
        ("product_images", "is_extra", "0"),
        ("product_variants", "is_available", "1"),
    ]

    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    for table, column, default in targets:
        if table not in tables:
            continue
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            continue

        try:
            result = db.session.execute(
                text(f"UPDATE {table} SET {column} = {default} "
                     f"WHERE {column} IS NULL")
            )
            db.session.commit()
            if result.rowcount:
                print(f"   🔧 صلّحنا {result.rowcount} صف فـ{table}.{column}")
        except Exception as e:
            db.session.rollback()
            print(f"   ⚠️ ما قدرناش نصلّحو {table}.{column}: {e}")


def _seed_communes():
    """
    يعمّر البلديات عند أول إقلاع.
    ⚠️ نتخطاه إذا الجدول معمّر — ما نعاودوش نقراو 1541 سطر فكل نشر.
    """
    from models import Commune

    count = Commune.query.count()
    if count >= 1500:
        print(f"🏘️  البلديات موجودة ({count} بلدية)")
        return

    try:
        import seed_communes
        seed_communes.run()
    except Exception as e:
        print(f"⚠️ ما قدرناش نعمّرو البلديات: {e}")


def _repair_stale_communes(db):
    """
    يصلّح جدول communes إذا كان من نسخة قديمة.

    ⚠️ ليش هاد الدالة؟
    فنسخة قديمة، جدول communes كان يدمج بيانات البلدية مع ربط شركة التوصيل
    (فيه carrier_id, external_id, has_home_delivery... وكلهم NOT NULL).
    درك الربط ولا فجدول منفصل (carrier_communes)، والبلدية فيها 5 أعمدة فقط.

    db.create_all() ما يلمسش جدول موجود، فالجدول القديم يبقى — وأي محاولة
    إدخال بلدية تفشل بـNotNullViolation على carrier_id.

    الحل: نكشفو الأعمدة الغريبة، وإذا لقيناهم نمسحو الجدول ونعاودو نبنيوه.

    ✅ آمن: البلديات بيانات مرجعية، تتعمّر من ملف JSON فكل مرة.
       ما فيها حتى معلومة تاع زبون.
    """
    from sqlalchemy import inspect, text
    from models import Commune

    inspector = inspect(db.engine)
    if "communes" not in inspector.get_table_names():
        return  # الجدول ماكانش — create_all راح يبنيه صحيح

    db_columns = {c["name"] for c in inspector.get_columns("communes")}
    model_columns = {c.name for c in Commune.__table__.columns}

    # أعمدة موجودة فقاعدة البيانات وماشي فالموديل = بقايا نسخة قديمة
    stale = db_columns - model_columns
    if not stale:
        return

    print(f"   ⚠️ جدول communes قديم — أعمدة زائدة: {', '.join(sorted(stale))}")
    print("      نعاودو نبنيوه من الصفر (البلديات تتعمّر من الملف)")

    # ⚠️ CASCADE يخدم فقط على PostgreSQL — SQLite (التطوير المحلي) يرفضو.
    # نمسحو carrier_communes أولاً باش ما يبقاش مفتاح أجنبي معلّق.
    cascade = " CASCADE" if db.engine.dialect.name == "postgresql" else ""

    try:
        db.session.execute(text(f"DROP TABLE IF EXISTS carrier_communes{cascade}"))
        db.session.execute(text(f"DROP TABLE IF EXISTS communes{cascade}"))
        db.session.commit()

        db.create_all()
        print("   ✅ جدول communes تعاود بناؤه")
    except Exception as e:
        db.session.rollback()
        print(f"   🔴 ما قدرناش نصلّحو جدول communes: {e}")


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
        ("shipping_rates", "carrier_territory_id", "VARCHAR(60)"),
        ("orders", "commune_external_id", "VARCHAR(80)"),
        ("orders", "pickup_point_id", "VARCHAR(80)"),
        ("orders", "pickup_point_name", "VARCHAR(200)"),
        ("communes", "daira", "VARCHAR(150)"),
        # ⚠️ `DEFAULT FALSE` ضروري: بلاه، الصفوف القديمة تولّي NULL،
        # و`img.is_extra == False` ما تلقاهمش (NULL ماشي False فـSQL).
        # النتيجة: كل صور المنتجات القديمة تختفي من المعرض.
        ("product_images", "is_extra", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("product_images", "caption", "VARCHAR(200)"),
        # وحدة البيع — المنتجات القديمة كلها «حبة»
        ("products", "unit", "VARCHAR(40) NOT NULL DEFAULT 'piece'"),
        # توفّر الموديل — القديمة كلها متوفرة
        ("product_variants", "is_available", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ]

    inspector = inspect(db.engine)
    added = 0

    # ⚠️ الأعمدة اليدوية فوق ما تكفيش. الدرس جانا من عمود communes.daira:
    # تزيد عمود للموديل، تنسى تسجّلو هنا، والموقع يحبس فالإنتاج بلا
    # ما تفهم ليش. درك نقارنو **كل** الموديلات مع قاعدة البيانات
    # ونزيدو أي عمود ناقص تلقائيًا.
    auto = []
    type_map = {
        "INTEGER": "INTEGER", "BIGINT": "BIGINT", "SMALLINT": "INTEGER",
        "BOOLEAN": "BOOLEAN", "TEXT": "TEXT", "DATE": "DATE",
        "DATETIME": "TIMESTAMP", "TIMESTAMP": "TIMESTAMP",
        "FLOAT": "DOUBLE PRECISION", "NUMERIC": "NUMERIC",
    }

    existing_tables = set(inspector.get_table_names())

    for table in db.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue   # جدول جديد — create_all بناه كامل

        db_columns = {c["name"] for c in inspector.get_columns(table.name)}

        for column in table.columns:
            if column.name in db_columns:
                continue

            # ⚠️ ما نزيدوش مفاتيح أولية ولا أعمدة NOT NULL بلا افتراضي:
            # الأولى مستحيلة بـALTER، والثانية تفشل إذا الجدول فيه صفوف.
            if column.primary_key:
                continue

            try:
                compiled = column.type.compile(db.engine.dialect)
            except Exception:
                compiled = "TEXT"

            base = compiled.split("(")[0].upper()
            ddl = compiled if "(" in compiled else type_map.get(base, compiled)

            # عمود إجباري بلا قيمة افتراضية → نزيدو كـnullable باش ما
            # يفشلش، والتطبيق يعمّرو بعدين
            auto.append((table.name, column.name, ddl))

    for table, column, ddl in list(wanted) + auto:
        if table not in existing_tables:
            continue

        current = {c["name"] for c in inspector.get_columns(table)}
        if column in current:
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
        # ⚠️ نفرّقو بين نوعين من الأخطاء:
        #
        # 1) خطأ **قاتل**: التطبيق ما يقدرش يخدم بلا قاعدة بيانات
        #    (مثلاً psycopg2 ما يتحمّلش، ولا الاتصال مرفوض). فهاد الحالة
        #    نوقفو مباشرة برسالة واضحة، بدل ما الموقع يعاود يقلع بلا فايدة
        #    ويعمّر السجلات بمئات الأسطر يخبّيو السبب الحقيقي.
        #
        # 2) خطأ **ثانوي**: خطوة تجهيز وحدة فشلت (مثلاً أسعار التوصيل).
        #    الموقع يقدر يخدم بلاها، فنكمّلو.
        message = str(e)

        fatal_signs = [
            "libpq",                    # psycopg2 ما لقاش مكتبة PostgreSQL
            "No module named 'psycopg2'",
            "could not connect",        # قاعدة البيانات ما ترّدش
            "password authentication",  # بيانات دخول غالطة
            'database "',               # قاعدة البيانات ماكانتش
            "could not translate host", # اسم السيرفر غالط
            "Connection refused",
            "timeout expired",
        ]

        if any(sign in message for sign in fatal_signs):
            print("=" * 60, file=sys.stderr)
            print("🔴 خطأ قاتل فالاتصال بقاعدة البيانات — وقّفنا الإقلاع", file=sys.stderr)
            print(f"   {message}", file=sys.stderr)
            print("", file=sys.stderr)
            print("   تحقق من هاذي:", file=sys.stderr)
            print("   • DATABASE_URL موجود فمتغيرات البيئة تاع خدمة web؟", file=sys.stderr)
            print("   • psycopg2-binary فـrequirements.txt متوافق مع إصدار Python؟", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            sys.exit(1)

        print(f"⚠️ خطوة تجهيز فشلت: {message}", file=sys.stderr)
        print("   الموقع راح يبدا رغم هذا — راجع السجلات.", file=sys.stderr)
