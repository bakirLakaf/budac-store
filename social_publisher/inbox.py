"""صندوق الرسائل: محادثات صفحة فيسبوك (Messenger) + انستغرام (DM) من Meta Graph API.

⚠️ تيك توك: ما كاينش API عمومي لقراءة الرسائل ولا الرد عليها (Business Messaging
API مخصّصة لشركاء معتمدين برك). علاه ما ندعموهاش هنا، والواجهة تقول هذا بوضوح.

ما نخزّنو حتى رسالة فقاعدة البيانات: كلشي يتجبد مباشرة من Meta (باش ما نزيدوش حمل
ولا بيانات حساسة فالسيرفر). ذاكرة قصيرة (20 ثانية) برك باش التحديث التلقائي ما يغرقش Meta.
"""
import json
import re
import threading
import time

from .config import cfg, redact
from .platforms.base import PlatformError, graph

PLATFORMS = ("facebook", "instagram")
CONV_LIMIT = 25          # عدد المحادثات لكل منصة
MSG_LIMIT = 30           # عدد الرسائل فالمحادثة الوحدة
MAX_REPLY_BYTES = 1000   # حد Meta للنص (بايت UTF-8) — العربية حرف = 2 بايت تقريبًا
CACHE_TTL = 20

_ID_RE = re.compile(r"^[A-Za-z0-9_.:=-]{1,120}$")
_cache = {}
_lock = threading.Lock()


# ──────────────────────────── أدوات ────────────────────────────
def valid_id(value):
    """معرّف محادثة/مستخدم: حروف وأرقام ورموز بسيطة برك (ما ندخّلوش أي حاجة فمسار Graph)."""
    return bool(value and _ID_RE.match(str(value)))


def configured():
    """واش الربط جاهز: كل منصة وكلاش تحتاج."""
    has_page = bool(cfg.fb_page_id and cfg.fb_page_token)
    return {"facebook": has_page, "instagram": has_page and bool(cfg.ig_user_id)}


def _own_ids():
    return {x for x in (cfg.fb_page_id, cfg.ig_user_id) if x}


_HINTS = {
    "190": "التوكن انتهت صلاحيتو ولا تبدّل — ولّد FB_PAGE_TOKEN جديد وحطو فـ Railway.",
    "10": "الصلاحية ناقصة أو مرّت 24 ساعة على آخر رسالة من الزبون (Meta تمنع الرد بعد 24 ساعة).",
    "200": "صلاحية ناقصة: لازم pages_messaging (وللانستغرام instagram_manage_messages) فالتوكن.",
    "230": "صلاحية ناقصة: لازم pages_messaging (وللانستغرام instagram_manage_messages) فالتوكن.",
    "551": "الزبون ما عادش متاح للرد (حذف المحادثة ولا حظر الصفحة).",
    "2018278": "مرّت 24 ساعة على آخر رسالة من الزبون — Meta تمنع الرد خارج هاد المدة.",
    "2534014": "الزبون ما بعثش رسالة (ما نقدروش نردو عليه).",
}


def _friendly(err):
    """يزيد تلميح بالدارجة على خطأ Meta حسب الكود."""
    text = redact(err)
    m = re.search(r"code (\d+)/(\w+)", text)
    if m:
        hint = _HINTS.get(m.group(2)) or _HINTS.get(m.group(1))
        if hint:
            return f"{text}\n💡 {hint}"
    return text


def _cached(key, loader, force=False):
    with _lock:
        hit = _cache.get(key)
    if hit and not force and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    value = loader()
    with _lock:
        _cache[key] = (time.time(), value)
    return value


def clear_cache():
    with _lock:
        _cache.clear()


# ──────────────────────────── المحادثات ────────────────────────────
def _other_party(participants):
    """الطرف الثاني (الزبون): أول مشارك ماشي الصفحة ولا حساب الانستغرام تاعنا."""
    mine = _own_ids()
    for p in (participants or {}).get("data", []) or []:
        if str(p.get("id")) not in mine:
            return p
    return {}


def _fetch_conversations(platform):
    graph_platform = "messenger" if platform == "facebook" else "instagram"
    base = {"platform": graph_platform, "limit": CONV_LIMIT}
    fields = "id,updated_time,snippet,unread_count,participants{id,name,username}"
    try:
        js = graph("GET", f"{cfg.fb_page_id}/conversations", cfg.fb_page_token,
                   params={**base, "fields": fields})
    except PlatformError:
        # unread_count ما تتقبلش دايمًا (خصوصًا انستغرام) — نعاودو بحقول أبسط
        js = graph("GET", f"{cfg.fb_page_id}/conversations", cfg.fb_page_token,
                   params={**base, "fields": "id,updated_time,snippet,participants{id,name,username}"})
    items = []
    for c in js.get("data", []) or []:
        other = _other_party(c.get("participants"))
        name = other.get("name") or other.get("username") or "زبون"
        items.append({
            "id": c.get("id"),
            "platform": platform,
            "name": name,
            "username": other.get("username") or "",
            "user_id": other.get("id") or "",
            "snippet": (c.get("snippet") or "")[:140],
            "updated_time": c.get("updated_time") or "",
            "unread": int(c.get("unread_count") or 0),
        })
    return items


def list_conversations(platform, force=False):
    """يرجّع {ok, configured, items, error}. فشل منصة ما يوقّفش الأخرى."""
    if platform not in PLATFORMS:
        return {"ok": False, "configured": False, "items": [], "error": "منصة غير معروفة"}
    if not configured()[platform]:
        need = "FB_PAGE_ID و FB_PAGE_TOKEN" if platform == "facebook" else "FB_PAGE_ID و FB_PAGE_TOKEN و IG_USER_ID"
        return {"ok": False, "configured": False, "items": [], "error": f"الربط ناقص: حط {need} فـ Railway."}
    try:
        items = _cached(("conv", platform), lambda: _fetch_conversations(platform), force)
        return {"ok": True, "configured": True, "items": items, "error": ""}
    except PlatformError as e:
        return {"ok": False, "configured": True, "items": [], "error": _friendly(e)}
    except Exception as e:  # noqa: BLE001 — منصة وحدة ما توقّفش الباقي
        return {"ok": False, "configured": True, "items": [], "error": redact(e)}


def get_messages(conversation_id):
    """رسائل محادثة (الأقدم أولاً). كل رسالة: {id, text, mine, time, has_attachment}."""
    if not valid_id(conversation_id):
        raise PlatformError("معرّف المحادثة غير صالح")
    js = graph("GET", conversation_id, cfg.fb_page_token, params={
        "fields": f"messages.limit({MSG_LIMIT}){{message,from,created_time,attachments}}"})
    mine = _own_ids()
    rows = []
    for m in ((js.get("messages") or {}).get("data") or []):
        sender = m.get("from") or {}
        attachments = ((m.get("attachments") or {}).get("data")) or []
        rows.append({
            "id": m.get("id"),
            "text": m.get("message") or "",
            "mine": str(sender.get("id")) in mine,
            "sender": sender.get("name") or sender.get("username") or "",
            "time": m.get("created_time") or "",
            "has_attachment": bool(attachments),
        })
    rows.reverse()   # Meta ترجّع الأحدث أولاً
    return rows


# ──────────────────────────── الرد ────────────────────────────
def send_reply(platform, recipient_id, text):
    """يبعث ردّ نصي. يرمي PlatformError برسالة مفهومة."""
    if platform not in PLATFORMS or not configured()[platform]:
        raise PlatformError("هاد المنصة ما هيش مربوطة")
    if not valid_id(recipient_id):
        raise PlatformError("معرّف الزبون غير صالح")
    text = (text or "").strip()
    if not text:
        raise PlatformError("اكتب الرسالة قبل")
    if len(text.encode("utf-8")) > MAX_REPLY_BYTES:
        raise PlatformError("الرسالة طويلة بزاف — قصّرها (الحد تقريبًا 500 حرف بالعربية).")
    try:
        js = graph("POST", f"{cfg.fb_page_id}/messages", cfg.fb_page_token, data={
            "recipient": json.dumps({"id": recipient_id}),
            "message": json.dumps({"text": text}, ensure_ascii=False),
            "messaging_type": "RESPONSE",
        })
    except PlatformError as e:
        raise PlatformError(_friendly(e))
    clear_cache()
    return str(js.get("message_id") or "")
