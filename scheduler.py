"""المجدول (APScheduler). آمن مع بزاف workers: التحميل يتم عبر قفل في قاعدة البيانات."""
import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

from . import service
from .config import cfg

log = logging.getLogger("social_publisher")
_scheduler = None


def _tick():
    try:
        service.run_due()
    except Exception:
        log.exception("خطأ في tick")


def _clean():
    try:
        service.janitor()
    except Exception:
        log.exception("خطأ في التنظيف")


def start():
    global _scheduler
    # مع flask run --debug المحرّك يتشغّل مرتين: نبداو غير في العملية الأصلية
    if os.environ.get("WERKZEUG_RUN_MAIN") == "false" or _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC", daemon=True)
    _scheduler.add_job(_tick, "interval", seconds=cfg.tick_seconds, id="sp_tick",
                       max_instances=1, coalesce=True)
    _scheduler.add_job(_clean, "interval", minutes=30, id="sp_clean", max_instances=1, coalesce=True)
    _scheduler.start()


def stop():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
