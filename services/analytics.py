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

from flask import request, session, current_app

from models import db, SiteVisit

# مسارات ما تتعدّش: لوحة التحكم، الملفات الثابتة، وواجهات API
SKIP_PREFIXES = ("/admin", "/static", "/api", "/favicon", "/sp/")

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

# ⚠️ ملفات تقنية لمحركات البحث — ماشي صفحات يزورها بنيادم.
# ظهرو فالإحصائيات وأوهمو أنهم زوّار.
NON_PAGE_PATHS = (
    "/robots.txt", "/sitemap.xml", "/favicon.ico",
    "/manifest.json", "/sw.js", "/ads.txt",
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
    # 🔴 إصلاح: كان هنا قراءة خام لـX-Forwarded-For.
    # هاد الهيدر **يتحكم فيه العميل**: يقدر يبعث قيمة مختلفة فكل
    # طلب، فتتولّد بصمة جديدة فكل مرة، فيتزاد صف جديد فـsite_visits
    # مع كل طلب — تضخّم جدول بلا حد + أرقام زوّار مزوّرة.
    #
    # ProxyFix (فـapp.py، x_for=1) خلاص عالج المشكل: ياخذ العنوان
    # الصحيح حسب عدد البروكسيات الموثوقة ويحطّو فـremote_addr.
    # فنثقو فيه هو، ماشي فالهيدر الخام.
    ip = request.remote_addr or ""

    agent = request.headers.get("User-Agent", "")
    secret = os.environ.get("SECRET_KEY", "budac")

    raw = f"{ip}|{agent}|{today.isoformat()}|{secret}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


# ══════════════════════════════════════════════════════════════
# تجميع الزيارات فالذاكرة
# ══════════════════════════════════════════════════════════════
#
# لكل بصمة زائر نحتفظو بـ(وقت آخر كتابة، زيارات مازال ما تكتبتش).
# الكتابة تصرا كل FLUSH_SECONDS على الأكثر، وما تضيع حتى زيارة:
# اللي تجمّع فالذاكرة يتزاد للعدّاد وقت الكتابة الجاية.
#
# ⚠️ محدودية معروفة: عامل جديد (بعد نشر ولا إعادة تشغيل) يبدا
# بذاكرة فارغة، فيمكن تضيع زيارات ما تكتبتش بعد. مقبول تمامًا —
# هاذي إحصائيات تقريبية، ماشي محاسبة.

_PENDING = {}
FLUSH_SECONDS = 300          # 5 دقايق
_MAX_PENDING_KEYS = 5000     # سقف الذاكرة


def _take_pending(key):
    """
    يرجّع عدد الزيارات اللي لازم تتكتب درك، أو None إذا مازال بكري.
    """
    from time import time as _now

    now = _now()
    last_write, buffered = _PENDING.get(key, (0.0, 0))
    buffered += 1

    if now - last_write < FLUSH_SECONDS:
        _PENDING[key] = (last_write, buffered)
        return None

    _PENDING[key] = (now, 0)
    _prune_pending(now)
    return buffered


def reset_pending():
    """
    يفرّغ ذاكرة التجميع.

    ⚠️ للاختبارات: كل اختبار عندو قاعدة بيانات جديدة، لكن هاد
    القاموس عام على مستوى الوحدة فيعيش بينهم — فالاختبار الثاني
    يظن أن الزيارة اتكتبت خلاص وما يكتبش والو.
    (فالإنتاج ماكانش المشكل: قاعدة البيانات ما تتفرّغش، والمفتاح
    يتبدّل كل يوم مع التاريخ.)
    """
    _PENDING.clear()


def _prune_pending(now):
    """تنظيف: ما نخلّيوش القاموس يكبر بلا حدود."""
    if len(_PENDING) <= _MAX_PENDING_KEYS:
        return
    stale = [k for k, (ts, _) in _PENDING.items() if now - ts > 86400]
    for k in stale:
        _PENDING.pop(k, None)
    # مازال كبير؟ نفرّغوه — أحسن من تسريب ذاكرة
    if len(_PENDING) > _MAX_PENDING_KEYS:
        _PENDING.clear()


def track_response(response):
    """
    يسجّل زيارة. يتنادى فـafter_request.

    ⚠️ علاش بعد الطلب وماشي قبلو؟
    الدرس جانا من الواقع: عدّاد الزوار عرض 50 «زائر» فيوم، ومنهم 80
    زيارة لـ/wp-admin/install.php و8 لـ/wp-login.php. موقعنا ماشي
    WordPress — هاذو روبوتات تدور على ثغرات، وتزوّر User-Agent تاع
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

        # ملفات تقنية — روبوتات البحث تقراها، ماشي زوّار
        if lowered in NON_PAGE_PATHS:
            return response

        # الأدمن ما يتعدّش — يزور موقعو بزاف ويخرّب الرقم
        if session.get("admin_id") or session.get("admin_username"):
            return response

        if _is_bot(request.headers.get("User-Agent")):
            return response

        today = date.today()
        key = _visitor_key(today)

        # ⚠️ ما نكتبوش لقاعدة البيانات فكل عرض صفحة.
        # قبل: SELECT + UPDATE + COMMIT مع **كل** صفحة HTML — يعني
        # رحلة زيادة لقاعدة البيانات على المسار الحرج، وتزاحم على
        # نفس الصف كي عدة زبائن يشاركو نفس العنوان (شائع بزاف
        # فبيانات الجوال).
        #
        # درك نجمّعو الزيارات فالذاكرة ونكتبوهم دفعة. ما نستعملوش
        # الجلسة لهذا: الجلسة كوكي موقّع يتبعث مع **كل** طلب،
        # فتكبيرها يكلّف كل زائر بايتات زيادة فكل صورة وملف.
        pending = _take_pending(key)
        if pending is None:
            return response          # مازال بكري — تجمّعت فالذاكرة برك

        visit = SiteVisit.query.filter_by(day=today, visitor_key=key).first()

        if visit:
            visit.views = (visit.views or 0) + pending
        else:
            referrer = (request.referrer or "")[:300]

            # الإحالات من نفس الموقع ماشي مصدر — نخليوها فارغة
            if referrer and request.host in referrer:
                referrer = ""

            visit = SiteVisit(
                day=today,
                visitor_key=key,
                views=pending,
                first_page=path[:300],
                referrer=referrer or None,
            )
            db.session.add(visit)

        db.session.commit()

    except Exception:
        # ⚠️ عدّاد الزوار ما يستاهلش يكسر صفحة للزبون — نكملو عادي.
        # لكن **ما نبلعوش الخطأ بصمت**: نسجّلوه فسجلات Railway،
        # وإلا عدّاد معطّل يبقى معطّل شهور بلا ما نعرفو.
        current_app.logger.exception("📊 فشل تسجيل الزيارة")
        try:
            db.session.rollback()
        except Exception:
            # التراجع نفسو فشل (الاتصال مقطوع) — ماكانش شي نديروه
            current_app.logger.warning("📊 فشل التراجع عن الجلسة كذلك")

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

    ⚠️ علاش نحتاجوها؟
    الفلتر القديم كان يعتمد على User-Agent وحدو، والسكانرات تزوّرو.
    فدخلو زيارات لـ/wp-admin/install.php و/wp-login.php وخرّبو الأرقام.
    الفلتر الجديد يمنعهم، لكن اللي تسجّل من قبل لازمو يتنظّف.
    """
    from sqlalchemy import or_

    conditions = [
        SiteVisit.first_page.ilike(f"%{pattern}%")
        for pattern in SCAN_PATTERNS
    ]
    # الملفات التقنية كذلك
    conditions += [
        SiteVisit.first_page == path for path in NON_PAGE_PATHS
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


def week_start(today=None):
    """
    أول يوم فالأسبوع الجاري = **السبت**.

    ⚠️ ماشي الاثنين ولا الأحد: الأسبوع فالجزائر يبدا السبت، والعطلة
    الجمعة والسبت. `weekday()` تاع بايثون: الاثنين=0 … السبت=5، الأحد=6.
    الحساب تحت يرجّع السبت اللي فات (ولا اليوم نفسو إذا كان سبت).
    """
    today = today or date.today()
    # السبت=5 → 0 · الأحد=6 → 1 · الاثنين=0 → 2 · … · الجمعة=4 → 6
    days_since_saturday = (today.weekday() - 5) % 7
    return today - timedelta(days=days_since_saturday)


def keep_current_week(today=None):
    """
    يمسح كل سجل الزيارات **ما عدا** الأسبوع الجاري (من السبت لليوم).

    يرجّع (عدد الأسطر الممسوحة، تاريخ بداية الأسبوع).

    ⚠️ نمسحو `day < السبت` برك. أي سطر بتاريخ مستقبلي (ساعة سيرفر
    غالطة) يبقى — أحسن من مسح بيانات صحيحة بالغلط.
    """
    start = week_start(today)
    deleted = (
        SiteVisit.query
        .filter(SiteVisit.day < start)
        .delete(synchronize_session=False)
    )
    db.session.commit()
    return deleted, start
