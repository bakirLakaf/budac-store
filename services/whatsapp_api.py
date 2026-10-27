"""
رسائل واتساب التلقائية عبر WhatsApp Business **Cloud API** الرسمي (Meta).

════════════════════════════════════════════════════════════════
علاش API رسمي ماشي «ربط واتساب ويب» ولا مكتبات غير رسمية؟
────────────────────────────────────────────────────────────────
الواتساب ما يسمحش لحساب عادي يبعث رسائل تلقائية. الطرق غير الرسمية
(QR / واتساب ويب) كتخلّي الرقم يتحظر، ورقم المتجر هو قناة الزبائن —
خسارتو أكبر من أي فايدة. الـCloud API هو الطريق الوحيد الآمن.

شروطو (لازم تعرفهم):
  1. حساب Meta Business + رقم واتساب للمتجر مسجّل فـCloud API
     (الرقم ما يبقاش يخدم فتطبيق واتساب العادي على نفس الشريحة).
  2. رسالة تبعثها **أنت أولاً** (تأكيد، شحن...) لازم تكون «قالب» (Template)
     معتمد من Meta مسبقًا — يتقدّم من لوحتهم ويتقبّل فعادةً من دقائق لـ24 ساعة.
  3. Meta تحاسب على المحادثات (رسوم صغيرة لكل محادثة). شوف تسعيرهم.
نصوص القوالب الجاهزة للنسخ كاملة فـWHATSAPP_SETUP.md.

كيفاش يخدم؟
  • enqueue(order, kind) يسجّل صف فجدول `whatsapp_outbox` (فنفس transaction
    تاع الطلبية → ما تضيعش رسالة أبدًا).
  • process_outbox() (الخيط الخلفي، services/jobs.py) يبعث الرسائل المعلّقة.
  • unique(order, kind) = نفس الرسالة ما تتبعثش مرتين مهما صرا.
  • إذا الـAPI ماهوش مضبوط: ما نسجّلو والو، والأدمن يبقى يبعث يدويًا
    بأزرار wa.me القديمة (ما تبدّل والو).
════════════════════════════════════════════════════════════════
"""

import hashlib
import hmac
import logging
import os

import requests
from sqlalchemy.exc import IntegrityError

from models import db, Order, StoreSetting, WhatsAppOutbox, utcnow
from utils import normalize_algerian_phone

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
HTTP_TIMEOUT = 15

# نوع الرسالة ← (اسم القالب الافتراضي عند Meta، المتغيّرات بالترتيب {{1}}, {{2}}...)
# ⚠️ الترتيب هنا = الترتيب فالقالب المعتمد. إذا بدّلتو بدّل القالب عند Meta.
KINDS = {
    "confirm": {
        "template": "budac_order_confirm",
        "params": ["name", "order_number", "total", "track_link"],
        "buttons": True,           # أزرار: تأكيد / إلغاء
        "label": "تأكيد الطلبية (بعد ما الزبون يكمّل)",
        "default_on": True,
    },
    "shipped": {
        "template": "budac_order_shipped",
        "params": ["name", "order_number", "carrier", "track_link"],
        "label": "الطلبية تبعثت (فالطريق)",
        "default_on": True,
    },
    "arrived": {
        "template": "budac_order_arrived",
        "params": ["name", "order_number", "where", "track_link"],
        "label": "وصلت لمكتب الاستلام",
        "default_on": True,
    },
    "thanks": {
        "template": "budac_order_delivered",
        "params": ["name", "order_number"],
        "label": "شكر بعد التسليم",
        "default_on": False,
    },
}

# حالة الطلبية ← نوع الرسالة التلقائية
STATUS_TO_KIND = {
    Order.STATUS_SHIPPED: "shipped",
    Order.STATUS_AT_OFFICE: "arrived",
    Order.STATUS_DELIVERED: "thanks",
}


# ───────────────────────── الإعدادات ─────────────────────────

def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def is_configured():
    """واش الـAPI مضبوط؟ (التوكن + معرّف رقم الهاتف)"""
    return bool(_env("WA_CLOUD_TOKEN") and _env("WA_PHONE_NUMBER_ID"))


def site_url():
    return _env("SITE_URL", "https://budacdz.com").rstrip("/")


def template_name(kind):
    return _env(f"WA_TPL_{kind.upper()}", KINDS[kind]["template"])


def kind_enabled(kind):
    """واش هاد النوع مفعّل؟ (الأدمن يبدّلو من صفحة «واتساب التلقائي»)"""
    if kind not in KINDS:
        return False
    if StoreSetting.get("wa_auto_master", "1") != "1":
        return False
    default = "1" if KINDS[kind]["default_on"] else "0"
    return StoreSetting.get(f"wa_auto_{kind}", default) == "1"


def set_kind_enabled(kind, enabled):
    StoreSetting.set(f"wa_auto_{kind}", "1" if enabled else "0")


# ───────────────────────── الطابور ─────────────────────────

def enqueue(order, kind):
    """
    يسجّل رسالة تلقائية لطلبية. يرجّع الصف، أو None (مطفية/مكرّرة/رقم غالط).
    ما يعملش commit — المستدعي يحفظ مع الطلبية.
    """
    if not is_configured() or not kind_enabled(kind):
        return None
    if not normalize_algerian_phone(order.customer_phone):
        return None            # رقم غير صالح للواتساب — ما نسجّلوش

    row = WhatsAppOutbox(order_id=order.id, kind=kind, status="pending")
    try:
        # savepoint: التكرار ما يفسدش transaction الطلبية
        with db.session.begin_nested():
            db.session.add(row)
    except IntegrityError:
        return None            # نفس الرسالة تسجّلت من قبل
    _wake_worker()
    return row


def enqueue_for_status(order, new_status):
    kind = STATUS_TO_KIND.get(new_status)
    if kind:
        return enqueue(order, kind)
    return None


def _wake_worker():
    try:
        from services import jobs
        jobs.kick()
    except Exception:   # noqa: BLE001
        pass


# ───────────────────────── الإرسال ─────────────────────────

def _context(order):
    """قيم متغيّرات القالب (نصوص قصيرة، بلا أسطر جديدة — شرط Meta)."""
    return {
        "name": (order.customer_name or "خويا").strip()[:60],
        "order_number": order.order_number,
        "total": order.total_display,
        "carrier": order.carrier_name or "شركة التوصيل",
        "where": order.pickup_point_name or order.wilaya_name or "-",
        "track_link": order.track_url(site_url()),
    }


def build_payload(order, kind):
    """جسم طلب Cloud API لقالب معيّن."""
    spec = KINDS[kind]
    ctx = _context(order)
    phone = normalize_algerian_phone(order.customer_phone)
    components = [{
        "type": "body",
        "parameters": [{"type": "text", "text": str(ctx[k]) or "-"} for k in spec["params"]],
    }]
    if spec.get("buttons"):
        for index, action in enumerate(("CONFIRM", "CANCEL")):
            components.append({
                "type": "button", "sub_type": "quick_reply", "index": str(index),
                "parameters": [{"type": "payload", "payload": f"{action}:{order.order_number}"}],
            })
    return {
        "messaging_product": "whatsapp",
        "to": "213" + phone[1:],
        "type": "template",
        "template": {
            "name": template_name(kind),
            "language": {"code": _env("WA_TEMPLATE_LANG", "ar")},
            "components": components,
        },
    }


def _graph_url():
    version = _env("WA_GRAPH_VERSION", "v21.0")
    return f"https://graph.facebook.com/{version}/{_env('WA_PHONE_NUMBER_ID')}/messages"


def send_message(order, kind):
    """يبعث رسالة وحدة. يرجّع wamid. يرفع RuntimeError برسالة واضحة عند الفشل."""
    payload = build_payload(order, kind)
    try:
        resp = requests.post(
            _graph_url(), json=payload, timeout=HTTP_TIMEOUT,
            headers={"Authorization": f"Bearer {_env('WA_CLOUD_TOKEN')}"},
        )
    except requests.RequestException as e:
        raise RuntimeError(f"اتصال: {type(e).__name__}") from e

    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400:
        err = (data.get("error") or {})
        raise RuntimeError(
            f"Meta {resp.status_code}: {err.get('message', resp.text[:120])}"
            f" (code {err.get('code', '?')})")
    messages = data.get("messages") or [{}]
    return messages[0].get("id")


def process_outbox(limit=20):
    """
    يبعث الرسائل المعلّقة. يرجّع (sent, failed).

    ⚠️ claim ذرّي: UPDATE ... WHERE status='pending' — إذا جوج عمّال (gunicorn
    workers) شافو نفس الصف، واحد برك يربح (rowcount=1) والآخر يتخطّاه.
    """
    if not is_configured():
        return 0, 0

    rows = (WhatsAppOutbox.query.filter_by(status="pending")
            .order_by(WhatsAppOutbox.id).limit(limit).all())
    sent = failed = 0
    for row in rows:
        claimed = (WhatsAppOutbox.query
                   .filter_by(id=row.id, status="pending")
                   .update({"status": "sending"}, synchronize_session=False))
        db.session.commit()
        if not claimed:
            continue
        db.session.refresh(row)

        order = db.session.get(Order, row.order_id)
        try:
            if order is None:
                raise RuntimeError("الطلبية انمسحت")
            wamid = send_message(order, row.kind)
            row.status, row.provider_id, row.sent_at, row.error = "sent", wamid, utcnow(), None
            sent += 1
        except Exception as e:   # noqa: BLE001 — رسالة وحدة ما توقفش الباقي
            row.attempts += 1
            row.error = str(e)[:300]
            row.status = "failed" if row.attempts >= MAX_ATTEMPTS else "pending"
            failed += 1
            logger.warning("واتساب %s/%s فشل (%s/%s): %s",
                           row.order_id, row.kind, row.attempts, MAX_ATTEMPTS, e)
        db.session.commit()
    return sent, failed


def retry(row):
    """الأدمن يعاود الإرسال لرسالة فاشلة."""
    row.status, row.attempts, row.error = "pending", 0, None
    _wake_worker()


# ───────────────────────── Webhook (ردود الزبون) ─────────────────────────

def verify_signature(raw_body, header):
    """
    يتحقق أن الطلب جاي من Meta فعلاً (X-Hub-Signature-256).
    ⚠️ بلا WA_APP_SECRET نرفض كلش: webhook مفتوح = أي واحد يأكّد/يلغي طلبيات.
    """
    secret = _env("WA_APP_SECRET")
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def handle_webhook(data):
    """
    يعالج إشعار Meta. يرجّع عدد الإجراءات.
      • زر «تأكيد» ← الطلبية الجديدة تولّي «مؤكدة».
      • زر «إلغاء» ← ما نلغيوش وحدنا (مخزون/تكاليف): نكتبو ملاحظة للأدمن.
      • حالة «failed» لرسالة مبعوثة ← نسجّلو السبب فالطابور.
    كل رد نتحققو أن رقم المرسل = رقم الزبون فالطلبية.
    """
    from services.order_status import change_order_status

    actions = 0
    for entry in data.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}

            for st in value.get("statuses", []) or []:
                if st.get("status") == "failed":
                    row = WhatsAppOutbox.query.filter_by(provider_id=st.get("id")).first()
                    if row:
                        errs = st.get("errors") or [{}]
                        row.status = "failed"
                        row.error = (errs[0].get("title") or "failed")[:300]
                        actions += 1

            for msg in value.get("messages", []) or []:
                payload = ""
                if msg.get("type") == "button":
                    payload = (msg.get("button") or {}).get("payload", "")
                elif msg.get("type") == "interactive":
                    payload = ((msg.get("interactive") or {}).get("button_reply") or {}).get("id", "")
                action, _, number = payload.partition(":")
                if action not in ("CONFIRM", "CANCEL") or not number:
                    continue

                order = Order.query.filter_by(order_number=number.strip().upper()).first()
                sender = normalize_algerian_phone(msg.get("from"))
                if order is None or sender != normalize_algerian_phone(order.customer_phone):
                    continue

                if action == "CONFIRM" and order.status == Order.STATUS_PENDING:
                    change_order_status(order, Order.STATUS_CONFIRMED,
                                        note=" (الزبون أكّد عبر واتساب)")
                    actions += 1
                elif action == "CANCEL" and order.status in (
                        Order.STATUS_PENDING, Order.STATUS_CONFIRMED):
                    stamp = utcnow().strftime("%Y-%m-%d %H:%M")
                    note = f"⚠️ [{stamp}] الزبون طلب الإلغاء عبر واتساب"
                    if note not in (order.admin_note or ""):
                        order.admin_note = ((order.admin_note or "") + "\n" + note).strip()
                        actions += 1
    db.session.commit()
    return actions
