"""
منطق تغيير حالة الطلبية — مصدر واحد يستعملو:
  • الأدمن (تبديل يدوي + زر «تحديث التتبّع»)
  • المزامنة التلقائية مع شركات التوصيل (services/tracking_sync.py)
  • رسائل الواتساب التلقائية (الربط فـ`_change_order_status`)

⚠️ كان هذا الكود داخل routes/admin.py. نقلناه هنا باش المزامنة الخلفية
(بلا طلب HTTP ولا جلسة أدمن) تستعمل **نفس** القواعد بالضبط: بلا نسخة ثانية
تختلف مع الوقت. routes/admin.py يستورده بنفس الأسماء القديمة.
"""

from models import Order, AdminAction, db
from services import audit
from services.cart import CartLine
from services.orders import restore_stock, reserve_stock, OrderError


def change_order_status(order, new_status, note=""):
    """
    يبدّل حالة طلبية ويضبط المخزون — منطق واحد يستعملو التبديل اليدوي
    وتحديث ZR التلقائي (باش ما يتكرّرش الكود).

    ⚠️ منطق مهم: كي نلغيو (ولا تولّي راجعة) نرجّعو المخزون تلقائيًا.
    وكي نرجعوها لحالة نشطة، نخصمو المخزون من جديد.

    يرجع قائمة رسائل [(نص, نوع)]. يرفع OrderError إذا ما قدرش يخصم المخزون
    (وقتها يعمل rollback ويبقى كل شي كما كان).
    """
    messages = []
    old_status = order.status

    was_off = old_status in Order.STATUS_TERMINAL
    now_off = new_status in Order.STATUS_TERMINAL

    if now_off and not was_off:
        restore_stock(order)
        messages.append(("تم إرجاع المخزون للمنتجات", "success"))

    elif was_off and not now_off:
        lines = [
            CartLine(
                key="", kind="offer" if item.offer_id else "product",
                name=item.item_name, unit_price=item.unit_price,
                quantity=item.quantity, product_id=item.product_id,
                variant_id=item.variant_id, offer_id=item.offer_id,
            )
            for item in order.items
        ]
        try:
            reserve_stock(lines)
        except OrderError:
            db.session.rollback()
            raise
        messages.append(("تم خصم المخزون من جديد", "success"))

    order.status = new_status
    # رسالة واتساب تلقائية للزبون (إذا مفعّلة) — تتسجّل فالصف نفسو، والإرسال
    # الحقيقي يتم فالخلفية بعد الـcommit، فما تبطّأش ولا تفشل الطلب.
    try:
        from services import whatsapp_api
        whatsapp_api.enqueue_for_status(order, new_status)
    except Exception:   # noqa: BLE001 — الواتساب ما يكسرش تبديل الحالة أبدًا
        pass
    audit.record(
        AdminAction.ORDER_STATUS, "order", order.order_number,
        f"{old_status} ← {new_status}{note}",
    )
    return messages


def normalize_carrier_state(status):
    """يستخرج نص حالة ZR من الرد (مهما كان شكلو). يرجع None إذا ما لقاش."""
    state = status.get("state") if isinstance(status, dict) else None
    if isinstance(state, dict):
        state = state.get("description") or state.get("name") or None
    if state is not None:
        state = str(state)[:120]
    return state or None


def decide_status_change(order, new_state):
    """
    يقرّر واش نبدّلو حالة الطلبية بناءً على حالة ZR — قواعد الأمان فبلاصة وحدة
    يستعملوها الزر الفردي والزر الجماعي.

    يرجع (kind, target):
      "change"  → نبدّلو لـtarget
      "same"    → نفس الحالة، ما كاين والو
      "back"    → التبديل يكون رجوع للوراء، نتجاهلوه
      "locked"  → الطلبية مسلّمة/ملغاة/راجعة، ما نلمسوهاش
      "unknown" → حالة ZR ما تتعرفش
    """
    if order.carrier_code == "worldexpress":
        from services.worldexpress import map_state_to_order_status
    elif order.carrier_code == "yalidine":
        from services.yalidine import map_state_to_order_status
    else:
        from services.zrexpress import map_state_to_order_status

    target = map_state_to_order_status(new_state, order.delivery_type)
    if target is None:
        return "unknown", None
    if target == order.status:
        return "same", target
    if order.status in (Order.STATUS_DELIVERED, *Order.STATUS_TERMINAL):
        return "locked", target

    flow = Order.STATUS_FLOW
    moving_forward = (
        target in Order.STATUS_TERMINAL
        or (order.status in flow and target in flow
            and flow.index(target) > flow.index(order.status))
    )
    return ("change", target) if moving_forward else ("back", target)
