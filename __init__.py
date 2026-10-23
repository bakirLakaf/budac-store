"""أداة النشر على فيسبوك + انستغرام + تيك توك — Blueprint جاهز يتركّب في متجر Flask.

الاستعمال في المتجر:
    from social_publisher import init_social_publisher
    init_social_publisher(app, admin_required=login_admin_required)
"""
import logging

from . import db, scheduler
from .adapter import BaseAdapter, BudacAdapter
from .config import cfg
from .routes import create_blueprint

__all__ = ["init_social_publisher", "BaseAdapter", "BudacAdapter"]
log = logging.getLogger("social_publisher")


def init_social_publisher(app, admin_required, adapter=None, start_scheduler=True, limiter=None):
    """admin_required: الـ decorator تاع المتجر اللي يحمي لوحة التحكم (لازم)."""
    if admin_required is None:
        raise ValueError("admin_required لازم — ما نقبلوش لوحة نشر بلا حماية")
    cfg.load(app)
    if not cfg.database_url:
        raise RuntimeError("DATABASE_URL ناقص")
    if len(cfg.secret) < 16:
        raise RuntimeError("SP_SECRET ناقص أو قصير (16 حرف على الأقل) — ولا حط app.secret_key")
    db.init_pool(cfg.database_url)
    db.init_schema()
    bp = create_blueprint(admin_required, adapter or BudacAdapter())
    app.register_blueprint(bp)
    if limiter is not None:  # فيسبوك/انستغرام يجبدو الملفات بطلبات متتالية: ما نخلّوهمش يتحدّو بـ rate limit
        limiter.exempt(bp)
    if start_scheduler:
        scheduler.start()
    log.info("social_publisher جاهز على %s", cfg.admin_prefix)
