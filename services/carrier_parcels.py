"""
كل طرود شركات التوصيل (ZR · World Express · Yalidine) — حتى اللي ما جاوش من المتجر.

الفكرة: نسقسيو كل شركة على قائمة طرودها (من حسابك عندها) ونوحّدو الشكل،
وبعدها نعلّمو الطرد «من المتجر» إذا رقم تتبّعو موجود فـ Order.tracking_number.

⚠️ ملاحظات مهمة:
  • القراءة برك — هاد الوحدة ما تبدّل لا حالة طلبية ولا تلمس المخزون ولا ترسل واتساب.
  • كل شركة مستقلة: لو وحدة فشلت (مفتاح غالط، API طايح) الباقي يبقى يخدم.
  • نحفظو النتيجة 5 دقائق فالذاكرة (باش ما نغرقوش API الشركات: World 50/دقيقة، Yalidine 50/دقيقة).
  • ZR وWorld: أشكال الردود ما تتأكّدتش بتجربة حيّة، فاستخراج الحقول مرن (يجرّب عدة
    أسماء) — وكاين `raw_sample` للتشخيص إذا حقل ما بانش.
"""
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone

CARRIERS = {
    "zr": {"label": "ZR Express", "color": "#7c3aed"},
    "world": {"label": "World Express", "color": "#0284c7"},
    "yalidine": {"label": "Yalidine", "color": "#dc2626"},
}
CATEGORY_LABELS = {
    "transit": "فالطريق",
    "delivered": "مسلَّمة",
    "returned": "راجعة",
    "cancelled": "ملغاة",
    "other": "أخرى / قيد التحضير",
}
CACHE_TTL = 300
MAX_ROWS = 3000
MAX_PAGES = 15
PAGE_SIZE = 100

_cache = {}
_lock = threading.Lock()


class ParcelsError(Exception):
    """خطأ مفهوم نعرضوه للأدمن كما هو."""


# ──────────────────────────── أدوات استخراج ────────────────────────────
def _dig(data, path):
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _text(value):
    """نص من أي قيمة: كائن ← name/description، قائمة/رقم هاتف مركّب ← أول قيمة مقروءة."""
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("name", "description", "label", "number1", "number", "value"):
            if value.get(key):
                return _text(value[key])
        for v in value.values():
            t = _text(v)
            if t:
                return t
        return ""
    if isinstance(value, (list, tuple)):
        for v in value:
            t = _text(v)
            if t:
                return t
        return ""
    return str(value).strip()


def _pick(row, *paths):
    for path in paths:
        text = _text(_dig(row, path))
        if text:
            return text
    return ""


def _amount(row, *paths):
    for path in paths:
        raw = _dig(row, path)
        if raw in (None, ""):
            continue
        try:
            return float(str(_text(raw)).replace(",", ".").replace(" ", ""))
        except ValueError:
            continue
    return None


def _parse_ts(value):
    """تاريخ نصي ← (نص مقروء، طابع للترتيب). فشل = ('', 0)."""
    text = _text(value)
    if not text:
        return "", 0.0
    iso = text.replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            return text[:16], 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M"), dt.timestamp()


def _plain(text):
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = text.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


# ──────────────────────────── تصنيف الحالة ────────────────────────────
def _mapper(carrier):
    if carrier == "zr":
        from services import zrexpress as svc
    elif carrier == "world":
        from services import worldexpress as svc
    else:
        from services import yalidine as svc
    return svc.map_state_to_order_status


def categorize(carrier, state):
    """حالة الشركة ← فئة موحّدة. نستعملو نفس مترجمات المزامنة (الأمان نفسو)."""
    plain = _plain(state)
    if "annul" in plain or "cancel" in plain:
        return "cancelled"
    try:
        mapped = _mapper(carrier)(state)
    except Exception:   # noqa: BLE001 — تصنيف فاشل ما يوقّفش القائمة
        mapped = None
    return {"delivered": "delivered", "returned": "returned", "shipped": "transit"}.get(mapped, "other")


# ──────────────────────────── توحيد الأسطر ────────────────────────────
def _finish(carrier, row, **fields):
    date_txt, ts = _parse_ts(fields.pop("created"))
    state = fields.pop("state") or "—"
    return {
        "carrier": carrier,
        "state": state,
        "category": categorize(carrier, state),
        "date": date_txt,
        "ts": ts,
        **fields,
    }


def normalize_yalidine(r):
    name = " ".join(x for x in (_pick(r, "firstname"), _pick(r, "familyname")) if x)
    return _finish(
        "yalidine", r,
        tracking=_pick(r, "tracking"),
        ref=_pick(r, "order_id"),
        name=name or _pick(r, "customer_name"),
        phone=_pick(r, "contact_phone", "contact_phone_2"),
        wilaya=_pick(r, "to_wilaya_name"),
        commune=_pick(r, "to_commune_name"),
        amount=_amount(r, "price"),
        state=_pick(r, "last_status"),
        created=_pick(r, "date_creation"),
    )


def normalize_world(r):
    return _finish(
        "world", r,
        tracking=_pick(r, "tracking", "tracking_number"),
        ref=_pick(r, "reference", "order_id", "ref"),
        name=_pick(r, "client", "nom_client", "customer", "name"),
        phone=_pick(r, "phone", "telephone", "phone_1", "tel"),
        wilaya=_pick(r, "wilaya_name", "wilaya", "wilaya_id"),
        commune=_pick(r, "commune", "commune_name"),
        amount=_amount(r, "montant", "amount", "price", "total"),
        state=_pick(r, "status", "state"),
        created=_pick(r, "created_at", "date_creation", "date", "created"),
    )


def normalize_zr(r):
    return _finish(
        "zr", r,
        tracking=_pick(r, "trackingNumber", "tracking", "trackingCode", "code"),
        ref=_pick(r, "externalId", "externalReference", "reference", "orderNumber", "orderId"),
        name=_pick(r, "customer.name", "customerName", "recipient.name", "receiver.name"),
        phone=_pick(r, "customer.phone", "customerPhone", "recipient.phone", "receiver.phone"),
        wilaya=_pick(r, "deliveryAddress.city", "deliveryAddress.cityName", "cityName", "city"),
        commune=_pick(r, "deliveryAddress.district", "deliveryAddress.districtName", "districtName", "district"),
        amount=_amount(r, "amount", "totalAmount", "codAmount", "price"),
        state=_pick(r, "state.description", "state.name", "stateName", "state", "status"),
        created=_pick(r, "createdAt", "created_at", "date", "creationDate"),
    )


_NORMALIZERS = {"zr": normalize_zr, "world": normalize_world, "yalidine": normalize_yalidine}


# ──────────────────────────── الجلب من كل شركة ────────────────────────────
def _row_key(row):
    return _pick(row, "trackingNumber", "tracking", "tracking_number", "id") or repr(sorted(row.items()))[:200]


def _fetch_yalidine():
    from services import yalidine as svc
    if not svc.is_configured():
        raise ParcelsError("Yalidine ما هيش مربوطة (YALIDINE_API_ID / YALIDINE_API_TOKEN).")
    return svc._get_all("/parcels/"), None


def _world_rows(data):
    rows = data.get("data") if isinstance(data, dict) else data
    if isinstance(rows, dict):          # شكل Laravel: {"data": {"data": [...]}}
        rows = rows.get("data")
    return [r for r in (rows or []) if isinstance(r, dict)]


def _fetch_world():
    from services import worldexpress as svc
    if not svc.is_configured():
        raise ParcelsError("World Express ما هيش مربوطة (WORLD_API_TOKEN).")
    items, seen, total = [], set(), None
    for page in range(1, MAX_PAGES + 1):
        data = svc._request("GET", "/api/v1/get/orders", params={"page": page})
        rows = _world_rows(data)
        fresh = [r for r in rows if _row_key(r) not in seen]
        if not fresh:                    # فراغ ولا نفس الصفحة تتعاود = كملنا
            break
        for r in fresh:
            seen.add(_row_key(r))
            items.append(r)
        if isinstance(data, dict):
            total = data.get("total") or (data.get("meta") or {}).get("total") or total
            last = data.get("last_page") or (data.get("meta") or {}).get("last_page")
            if last and page >= int(last):
                break
        if len(items) >= MAX_ROWS:
            break
    return items, total


def _fetch_zr():
    from services import zrexpress as svc
    if not svc.is_configured():
        raise ParcelsError("ZR Express ما هيش مربوطة (ZR_TENANT_ID / ZR_API_KEY).")
    best, last_error, total = [], None, None
    # نفس درس hubs/territories: ZR تتجاهل أسماء الترقيم الغالطة بصمت، فنجرّبو عدة أنماط
    for _style, build in svc._PAGINATION_STYLES:
        items, seen, first_len = [], set(), None
        for page in range(1, MAX_PAGES + 1):
            try:
                data = svc._request("POST", "/parcels/search", json=build(page, PAGE_SIZE))
            except svc.ZRError as e:
                last_error = e
                break
            batch = [r for r in svc._extract_items(data) if isinstance(r, dict)]
            first_len = len(batch) if first_len is None else first_len
            if isinstance(data, dict):
                total = data.get("totalCount") or data.get("total") or total
            fresh = [r for r in batch if _row_key(r) not in seen]
            if not fresh:
                break
            for r in fresh:
                seen.add(_row_key(r))
                items.append(r)
            if len(items) >= MAX_ROWS:
                break
        if len(items) > len(best):
            best = items
        if first_len and len(items) > first_len:   # الترقيم خدم (أكثر من صفحة)
            break
        if first_len is not None and len(items) >= (total or 0) > 0:
            break
    if not best and last_error:
        raise ParcelsError(str(last_error))
    return best, total


_FETCHERS = {"zr": _fetch_zr, "world": _fetch_world, "yalidine": _fetch_yalidine}


# ──────────────────────────── الواجهة العامة ────────────────────────────
def get(carrier, force=False):
    """
    يرجّع {ok, error, rows, fetched_at, total, truncated, stale, raw_sample}.
    الأسطر مرتّبة من الأحدث للأقدم. فشل = ok False + رسالة (ونسخة قديمة إن كانت).
    """
    if carrier not in CARRIERS:
        return {"ok": False, "error": "شركة غير معروفة", "rows": []}

    with _lock:
        hit = _cache.get(carrier)
    if hit and not force and time.time() - hit["at"] < CACHE_TTL:
        return hit["payload"]

    try:
        raw, total = _FETCHERS[carrier]()
        normalize = _NORMALIZERS[carrier]
        rows = []
        for r in raw[:MAX_ROWS]:
            try:
                row = normalize(r)
            except Exception:   # noqa: BLE001 — سطر مكسور ما يوقّفش الباقي
                continue
            if row["tracking"]:
                rows.append(row)
        rows.sort(key=lambda x: x["ts"], reverse=True)
        payload = {
            "ok": True, "error": "", "rows": rows,
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "total": total, "truncated": len(raw) >= MAX_ROWS,
            "stale": False, "raw_sample": raw[0] if raw else None,
        }
        with _lock:
            _cache[carrier] = {"at": time.time(), "payload": payload}
        return payload
    except Exception as e:   # noqa: BLE001 — الشركات ترمي أنواع أخطاء مختلفة (ZRError, WorldError…)
        message = str(e) or e.__class__.__name__
        if hit:   # نسخة قديمة أحسن من والو
            return {**hit["payload"], "stale": True,
                    "error": f"تعذّر التحديث ({message}) — هاد المعطيات قديمة."}
        return {"ok": False, "error": message, "rows": [], "stale": False}


def clear_cache():
    with _lock:
        _cache.clear()


def annotate_store_orders(rows):
    """نعلّمو كل طرد «من المتجر» (ونحطو رقم الطلبية) إذا رقم تتبّعو عندنا. نسخة جديدة للأسطر."""
    from models import Order

    trackings = list({r["tracking"] for r in rows if r.get("tracking")})
    found = {}
    for i in range(0, len(trackings), 400):   # دفعات صغيرة (حد باراميترات SQL)
        chunk = trackings[i:i + 400]
        for o in Order.query.filter(Order.tracking_number.in_(chunk)).all():
            found[str(o.tracking_number).strip()] = (o.id, o.order_number)

    out = []
    for r in rows:
        hit = found.get(str(r.get("tracking", "")).strip())
        out.append({**r, "store_order_id": hit[0] if hit else None,
                    "store_order_number": hit[1] if hit else None})
    return out
