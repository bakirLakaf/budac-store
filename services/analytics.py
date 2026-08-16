"""
تتبّع الزوار — خفيف ويحترم الخصوصية.

⚠️ مبادئ التصميم:

1) **ما نخزّنوش بيانات شخصية.** ماكانش IP ولا user-agent محفوظ.
   نحسبو بصمة مشفّرة (SHA-256) من IP + المتصفح + اليوم + مفتاح سري.
   البصمة تتبدّل كل يوم، فما نقدروش نتبعو زائر عبر الأيام حتى إذا
   بغينا.

2) **ما نبطّئوش الموقع.** الحساب داخل الطلب، لكن سطر واحد UPDATE
   ولا INSERT. وإذا فشل أي شي، نبلعو الخطأ — عدّاد الزوار ما يستاهلش
   يكسر صفحة للزبون.

3) **ما نعدّوش الأدمن ولا البوتات.** الأدمن يزور موقعو 50 مرة فاليوم،
   والبوتات تزيدو ضجيج — الاثنين يخرّبو الرقم.
"""

import hashlib
import os
from datetime import date, timedelta

from flask import request, session

from models import db, SiteVisit

# مسارات ما تتعدّش: لوحة التحكم، الملفات الثابتة، وواجهات API
SKIP_PREFIXES = ("/admin", "/static", "/api", "/favicon")

# ⚠️ كلمات تدل على بوت. القائمة ماشي كاملة — البوتات الجدّية تتخفّى،
# لكن هاذي تشيل معظم الضجيج (محركات البحث، أدوات المراقبة).
# ⚠️ مسارات معروفة عند سكانرات الثغرات. موقعنا Flask، فأي طلب
# لهاد المسارات = محاولة اختراق، ماشي زائر.
SCAN_PATTERNS = (
    "wp-admin", "wp-login", "wp-content", "wp-includes", "wordpress",
    "xmlrpc", ".php", ".asp", ".jsp", ".env", ".git", ".sql",
    "phpmyadmin", "adminer", "shell", "eval-stdin", "cgi-bin",
    "config.json", "credentials", "backup", "vendor/",
)

BOT_HINTS = (
    "bot", "crawler", "spider", "slurp", "curl", "wget",
    "python-requests", "headless", "monitor", "uptime",
    "facebookexternalhit", "preview", "scan", "lighthouse",
)


def _is_bot(user_agent):
    if not user_agent:
        return True   # بلا user-agent = شبه أكيد ماشي متصفح حقيقي
    ua = user_agent.lower()
    return any(hint in ua for hint in BOT_HINTS)


def _visitor_key(today):
    """
    بصمة يومية للزائر — ما ترجعش لشخص.

    نخلطو المفتاح السري باش حتى لو تسرّبت قاعدة البيانات، ما يقدرش
    حد يعاود يبني البصمة من IP معروف.
    """
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    ip = ip.split(",")[0].strip()

    agent = request.headers.get("User-Agent", "")
    secret = os.environ.get("SECRET_KEY", "budac")

    raw = f"{ip}|{agent}|{today.isoformat()}|{secret}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def track_response(response):
    """
    يسجّل زيارة. يتنادى فـafter_request.

    ⚠️ ليش بعد الطلب وماشي قبلو؟
    الدرس جانا من الواقع: عدّاد الزوار عرض 50 «زائر» فيوم، ومنهم 80
    زيارة لـ/wp-admin/install.php و8 لـ/wp-login.php. موقعنا ماشي
    WordPress — هاذو روبوتات تدور على ثغرات، وتزوّر User-Agent ديال
    متصفح عادي فالفلتر القديم ما شافهمش.

    الحل: نعدّو **فقط الصفحات اللي موجودة فعلاً**. السكانر ياخذ 404
    فما يتعدّش. هاد الفلتر ما يقدرش يتزوّر — يعتمد على ردنا احنا،
    ماشي على شي يقولو الزائر.
    """
    try:
        # ✅ الشرط الأول: الصفحة موجودة فعلاً
        if response.status_code != 200:
            return response

        # ✅ الثاني: صفحة HTML — ماشي صورة ولا JSON ولا خط
        content_type = (response.content_type or "").lower()
        if "text/html" not in content_type:
            return response

        if request.method != "GET":
            return response

        path = request.path or "/"
        if path.startswith(SKIP_PREFIXES):
            return response

        # ✅ الثالث: مسارات المسح المعروفة — حتى لو ردّينا 200 بالغلط
        lowered = path.lower()
        if any(pattern in lowered for pattern in SCAN_PATTERNS):
            return response

        # الأدمن ما يتعدّش — يزور موقعو بزاف ويخرّب الرقم
        if session.get("admin_id") or session.get("admin_username"):
            return response

        if _is_bot(request.headers.get("User-Agent")):
            return response

        today = date.today()
        key = _visitor_key(today)

        visit = SiteVisit.query.filter_by(day=today, visitor_key=key).first()

        if visit:
            visit.views = (visit.views or 0) + 1
        else:
            referrer = (request.referrer or "")[:300]

            # الإحالات من نفس الموقع ماشي مصدر — نخليوها فارغة
            if referrer and request.host in referrer:
                referrer = ""

            visit = SiteVisit(
                day=today,
                visitor_key=key,
                views=1,
                first_page=path[:300],
                referrer=referrer or None,
            )
            db.session.add(visit)

        db.session.commit()

    except Exception:
        # ⚠️ عدّاد الزوار ما يستاهلش يكسر صفحة — نتجاهلو بصمت
        try:
            db.session.rollback()
        except Exception:
            pass

    return response


# ═══════════════════════════════════════════
# التقارير
# ═══════════════════════════════════════════

def _range_stats(start_day, end_day=None):
    """زوّار وزيارات بين تاريخين (شاملين)."""
    end_day = end_day or date.today()

    rows = (
        db.session.query(
            db.func.count(SiteVisit.id),
            db.func.coalesce(db.func.sum(SiteVisit.views), 0),
        )
        .filter(SiteVisit.day >= start_day, SiteVisit.day <= end_day)
        .first()
    )

    return {"visitors": rows[0] or 0, "views": rows[1] or 0}


def get_summary():
    """
    ملخّص للوحة التحكم: اليوم، أمس، 7 أيام، 30 يوم + رسم بياني.
    """
    today = date.today()
    yesterday = today - timedelta(days=1)

    stats = {
        "today": _range_stats(today, today),
        "yesterday": _range_stats(yesterday, yesterday),
        "week": _range_stats(today - timedelta(days=6)),
        "month": _range_stats(today - timedelta(days=29)),
    }

    # ---------- آخر 14 يوم للرسم ----------
    start = today - timedelta(days=13)
    rows = (
        db.session.query(
            SiteVisit.day,
            db.func.count(SiteVisit.id),
            db.func.coalesce(db.func.sum(SiteVisit.views), 0),
        )
        .filter(SiteVisit.day >= start)
        .group_by(SiteVisit.day)
        .all()
    )
    by_day = {r[0]: {"visitors": r[1], "views": r[2]} for r in rows}

    # ⚠️ نعمّرو الأيام الفارغة بصفر — وإلا الرسم يقفز ويوهم بنمو
    daily = []
    for i in range(14):
        d = start + timedelta(days=i)
        entry = by_day.get(d, {"visitors": 0, "views": 0})
        daily.append({
            "date": d,
            "label": d.strftime("%d/%m"),
            "visitors": entry["visitors"],
            "views": entry["views"],
        })

    peak = max((d["visitors"] for d in daily), default=0)

    # ---------- مصادر الزيارات ----------
    sources = (
        db.session.query(
            SiteVisit.referrer,
            db.func.count(SiteVisit.id),
        )
        .filter(
            SiteVisit.day >= today - timedelta(days=29),
            SiteVisit.referrer.isnot(None),
            SiteVisit.referrer != "",
        )
        .group_by(SiteVisit.referrer)
        .order_by(db.func.count(SiteVisit.id).desc())
        .limit(6)
        .all()
    )

    # ---------- صفحات الدخول ----------
    landing = (
        db.session.query(
            SiteVisit.first_page,
            db.func.count(SiteVisit.id),
        )
        .filter(SiteVisit.day >= today - timedelta(days=29))
        .group_by(SiteVisit.first_page)
        .order_by(db.func.count(SiteVisit.id).desc())
        .limit(6)
        .all()
    )

    return {
        "stats": stats,
        "daily": daily,
        "peak": peak,
        "sources": [{"name": _clean_source(s), "count": c} for s, c in sources],
        "landing": [{"path": p or "/", "count": c} for p, c in landing],
        "change": _percent_change(
            stats["today"]["visitors"], stats["yesterday"]["visitors"]
        ),
    }


def _clean_source(url):
    """يحوّل رابط الإحالة لاسم مفهوم."""
    if not url:
        return "مباشر"

    lowered = url.lower()
    known = {
        "facebook": "فيسبوك",
        "instagram": "إنستغرام",
        "google": "جوجل",
        "tiktok": "تيك توك",
        "youtube": "يوتيوب",
        "t.co": "تويتر",
        "whatsapp": "واتساب",
        "telegram": "تيليغرام",
    }
    for key, name in known.items():
        if key in lowered:
            return name

    # نرجّعو الدومين فقط
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc or url
        return host.replace("www.", "")[:40]
    except Exception:
        return url[:40]


def _percent_change(current, previous):
    """نسبة التغيّر مقارنة بأمس. None إذا ماكانش أساس نقارنو بيه."""
    if not previous:
        return None
    return round((current - previous) / previous * 100)


def prune_old(keep_days=180):
    """
    يمسح السجلات القديمة.

    ⚠️ بلاها الجدول يكبر بلا حدود. 180 يوم كافية للمقارنة السنوية
    التقريبية وتبقى خفيفة.
    """
    cutoff = date.today() - timedelta(days=keep_days)
    deleted = SiteVisit.query.filter(SiteVisit.day < cutoff).delete(
        synchronize_session=False
    )
    db.session.commit()
    return deleted


def purge_scan_noise():
    """
    يمسح سجلات الزيارات اللي جات من روبوتات المسح.

    ⚠️ ليش نحتاجوها؟
    الفلتر القديم كان يعتمد على User-Agent وحدو، والسكانرات تزوّرو.
    فدخلو زيارات لـ/wp-admin/install.php و/wp-login.php وخرّبو الأرقام.
    الفلتر الجديد يمنعهم، لكن اللي تسجّل من قبل خاصو يتنظّف.
    """
    from sqlalchemy import or_

    conditions = [
        SiteVisit.first_page.ilike(f"%{pattern}%")
        for pattern in SCAN_PATTERNS
    ]

    deleted = (
        SiteVisit.query
        .filter(or_(*conditions))
        .delete(synchronize_session=False)
    )
    db.session.commit()
    return deleted


def reset_all():
    """يصفّر عدّاد الزوار كليًا — للبداية النظيفة قبل الإطلاق."""
    deleted = SiteVisit.query.delete(synchronize_session=False)
    db.session.commit()
    return deleted
