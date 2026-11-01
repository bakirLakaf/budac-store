"""
فحص شامل للنظام — تقرير Markdown ينزّلو الأدمن ويبعثو للدعم.

  • قراءة برك: ما يبعث حتى رسالة (لا واتساب لا تيليغرام)، ما ينشئ طرد، ما يبدّل
    حتى طلبية. الاتصالات تتجرّب بنداءات قراءة (getMe، الولايات، قائمة الطرود...).
  • ما يخرّج حتى مفتاح: القيم السرّية تتعوّض بـ«***»، والتقرير يوري غير واش
    المتغيّر «موجود» ولا «ناقص».
  • أرقام الهاتف والأسماء والعناوين فعيّنات الطرود الخام تتخفّى (0555******12).
  • الاتصالات الخارجية تخدم بالتوازي بمهلة قصوى (باش الطلب ما يعدّيش مهلة gunicorn = 120ث).

الاستعمال: report = run(app)  ←  نص Markdown + ملخّص.
"""
import json
import logging
import os
import platform
import re
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter, deque
from concurrent import futures
from datetime import datetime, timezone

# مهلة كل اتصال خارجي + مهلة الفحص كامل (ثانية)
NET_TIMEOUT = 12
TOTAL_TIMEOUT = 85

STATUS_ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭️"}

# متغيّرات بيئة سرّية: نخبّيو قيمها بالكامل من التقرير
SECRET_ENV = (
    "SECRET_KEY", "DATABASE_URL", "SP_DATABASE_URL", "SP_SECRET", "ADMIN_PASSWORD", "JOBS_SECRET",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "WA_CLOUD_TOKEN", "WA_APP_SECRET", "WA_VERIFY_TOKEN",
    "ZR_API_KEY", "ZR_TENANT_ID", "WORLD_API_TOKEN", "YALIDINE_API_ID", "YALIDINE_API_TOKEN",
    "FB_PAGE_TOKEN", "IG_ACCESS_TOKEN", "TIKTOK_CLIENT_SECRET", "TIKTOK_CLIENT_KEY", "YOUCAN_ACCESS_TOKEN",
)
# متغيّرات نوريو إذا «موجودة/ناقصة» (السرّية + الإعدادات العادية)
ENV_PRESENCE = SECRET_ENV + (
    "SITE_URL", "STORE_NAME", "UPLOAD_ROOT", "WHATSAPP_NUMBER", "WA_PHONE_NUMBER_ID",
    "FB_PAGE_ID", "IG_USER_ID", "SP_PUBLIC_BASE_URL", "YALIDINE_FROM_WILAYA", "BACKGROUND_JOBS",
    "WEB_WORKERS", "TRACKING_SYNC_MINUTES", "FACEBOOK_PIXEL_ID", "TIKTOK_PIXEL_ID",
)
# قيم غير سرّية نوريوها كيما هي
ENV_SHOW_VALUE = ("SITE_URL", "SP_PUBLIC_BASE_URL", "BACKGROUND_JOBS", "WEB_WORKERS",
                  "TRACKING_SYNC_MINUTES", "YALIDINE_FROM_WILAYA", "UPLOAD_ROOT")

_run_lock = threading.Lock()


# ──────────────────────────── إخفاء الأسرار ────────────────────────────
_PATTERNS = [
    (re.compile(r"(?i)\b(api_token|access_token|token|api_key|apikey|api_id|key|secret|password|client_secret)=([^&\s\"']+)"), r"\1=***"),
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-~+/=]+"), "Bearer ***"),
    (re.compile(r"bot\d{6,}:[A-Za-z0-9_\-]{20,}"), "bot***"),
    (re.compile(r"(?i)(postgres(?:ql)?(?:\+\w+)?://[^:\s/]+:)[^@\s]+@"), r"\1***@"),
    (re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"), "***"),
]


def redact(value):
    """يشيل أي سرّ من نص: قيم المتغيّرات السرّية + أنماط التوكن المعروفة."""
    text = str(value)
    for name in SECRET_ENV:
        secret = (os.environ.get(name) or "").strip()
        if len(secret) >= 6:
            text = text.replace(secret, f"<{name}>")
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


# ──────────────────────────── إخفاء بيانات الزبائن ────────────────────────────
_PHONE_IN_TEXT = re.compile(r"(?<!\d)(?:\+?213|0)[\s.\-]?[5-7](?:[\s.\-]?\d){8}(?!\d)")
_PHONE_KEYS = {"phone", "tel", "telephone", "mobile", "contact_phone", "contact_phone_2", "phone_1",
               "phone_2", "number1", "number2", "customerphone"}
_NAME_KEYS = {"firstname", "familyname", "lastname", "fullname", "client", "nom_client", "customer_name",
              "customername", "recipient_name"}
_NAME_PARENTS = {"customer", "recipient", "receiver", "client", "consignee", "contact"}
_FREE_KEYS = {"address", "adresse", "email", "street", "note", "comment", "remark", "observation",
              "remarque", "product_list", "products", "commentaire"}


def mask_phone(value):
    digits = re.sub(r"\D", "", str(value))
    if len(digits) < 6:
        return "***"
    return digits[:2] + "*" * (len(digits) - 4) + digits[-2:]


def mask_name(value):
    text = str(value).strip()
    return (text[0] + "***") if text else ""


def mask_obj(obj, path=()):
    """نسخة من الكائن الخام بلا أرقام هاتف وأسماء وعناوين (الحقول الباقية تبقى باش نشخّصو)."""
    if isinstance(obj, dict):
        out = {}
        for key, val in obj.items():
            k = str(key).lower()
            parents = {str(p).lower() for p in path}
            if val in (None, "", [], {}):
                out[key] = val
            elif k in _PHONE_KEYS:
                out[key] = mask_phone(val) if not isinstance(val, (dict, list)) else mask_obj(val, path + (key,))
            elif k in _FREE_KEYS:
                out[key] = "***"
            elif (k in _NAME_KEYS or (k == "name" and parents & _NAME_PARENTS)) and not isinstance(val, (dict, list)):
                out[key] = mask_name(val)
            else:
                out[key] = mask_obj(val, path + (key,))
        return out
    if isinstance(obj, list):
        return [mask_obj(v, path) for v in obj[:5]]
    if isinstance(obj, str):
        return _PHONE_IN_TEXT.sub(lambda m: mask_phone(m.group(0)), obj)
    return obj


def mask_row(row):
    """سطر طرد موحَّد (من carrier_parcels) بلا بيانات الزبون."""
    out = dict(row)
    out["name"] = mask_name(out.get("name", ""))
    out["phone"] = mask_phone(out.get("phone", "")) if out.get("phone") else ""
    out.pop("ts", None)
    return out


# ──────────────────────────── ذاكرة الأخطاء ────────────────────────────
class _BufferHandler(logging.Handler):
    """يحتفظ بآخر أخطاء/تحذيرات التطبيق فالذاكرة (لكل عامل gunicorn) باش تدخل التقرير."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = deque(maxlen=120)

    def emit(self, record):
        try:
            self.records.append((record.created, record.levelname, record.name, record.getMessage()))
        except Exception:   # noqa: BLE001 — الـlogging ما يطيّحش التطبيق
            pass


_buffer = _BufferHandler()
_installed = False


def install_log_buffer():
    """نناديها مرة وحدة عند تشغيل التطبيق."""
    global _installed
    if _installed:
        return
    logging.getLogger().addHandler(_buffer)
    _installed = True


# ──────────────────────────── هيكل النتيجة ────────────────────────────
class Check:
    def __init__(self, name, status, detail="", block="", seconds=None):
        self.name, self.status, self.detail = name, status, detail
        self.block, self.seconds = block, seconds


def _fence(text, lang=""):
    text = str(text).replace("```", "'''")
    return f"```{lang}\n{text}\n```"


def _timed(name, fn):
    """يشغّل فحص: أي استثناء يتحوّل لـ❌ مفهوم (ما يوقّفش الباقي)."""
    start = time.time()
    try:
        check = fn()
    except Exception as e:   # noqa: BLE001
        check = Check(name, "fail", f"استثناء: {e.__class__.__name__}: {redact(e)}")
    check.seconds = round(time.time() - start, 1)
    check.name = check.name or name
    check.detail = redact(check.detail)
    check.block = redact(check.block) if check.block else ""
    return check


# ──────────────────────────── فحوص محلية (القاعدة والملفات) ────────────────────────────
def check_system(app):
    from VERSION import BUILD_NOTE, VERSION
    from importlib import metadata

    def ver(pkg):
        try:
            return metadata.version(pkg)
        except Exception:   # noqa: BLE001
            return "—"

    lines = [
        f"- الإصدار: **{VERSION}** — {BUILD_NOTE}",
        f"- Python: {sys.version.split()[0]} · {platform.system()} {platform.release()}",
        f"- الوقت (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
        "- المكتبات: " + " · ".join(f"{p} {ver(p)}" for p in
                                    ("Flask", "SQLAlchemy", "Flask-SQLAlchemy", "requests", "Pillow", "gunicorn")),
        f"- العمّال: WEB_WORKERS={os.environ.get('WEB_WORKERS', '1 (افتراضي)')}",
    ]
    return Check("النظام", "ok", block="\n".join(lines))


def check_env():
    present, missing, shown = [], [], []
    for name in ENV_PRESENCE:
        val = (os.environ.get(name) or "").strip()
        if not val:
            missing.append(name)
        elif name in ENV_SHOW_VALUE:
            shown.append(f"- `{name}` = `{val}`")
        else:
            present.append(name)
    lines = shown + [
        "- موجودة (بلا عرض القيم): " + (", ".join(f"`{n}`" for n in present) or "—"),
        "- ناقصة: " + (", ".join(f"`{n}`" for n in missing) or "—"),
    ]
    essential = [m for m in missing if m in ("SECRET_KEY", "DATABASE_URL")]
    if essential:
        status, detail = "fail", f"ناقصين أساسيين: {', '.join(essential)}"
    elif "SITE_URL" in missing:
        status, detail = "warn", "SITE_URL ناقص — الكود يستعمل https://budacdz.com الافتراضي (حطو إذا الدومين تبدّل)"
    else:
        status, detail = "ok", "المتغيّرات الاختيارية الناقصة معناها خدمة مطفية (واتساب API، شركة توصيل...) — عادي إذا ما تستعملهاش."
    return Check("متغيّرات البيئة (حضور فقط، بلا قيم سرّية)", status, detail, "\n".join(lines))


def check_database(app):
    from sqlalchemy import func, text

    from models import db

    dialect = db.engine.dialect.name
    db.session.execute(text("SELECT 1"))
    lines = [f"- المحرّك: {dialect}"]
    try:
        q = "select sqlite_version()" if dialect == "sqlite" else "select version()"
        lines.append("- النسخة: " + str(db.session.execute(text(q)).scalar())[:70])
    except Exception:   # noqa: BLE001
        pass
    rows = []
    for table in db.metadata.sorted_tables[:120]:
        try:
            n = db.session.query(func.count()).select_from(table).scalar()
        except Exception as e:   # noqa: BLE001
            db.session.rollback()
            n = f"خطأ ({e.__class__.__name__})"
        rows.append(f"| {table.name} | {n} |")
    table_md = "| الجدول | الأسطر |\n|---|---|\n" + "\n".join(rows)
    return Check("قاعدة البيانات", "ok", f"متصلة ({dialect})", "\n".join(lines) + "\n\n" + table_md)


def check_store_health():
    from sqlalchemy import func

    from models import Order, Product, ProductVariant, db

    lines, warns = [], []
    by_status = dict(db.session.query(Order.status, func.count()).group_by(Order.status).all())
    lines.append("- الطلبيات حسب الحالة: " + (" · ".join(f"{k}={v}" for k, v in sorted(by_status.items())) or "ما كاين"))

    shipped_no_track = Order.query.filter(
        Order.status.in_([Order.STATUS_SHIPPED, Order.STATUS_AT_OFFICE]),
        (Order.tracking_number.is_(None)) | (Order.tracking_number == "")).count()
    lines.append(f"- مشحونة بلا رقم تتبّع: {shipped_no_track}")
    if shipped_no_track:
        warns.append(f"{shipped_no_track} طلبية مشحونة بلا رقم تتبّع (ما تتزامنش)")

    no_carrier = Order.query.filter(
        Order.tracking_number.isnot(None), Order.tracking_number != "",
        (Order.carrier_name.is_(None)) | (Order.carrier_name == "")).count()
    lines.append(f"- فيها تتبّع بلا شركة: {no_carrier}")
    if no_carrier:
        warns.append(f"{no_carrier} طلبية فيها تتبّع بلا اسم شركة")

    from datetime import timedelta

    from models import utcnow
    old_pending = Order.query.filter(
        Order.status == Order.STATUS_PENDING,
        Order.created_at < utcnow() - timedelta(days=2)).count()
    lines.append(f"- طلبيات جديدة قديمة (أكثر من يومين بلا تأكيد): {old_pending}")
    if old_pending:
        warns.append(f"{old_pending} طلبية جديدة ما تأكّدتش من أكثر من يومين")

    no_img = Product.query.filter(
        (Product.image_filename.is_(None)) | (Product.image_filename == ""),
        Product.is_active.is_(True)).count()
    uncategorized = Product.query.filter_by(category_id=None, is_active=True).count()
    out_of_stock = Product.query.filter_by(is_active=True, stock_quantity=0).count()
    unlinked = (ProductVariant.query.join(Product).filter(Product.is_active.is_(True))
                .filter((ProductVariant.image_filename.is_(None)) | (ProductVariant.image_filename == "")).count())
    lines += [f"- منتجات مفعّلة بلا صورة: {no_img}", f"- بلا تصنيف: {uncategorized}",
              f"- نفذت كميتها: {out_of_stock}", f"- موديلات بلا صورة: {unlinked}"]
    for label, n in (("منتج بلا صورة", no_img), ("منتج بلا تصنيف", uncategorized), ("موديل بلا صورة", unlinked)):
        if n:
            warns.append(f"{n} {label}")
    status = "warn" if warns else "ok"
    return Check("سلامة بيانات المتجر", status, "؛ ".join(warns) if warns else "كلشي مليح", "\n".join(lines))


def check_jobs():
    from services import jobs
    from models import StoreSetting

    lines = [f"- المهام الخلفية: {'مفعّلة' if os.environ.get('BACKGROUND_JOBS', '1') != '0' else 'مطفية'}"]
    status, detail = "ok", ""
    raw = StoreSetting.get("jobs_sync_last")
    if raw and str(raw).isdigit():
        age = int(time.time()) - int(raw)
        lines.append(f"- آخر مزامنة تتبّع: قبل {age // 60} دقيقة (الدورة كل {jobs.SYNC_EVERY // 60} دقيقة)")
        if age > jobs.SYNC_EVERY * 3:
            status, detail = "warn", f"المزامنة متوقّفة من {age // 60} دقيقة — راجع BACKGROUND_JOBS ولا الـcron"
    else:
        lines.append("- آخر مزامنة تتبّع: ما تسجّلتش بعد")
    return Check("المهام الخلفية (مزامنة التتبّع)", status, detail, "\n".join(lines))


def check_whatsapp_outbox():
    from sqlalchemy import func

    from models import WhatsAppOutbox, db

    counts = dict(db.session.query(WhatsAppOutbox.status, func.count()).group_by(WhatsAppOutbox.status).all())
    lines = ["- حسب الحالة: " + (" · ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "فارغ")]
    failed = (WhatsAppOutbox.query.filter_by(status="failed")
              .order_by(WhatsAppOutbox.id.desc()).limit(5).all())
    for row in failed:
        lines.append(f"- ❌ #{row.id} ({row.kind}) محاولات={row.attempts}: {row.error or '—'}")
    status = "warn" if counts.get("failed") else "ok"
    return Check("صندوق واتساب التلقائي", status,
                 f"{counts.get('failed', 0)} رسالة فاشلة" if status == "warn" else "", "\n".join(lines))


def check_uploads(app):
    from services.images import get_upload_dir

    path = get_upload_dir("products")   # مجلد موجود أصلاً (ما نخلقوش مجلد زايد)
    base = os.path.dirname(str(path)) if path else ""
    lines = [f"- مجلد الصور: `{base or '—'}`"]
    status, detail = "ok", ""
    try:
        os.makedirs(path, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path, prefix="diag_", suffix=".tmp")
        os.write(fd, b"ok")
        os.close(fd)
        os.remove(tmp)
        lines.append("- الكتابة: ✅ نجحت (ملف تجريبي اتمسح)")
    except Exception as e:   # noqa: BLE001
        status, detail = "fail", f"ما نقدروش نكتبو فمجلد الصور: {redact(e)}"
        lines.append(f"- الكتابة: ❌ {redact(e)}")
    try:
        usage = shutil.disk_usage(base or ".")
        free_gb = usage.free / 1024 ** 3
        lines.append(f"- مساحة حرة: {free_gb:.1f} GB من {usage.total / 1024 ** 3:.1f} GB")
        if free_gb < 0.5 and status == "ok":
            status, detail = "warn", f"مساحة القرص قليلة ({free_gb:.2f} GB)"
    except Exception:   # noqa: BLE001
        pass
    lines.append(f"- UPLOAD_ROOT: {'مضبوط (قرص دائم)' if app.config.get('UPLOAD_ROOT') else 'غير مضبوط (الصور تتمسح مع كل نشر!)'}")
    if not app.config.get("UPLOAD_ROOT") and status == "ok":
        status, detail = "warn", "UPLOAD_ROOT غير مضبوط: الصور المرفوعة تتمسح مع كل نشر جديد (إلا إذا ما تستعملش Railway)"
    lines.append(f"- FFmpeg: {'موجود' if shutil.which('ffmpeg') else 'غير موجود'}")
    return Check("الصور والملفات", status, detail, "\n".join(lines))


def check_logs():
    if not _buffer.records:
        return Check("آخر أخطاء التطبيق (من تشغيل هاد العامل)", "ok", "ما كاين حتى تحذير/خطأ مسجّل")
    lines, last, repeat = [], None, 0
    for ts, level, name, message in list(_buffer.records)[-60:]:
        when = datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d %H:%M:%S")
        key = (level, name, message[:300])
        if key == last:                       # نفس الرسالة تتعاود: نجمّعوها (×N)
            repeat += 1
            lines[-1] = f"{lines[-1].split(' ×')[0]} ×{repeat + 1}"
            continue
        last, repeat = key, 0
        lines.append(f"{when} {level:<7} {name}: {message[:300]}")
    errors = sum(1 for r in _buffer.records if r[1] in ("ERROR", "CRITICAL"))
    return Check("آخر أخطاء التطبيق (من تشغيل هاد العامل)", "warn" if errors else "ok",
                 f"{errors} خطأ و{len(_buffer.records) - errors} تحذير مسجّل", _fence("\n".join(lines)))


# ──────────────────────────── فحوص الاتصالات (بالتوازي) ────────────────────────────
def _carrier(code, label, test_fn):
    from services import carrier_parcels as cp

    ok, message = test_fn()
    lines = [f"- اختبار المفاتيح: {'✅' if ok else '❌'} {message}"]
    if not ok:
        return Check(label, "fail", str(message), "\n".join(lines))
    payload = cp.get(code, force=True)
    if not payload.get("ok"):
        lines.append(f"- قائمة الطرود: ❌ {payload.get('error')}")
        return Check(label, "fail", f"المفاتيح صالحة لكن قائمة الطرود فشلت: {payload.get('error')}", "\n".join(lines))
    rows = payload["rows"]
    lines.append(f"- قائمة الطرود: ✅ {len(rows)} طرد" + (f" (المجموع حسب الشركة: {payload['total']})" if payload.get("total") else "")
                 + (" — وصلنا للحد الأقصى" if payload.get("truncated") else ""))
    status, detail = "ok", ""
    if rows:
        cats = Counter(r["category"] for r in rows)
        lines.append("- التصنيف: " + " · ".join(f"{cp.CATEGORY_LABELS[k]}={v}" for k, v in cats.items()))
        states = Counter(r["state"] for r in rows).most_common(12)
        lines.append("- الحالات الخام (الأكثر): " + " · ".join(f"{s}×{n}" for s, n in states))
        # واش استخراج الحقول مليح؟ حقل فارغ فأغلب الأسطر = اسم حقل غالط غالبًا
        weak = []
        for field in ("name", "phone", "wilaya", "amount", "state", "date"):
            empty = sum(1 for r in rows if r.get(field) in ("", None, "—"))
            lines.append(f"- حقل `{field}` فارغ فـ {empty}/{len(rows)}")
            if empty > len(rows) * 0.6:
                weak.append(field)
        if weak:
            status, detail = "warn", "حقول فارغة فأغلب الطرود: " + ", ".join(weak) + " (أسماء الحقول يمكن تحتاج تعديل — شوف العيّنة الخام)"
        lines.append("\n**أول 3 طرود بعد التوحيد (مقنّعة):**\n" + _fence(
            json.dumps([mask_row(r) for r in rows[:3]], ensure_ascii=False, indent=2), "json"))
        lines.append("**أول طرد خام كيما رجّعتو الشركة (مقنّع):**\n" + _fence(
            json.dumps(mask_obj(payload.get("raw_sample") or {}), ensure_ascii=False, indent=2)[:3500], "json"))
    else:
        lines.append("- ⚠️ متصلة بصح ما رجّعت حتى طرد")
        status, detail = "warn", "الاتصال مليح بصح القائمة فارغة (حساب جديد؟ ولا مسار/ترقيم غالط)"
    return Check(label, status, detail, "\n".join(lines))


def check_zr():
    from services import zrexpress
    if not zrexpress.is_configured():
        return Check("ZR Express", "skip", "غير مربوطة (ZR_TENANT_ID / ZR_API_KEY)")
    return _carrier("zr", "ZR Express", zrexpress.test_connection)


def check_world():
    from services import worldexpress
    if not worldexpress.is_configured():
        return Check("World Express", "skip", "غير مربوطة (WORLD_API_TOKEN)")
    return _carrier("world", "World Express", worldexpress.test_connection)


def check_yalidine():
    from services import yalidine
    if not yalidine.is_configured():
        return Check("Yalidine", "skip", "غير مربوطة (YALIDINE_API_ID / YALIDINE_API_TOKEN)")
    return _carrier("yalidine", "Yalidine", yalidine.test_connection)


def check_telegram():
    import requests

    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token or not (os.environ.get("TELEGRAM_CHAT_ID") or "").strip():
        return Check("Telegram (إشعارات الطلبيات)", "skip", "غير مربوط (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=NET_TIMEOUT)   # قراءة برك، بلا رسالة
    js = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.ok and js.get("ok"):
        return Check("Telegram (إشعارات الطلبيات)", "ok", f"البوت @{js['result'].get('username')} يخدم (ما بعثناش حتى رسالة)")
    return Check("Telegram (إشعارات الطلبيات)", "fail", f"HTTP {r.status_code}: {js.get('description') or r.text[:150]}")


def check_whatsapp_cloud():
    import requests

    from services import whatsapp_api as api

    if not api.is_configured():
        return Check("واتساب Cloud API (التلقائي)", "skip", "غير مضبوط — الأزرار اليدوية (wa.me) تخدم عادي")
    token = (os.environ.get("WA_CLOUD_TOKEN") or "").strip()
    version = os.environ.get("WA_GRAPH_VERSION", "v21.0").strip()
    url = f"https://graph.facebook.com/{version}/{os.environ.get('WA_PHONE_NUMBER_ID', '').strip()}"
    r = requests.get(url, params={"fields": "display_phone_number,verified_name,quality_rating"},
                     headers={"Authorization": f"Bearer {token}"}, timeout=NET_TIMEOUT)
    js = r.json() if r.content else {}
    if r.ok:
        return Check("واتساب Cloud API (التلقائي)", "ok",
                     f"الرقم {js.get('display_phone_number', '—')} · {js.get('verified_name', '—')} · الجودة {js.get('quality_rating', '—')}")
    err = (js.get("error") or {})
    return Check("واتساب Cloud API (التلقائي)", "fail",
                 f"HTTP {r.status_code}: {err.get('message', r.text[:150])} (code {err.get('code')})")


def check_meta():
    """فيسبوك + انستغرام + الرسائل (نفس نداءات تبويب الرسائل)."""
    from social_publisher.config import cfg

    if not getattr(cfg, "fb_page_token", ""):
        return Check("فيسبوك / انستغرام", "skip", "أداة النشر غير مفعّلة هنا (تحتاج PostgreSQL) ولا FB_PAGE_TOKEN ناقص")
    from social_publisher import inbox
    from social_publisher.platforms.base import PlatformError, graph

    lines, problems = [], []
    try:
        js = graph("GET", cfg.fb_page_id, cfg.fb_page_token, params={"fields": "name"})
        lines.append(f"- صفحة فيسبوك: ✅ {js.get('name')}")
    except PlatformError as e:
        lines.append(f"- صفحة فيسبوك: ❌ {redact(e)}")
        problems.append("فيسبوك")
    if getattr(cfg, "ig_user_id", ""):
        try:
            js = graph("GET", cfg.ig_user_id, cfg.ig_token, params={"fields": "username"})
            lines.append(f"- حساب انستغرام: ✅ @{js.get('username')}")
        except PlatformError as e:
            lines.append(f"- حساب انستغرام: ❌ {redact(e)}")
            problems.append("انستغرام")
    else:
        lines.append("- حساب انستغرام: ⏭️ IG_USER_ID ناقص")
    for platform_name in inbox.PLATFORMS:
        res = inbox.list_conversations(platform_name, force=True)
        if res["ok"]:
            lines.append(f"- رسائل {platform_name}: ✅ {len(res['items'])} محادثة")
        elif res["configured"]:
            lines.append(f"- رسائل {platform_name}: ❌ {redact(res['error'])}")
            problems.append(f"رسائل {platform_name}")
        else:
            lines.append(f"- رسائل {platform_name}: ⏭️ {res['error']}")
    lines.append("- تيك توك: ⏭️ ما يوفّرش API للرسائل (النشر برك، من تبويب الربط)")
    return Check("فيسبوك / انستغرام / الرسائل", "fail" if problems else "ok",
                 ("مشاكل: " + "، ".join(problems)) if problems else "", "\n".join(lines))


def check_site():
    import requests

    from services import whatsapp_api

    url = whatsapp_api.site_url()
    start = time.time()
    r = requests.get(url, timeout=NET_TIMEOUT, allow_redirects=True, headers={"User-Agent": "budac-diagnostics"})
    ms = int((time.time() - start) * 1000)
    status = "ok" if r.ok else "fail"
    return Check("الموقع من الخارج", status, f"{url} ← HTTP {r.status_code} في {ms}ms")


NETWORK_CHECKS = (
    ("ZR Express", check_zr), ("World Express", check_world), ("Yalidine", check_yalidine),
    ("Telegram (إشعارات الطلبيات)", check_telegram), ("واتساب Cloud API (التلقائي)", check_whatsapp_cloud),
    ("فيسبوك / انستغرام / الرسائل", check_meta), ("الموقع من الخارج", check_site),
)


# ──────────────────────────── التشغيل وبناء التقرير ────────────────────────────
def collect(app):
    """يشغّل كل الفحوص ويرجّع قائمة Check. الفحوص المحلية فالخيط الرئيسي (القاعدة)، الشبكية بالتوازي."""
    results = []

    def local(name, fn):
        return _timed(name, fn)

    results += [
        local("النظام", lambda: check_system(app)),
        local("متغيّرات البيئة", check_env),
        local("قاعدة البيانات", lambda: check_database(app)),
        local("سلامة بيانات المتجر", check_store_health),
        local("المهام الخلفية", check_jobs),
        local("صندوق واتساب التلقائي", check_whatsapp_outbox),
        local("الصور والملفات", lambda: check_uploads(app)),
    ]

    def wrapped(name, fn):
        def run():
            with app.app_context():
                return _timed(name, fn)
        return run

    # ⚠️ بلا `with`: الخروج منو يستنّى الخيوط المعلّقة (shutdown(wait=True)) ويضيّع المهلة
    pool = futures.ThreadPoolExecutor(max_workers=len(NETWORK_CHECKS))
    pending = {pool.submit(wrapped(n, f)): n for n, f in NETWORK_CHECKS}
    done, not_done = futures.wait(pending, timeout=TOTAL_TIMEOUT)
    for fut in done:
        results.append(fut.result())
    for fut in not_done:
        results.append(Check(pending[fut], "fail", f"ما ردّتش فـ{TOTAL_TIMEOUT} ثانية (تعليق/بطء في الشركة)"))
    pool.shutdown(wait=False, cancel_futures=True)

    results.append(local("آخر أخطاء التطبيق", check_logs))
    return results


def build_report(checks, site=""):
    counts = Counter(c.status for c in checks)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    order = {"fail": 0, "warn": 1, "ok": 2, "skip": 3}
    ranked = sorted(checks, key=lambda c: order[c.status])

    out = [
        "# تقرير الفحص الشامل — Budac",
        f"_{now}_ · {site}",
        "",
        f"**الملخّص:** ✅ {counts['ok']} · ⚠️ {counts['warn']} · ❌ {counts['fail']} · ⏭️ {counts['skip']} (غير مفعّلة)",
        "",
        "> هاد الملف ما فيهش مفاتيح ولا توكنات (تتعوّض بـ«***») وأرقام الزبائن وأسماؤهم مقنّعة. تقدر تبعثو كيما هو.",
        "",
        "## الأهم أولاً",
    ]
    problems = [c for c in ranked if c.status in ("fail", "warn")]
    if problems:
        for c in problems:
            out.append(f"- {STATUS_ICON[c.status]} **{c.name}** — {c.detail}")
    else:
        out.append("- ✅ ما كاين حتى مشكل.")
    out += ["", "## كل الفحوص"]
    for c in ranked:
        secs = f" ({c.seconds}ث)" if c.seconds is not None else ""
        out.append(f"\n### {STATUS_ICON[c.status]} {c.name}{secs}")
        if c.detail:
            out.append(c.detail)
        if c.block:
            out.append("")
            out.append(c.block)
    return "\n".join(out).strip() + "\n", dict(counts)


def run(app):
    """نقطة الدخول: (نص التقرير، {status: عدد}). فحص واحد فالمرة (قفل)."""
    if not _run_lock.acquire(blocking=False):
        raise RuntimeError("فحص آخر راه يخدم — استنى يكمل")
    try:
        from services import whatsapp_api
        checks = collect(app)
        return build_report(checks, site=whatsapp_api.site_url())
    finally:
        _run_lock.release()
