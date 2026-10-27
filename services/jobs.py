"""
مهام الخلفية: إرسال واتساب + مزامنة حالة الطلبيات مع شركات التوصيل.

كيفاش تخدم؟
  • خيط daemon واحد لكل عامل gunicorn، يفيق كل 30 ثانية (أو فورًا مع kick()).
  • كل دورة: نبعثو رسائل الواتساب المعلّقة. وكل SYNC_EVERY ثانية: نزامنو التتبّع.
  • ⚠️ جوج عمّال (workers) ما يزامنوش مع بعض: نحجزو الدور بتحديث ذرّي فجدول
    store_settings (compare-and-set) — اللي يربح يخدم، والآخر يتخطّى.
  • بديل/إضافة: نقطة /internal/jobs/run?key=JOBS_SECRET يناديها أي cron خارجي
    (مفيد إذا الاستضافة تنوّم التطبيق).

إيقاف: BACKGROUND_JOBS=0 (يتوقف تلقائيًا فالاختبارات).
"""

import logging
import os
import threading
import time

from models import db, StoreSetting

logger = logging.getLogger(__name__)

TICK = 30                                   # ثانية بين الدورات
SYNC_EVERY = int(os.environ.get("TRACKING_SYNC_MINUTES", "20")) * 60

_wake = threading.Event()
_started = False
_lock = threading.Lock()


def kick():
    """يفيّق الخيط فورًا (بعد تسجيل رسالة جديدة)."""
    _wake.set()


def _claim(key, every):
    """
    يحجز دور مهمة دورية بين عدّة عمّال. يرجّع True لعامل واحد برك.
    الطريقة: UPDATE ... WHERE value = <القديم> — ذرّي عند القاعدة.
    """
    now = int(time.time())
    row = StoreSetting.query.filter_by(key=key).first()
    if row is None:
        try:
            db.session.add(StoreSetting(key=key, value=str(now)))
            db.session.commit()
            return True
        except Exception:   # noqa: BLE001 — عامل آخر سبقنا
            db.session.rollback()
            return False
    try:
        last = int(row.value or 0)
    except ValueError:
        last = 0
    if now - last < every:
        return False
    won = (StoreSetting.query.filter_by(key=key, value=row.value)
           .update({"value": str(now)}, synchronize_session=False))
    db.session.commit()
    return bool(won)


def run_once(app, force_sync=False):
    """دورة وحدة. ترجّع قاموس نتائج (يستعملو endpoint الـcron والاختبارات)."""
    from services import whatsapp_api, tracking_sync

    result = {"whatsapp": (0, 0), "sync": None}
    # ⚠️ test_request_context: audit.record يقرا session — بلاها يرمي خطأ فالخلفية
    with app.app_context(), app.test_request_context("/"):
        try:
            result["whatsapp"] = whatsapp_api.process_outbox()
        except Exception:   # noqa: BLE001
            db.session.rollback()
            logger.exception("jobs: فشل إرسال الواتساب")
        try:
            if force_sync or _claim("jobs_sync_last", SYNC_EVERY):
                result["sync"] = tracking_sync.sync_active()
        except Exception:   # noqa: BLE001
            db.session.rollback()
            logger.exception("jobs: فشلت مزامنة التتبّع")
        finally:
            db.session.remove()
    return result


def start(app):
    """يشغّل الخيط الخلفي (مرة وحدة لكل عملية)."""
    global _started
    # ⚠️ PYTEST_CURRENT_TEST: create_app() يتنادى قبل ما الاختبار يحط TESTING=True،
    # فبلاها الخيط يبدا فالاختبارات ويتدخّل فقواعدها.
    if (app.testing or os.environ.get("PYTEST_CURRENT_TEST")
            or os.environ.get("BACKGROUND_JOBS", "1").lower() in ("0", "false", "no")):
        return False
    with _lock:
        if _started:
            return False
        _started = True

    def loop():
        time.sleep(15)                       # نخلّيو التطبيق يكمّل الإقلاع
        while True:
            try:
                run_once(app)
            except Exception:   # noqa: BLE001 — الخيط ما يموتش أبدًا
                logger.exception("jobs: خطأ غير متوقع")
            _wake.wait(TICK)
            _wake.clear()

    threading.Thread(target=loop, name="budac-jobs", daemon=True).start()
    logger.info("jobs: الخيط الخلفي بدا (مزامنة كل %s دقيقة)", SYNC_EVERY // 60)
    return True
