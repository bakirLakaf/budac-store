"""
مزامنة حالة الطلبيات تلقائيًا مع شركات التوصيل (ZR · World Express · Yalidine).

⚠️ نفس القواعد تاع زر «تحديث التتبّع» اليدوي بالضبط (services/order_status.py):
  • ما نرجعوش حالة للوراء، ولا نلمسو طلبية مسلّمة/ملغاة/راجعة.
  • حالة شركة ما نعرفوهاش ← نحفظوها فـcarrier_state ونتركو حالة الطلبية.
  • تبديل الحالة يمرّ من change_order_status ← المخزون + السجل + واتساب تلقائيًا.

التكرار: الأقدم تحديثًا أولاً، وما نعاودوش طلبية سألنا عليها قبل MIN_GAP.
"""

import logging
from datetime import timedelta

from models import db, Order, utcnow
from services.order_status import (
    change_order_status, decide_status_change, normalize_carrier_state)
from services.orders import OrderError

logger = logging.getLogger(__name__)

MIN_GAP = timedelta(minutes=10)     # ما نسقسيوش الشركة على نفس الطرد أكثر من هذا
MAX_PER_RUN = 30                    # سقف الطلبيات فالدورة الوحدة (باش ما نغرقوش API الشركة)


def _fetcher(order):
    """(دالة جلب الحالة، اسم الشركة، دالة is_configured) حسب شركة الطلبية."""
    if order.carrier_code == "worldexpress":
        from services import worldexpress as svc
        return svc.get_order_status, "World Express", svc.is_configured
    if order.carrier_code == "yalidine":
        from services import yalidine as svc
        return svc.get_order_status, "Yalidine", svc.is_configured
    from services import zrexpress as svc
    return svc.get_parcel_status, "ZR", svc.is_configured


def sync_order(order):
    """
    يحدّث طلبية وحدة. يرجّع: "changed" | "same" | "unknown" | "skipped".
    يرفع استثناء إذا فشل الاتصال (المستدعي يمسكو).
    ما يعملش commit — المستدعي يحفظ.
    """
    fetch, label, configured = _fetcher(order)
    if not configured():
        return "skipped"

    state = normalize_carrier_state(fetch(order.tracking_number))
    order.last_synced_at = utcnow()
    if not state:
        return "unknown"

    kind, target = decide_status_change(order, state)
    order.carrier_state = state
    if kind == "change":
        change_order_status(order, target, note=f" (تلقائي من {label}: {state})")
        return "changed"
    return "unknown" if kind == "unknown" else "same"


def sync_active(limit=MAX_PER_RUN):
    """يمرّ على الطلبيات النشطة اللي عندها رقم تتبّع. يرجّع قاموس إحصائيات."""
    cutoff = utcnow() - MIN_GAP
    candidates = (
        Order.query
        .filter(
            Order.tracking_number.isnot(None),
            Order.tracking_number != "",
            Order.status.notin_([Order.STATUS_DELIVERED, *Order.STATUS_TERMINAL]),
            db.or_(Order.last_synced_at.is_(None), Order.last_synced_at < cutoff),
        )
        # الأقدم تحديثًا أولاً (NULL = ما تحدّثتش أبدًا ← الأولوية)
        .order_by(Order.last_synced_at.isnot(None), Order.last_synced_at.asc(),
                  Order.created_at.asc())
        .limit(limit)
        .all()
    )

    stats = {"checked": 0, "changed": 0, "failed": 0, "skipped": 0}
    for order in candidates:
        try:
            result = sync_order(order)
            db.session.commit()
            if result == "skipped":
                stats["skipped"] += 1
            else:
                stats["checked"] += 1
                stats["changed"] += result == "changed"
        except OrderError as e:
            db.session.rollback()
            stats["failed"] += 1
            logger.warning("مزامنة %s: تعذّر تبديل الحالة: %s", order.order_number, e)
        except Exception as e:   # noqa: BLE001 — طلبية وحدة ما توقفش الباقي
            db.session.rollback()
            stats["failed"] += 1
            logger.warning("مزامنة %s فشلت: %s", order.order_number, e)
            # نسجّلو المحاولة باش ما نعاودوهاش كل دورة ونحرقو سقف API الشركة
            try:
                db.session.query(Order).filter_by(id=order.id).update(
                    {"last_synced_at": utcnow()})
                db.session.commit()
            except Exception:   # noqa: BLE001
                db.session.rollback()
    return stats
