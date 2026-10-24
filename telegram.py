# -*- coding: utf-8 -*-
"""
إشعارات Telegram — يبعث للقناة عند كل طلبية جديدة أو تقييم جديد.

═══════════ الإعداد ═══════════

1. أنشئ بوت من @BotFather وخذ التوكن.
2. أنشئ قناة (ولا مجموعة) وأضف البوت كمشرف.
3. احصل على chat_id:
   - قناة عامة: اسم القناة مثلاً @budac_orders
   - قناة خاصة ولا مجموعة: ابعث رسالة ثم افتح:
     https://api.telegram.org/bot<TOKEN>/getUpdates
     وشوف "chat": {"id": -100...}

4. حط فمتغيرات Railway:
   TELEGRAM_BOT_TOKEN = 7123456789:ABCdef...
   TELEGRAM_CHAT_ID   = @budac_orders   ولا   -1001234567890

═══════════ الاستعمال ═══════════

    from services.telegram import notify_new_order, notify_new_review

    order = create_order(...)
    notify_new_order(order)

    review = Review(...)
    notify_new_review(review)

⚠️ الدوال لا ترميو exceptions — فشل الإشعار ما يوقفش الطلبية.
    الخطأ يتسجّل فالـlogs برك.
"""

import logging
import os
import threading

import requests

logger = logging.getLogger(__name__)

_BOT_TOKEN = None
_CHAT_ID = None
_CONFIGURED = False


def _load_config():
    """نقرا الإعدادات من متغيرات البيئة مرة وحدة."""
    global _BOT_TOKEN, _CHAT_ID, _CONFIGURED
    _BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    _CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    _CONFIGURED = bool(_BOT_TOKEN and _CHAT_ID)


_load_config()


def is_configured():
    """واش الإشعارات معمّرة؟"""
    return _CONFIGURED


def _send(text: str):
    """
    يبعث رسالة للقناة بشكل غير متزامن (thread).

    ⚠️ غير متزامن قصدًا: الطلبية ما لازمش تستنّى Telegram باش تتأكد.
    لو السيرفر بطيء ولا قطع الاتصال، الزبون يقعد ينتظر.

    ⚠️ ما ترميش exceptions — فشل الإشعار ما يخسّرش طلبية.
    """
    if not _CONFIGURED:
        return

    def _do():
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": _CHAT_ID,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if not resp.ok:
                logger.warning("Telegram: %s — %s", resp.status_code, resp.text[:200])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram send failed: %s", exc)

    threading.Thread(target=_do, daemon=True).start()


def notify_new_order(order):
    """
    إشعار طلبية جديدة.

    يبعث: الرقم · الزبون · الهاتف · الولاية · نوع التوصيل · المجموع.
    """
    if not _CONFIGURED:
        return

    delivery_icon = "🏢" if getattr(order, "delivery_type", "") == "office" else "🏠"
    items_summary = ""
    try:
        lines = [
            f"  • {item.item_name} × {item.quantity}"
            for item in (order.items or [])[:5]
        ]
        if lines:
            extra = len(order.items) - 5
            if extra > 0:
                lines.append(f"  … و{extra} أخرى")
            items_summary = "\n" + "\n".join(lines)
    except Exception:  # noqa: BLE001
        pass

    text = (
        f"🛒 <b>طلبية جديدة</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📋 الرقم: <code>{order.order_number}</code>\n"
        f"👤 الزبون: {order.customer_name}\n"
        f"📞 الهاتف: {order.customer_phone}\n"
        f"📍 الولاية: {order.wilaya_name}"
    )

    if getattr(order, "commune", None):
        text += f" — {order.commune}"

    text += (
        f"\n{delivery_icon} التوصيل: "
        + ("للمكتب" if getattr(order, "delivery_type", "") == "office" else "للمنزل")
    )

    if getattr(order, "pickup_point_name", None):
        text += f"\n🏢 المكتب: {order.pickup_point_name}"

    text += f"\n💰 المجموع: {order.total:,} دج"

    if items_summary:
        text += f"\n\n📦 المنتجات:{items_summary}"

    if getattr(order, "customer_note", None):
        text += f"\n\n💬 ملاحظة: {order.customer_note}"

    _send(text)


def notify_new_review(review, base_url: str = ""):
    """
    إشعار تقييم جديد.

    يبعث: اسم الزبون · النجوم · التعليق · رابط اللوحة.
    """
    if not _CONFIGURED:
        return

    stars = "⭐" * (getattr(review, "rating", 0) or 0)
    product_name = ""
    try:
        if review.product:
            product_name = f"\n🎁 المنتج: {review.product.name}"
    except Exception:  # noqa: BLE001
        pass

    admin_link = ""
    if base_url:
        admin_link = f"\n🔗 <a href='{base_url.rstrip('/')}/admin/reviews'>اللوحة</a>"

    text = (
        f"💬 <b>تقييم جديد</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 {getattr(review, 'author_name', None) or 'زبون'}\n"
        f"{stars} ({getattr(review, 'rating', '—')}/5)"
        f"{product_name}"
    )

    comment = getattr(review, "comment", None)
    if comment:
        # نقصّو التعليق الطويل
        short = comment[:300]
        if len(comment) > 300:
            short += "…"
        text += f"\n\n💬 {short}"

    text += admin_link
    _send(text)
