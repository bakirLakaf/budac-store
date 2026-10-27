"""
سجل تدقيق أفعال الأدمن.

⚠️ قاعدتان تحكمو هاد الملف:

1) **التسجيل ما يكسرش العملية أبدًا.** لو فشلت الكتابة فالسجل،
   حذف المنتج (ولا أي عملية) لازم يكمل عادي. سجل تدقيق معطّل
   مشكل، لكن لوحة تحكم واقفة مشكل أكبر.

2) **ما نسجّلوش أسرارًا.** كلمات السر، الكوكيز، توكنات API،
   وبيانات الزبون الكاملة ما تدخلش هنا. `details` سياق قصير برك:
   «حذف 12 طلبية»، ماشي أسماء الزبائن وهواتفهم.
"""

import logging

from flask import request, session, current_app

from models import db, AdminAction

logger = logging.getLogger(__name__)

MAX_DETAILS = 300


def record(action, resource=None, resource_id=None, details=None, success=True):
    """
    يسجّل فعلاً حساسًا.

    الاستعمال:
        audit.record(AdminAction.DELETE, "product", product.id, product.name)

    ⚠️ ما نديروش commit هنا كي نكون وسط عملية أخرى — نضيفو للجلسة
    والمستدعي يعمل commit مع باقي تبديلاتو، باش السجل والعملية
    يتمو مع بعض ولا ما يتمو (نفس الـtransaction).
    ماعدا الدخول/الخروج: ماكانش عملية أخرى معاهم، فنعملو commit.
    """
    try:
        entry = AdminAction(
            admin_id=session.get("admin_id"),
            admin_username=session.get("admin_username"),
            action=action,
            resource=resource,
            resource_id=str(resource_id)[:60] if resource_id is not None else None,
            details=(str(details)[:MAX_DETAILS] if details else None),
            success=bool(success),
            # ⚠️ remote_addr ماشي X-Forwarded-For الخام — ProxyFix
            # خلاص حسب العنوان الصحيح، والهيدر الخام يتزوّر.
            ip=(request.remote_addr or "")[:60] if request else None,
        )
        db.session.add(entry)
        return entry
    except Exception:
        # القاعدة 1: ما نكسروش العملية
        try:
            current_app.logger.exception("⚠️ فشل تسجيل فعل الأدمن: %s", action)
        except Exception:
            logger.exception("فشل تسجيل فعل الأدمن: %s", action)
        return None


def record_and_commit(action, **kwargs):
    """
    نفس `record` لكن يحفظ فورًا — للعمليات اللي ماكانش معاها
    transaction أخرى (الدخول، الخروج، محاولة فاشلة).
    """
    entry = record(action, **kwargs)
    if entry is None:
        return None

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            current_app.logger.exception("⚠️ فشل حفظ سجل التدقيق: %s", action)
        except Exception:
            pass
        return None

    return entry
