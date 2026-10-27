# -*- coding: utf-8 -*-
"""
يعمّر جدول البلديات من القائمة الرسمية (1541 بلدية × 58 ولاية).

التشغيل:
    python seed_communes.py

آمن للتشغيل المتكرر: يزيد الناقص ويحدّث الموجود، ما يكرّرش.
المصدر: github.com/othmanus/algeria-cities
"""
import json
import os
import sys

from app import create_app
from models import db, Commune

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "algeria_communes.json")


def run():
    if not os.path.exists(DATA_FILE):
        print(f"❌ ملف البيانات ماكانش: {DATA_FILE}", file=sys.stderr)
        return 1

    with open(DATA_FILE, encoding="utf-8") as f:
        rows = json.load(f)

    print(f"📖 قرينا {len(rows)} بلدية من الملف")

    # الموجود عندنا — نفهرسو بـ(ولاية، اسم) باش نتفاداو التكرار
    existing = {
        (c.wilaya_code, c.name_ar): c
        for c in Commune.query.all()
    }

    added, updated = 0, 0

    for row in rows:
        wilaya_code = str(row["c"]).zfill(2)
        name_ar = (row.get("n") or "").strip()

        if not name_ar:
            continue

        key = (wilaya_code, name_ar)
        commune = existing.get(key)

        if commune is None:
            commune = Commune(wilaya_code=wilaya_code, name_ar=name_ar)
            db.session.add(commune)
            existing[key] = commune
            added += 1
        else:
            updated += 1

        commune.name_fr = (row.get("a") or "").strip() or None
        commune.daira = (row.get("d") or "").strip() or None

    db.session.commit()

    total = Commune.query.count()
    wilayas = db.session.query(Commune.wilaya_code).distinct().count()

    print("\n✅ خلص!")
    print(f"   بلديات جديدة : {added}")
    print(f"   محدّثة       : {updated}")
    print(f"   المجموع      : {total} بلدية فـ{wilayas} ولاية")

    if total < 1500:
        print("\n⚠️ العدد أقل من المتوقع (1541) — راجع ملف البيانات")

    return 0


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        sys.exit(run())
