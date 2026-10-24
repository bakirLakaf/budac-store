"""إعدادات أداة النشر — كلشي يتقرا من متغيرات البيئة (.env)، ما كاين حتى مفتاح داخل الكود."""
import os
import re
from pathlib import Path


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def normalize_dsn(url):
    """SQLAlchemy تستعمل postgresql+psycopg:// بصح psycopg2 ما يفهمهاش — نشيلو الجزء +driver."""
    return re.sub(r"^(postgres|postgresql)\+\w+://", "postgresql://", url or "")


class Config:
    def __init__(self):
        self.loaded = False

    def load(self, app=None):
        e = os.getenv
        # قاعدة البيانات: نفس قاعدة المتجر (جداولنا كلها تبدا بـ sp_)
        self.database_url = normalize_dsn(e("SP_DATABASE_URL") or e("DATABASE_URL", ""))
        # الرابط العام تاع المتجر (لازم https) — فيسبوك وانستغرام يجبدو منو الصور والفيديو
        self.public_base_url = e("SP_PUBLIC_BASE_URL", "").rstrip("/")
        # سر لتوقيع روابط الميديا المؤقتة
        self.secret = e("SP_SECRET") or (app.secret_key if app is not None and app.secret_key else "")
        self.admin_prefix = "/" + e("SP_ADMIN_PREFIX", "/admin/social").strip("/")
        # الملفات المؤقتة: لو كاين UPLOAD_ROOT (Volume تاع Railway) نخدمو فيه باش ما تتمسحش مع كل نشر
        upload_root = (app.config.get("UPLOAD_ROOT") if app is not None else "") or e("UPLOAD_ROOT", "")
        default_tmp = os.path.join(upload_root, "sp_tmp") if upload_root else "sp_tmp"
        self.temp_dir = Path(e("SP_TEMP_DIR") or default_tmp).resolve()
        self.max_video_mb = _int("SP_MAX_VIDEO_MB", 100)
        self.max_image_mb = _int("SP_MAX_IMAGE_MB", 10)
        self.max_images = min(_int("SP_MAX_IMAGES", 10), 10)  # انستغرام carousel حد أقصى 10
        self.tz_offset_hours = _int("SP_TZ_OFFSET_HOURS", 1)  # الجزائر UTC+1
        self.keep_failed_days = _int("SP_KEEP_FAILED_DAYS", 3)
        self.tick_seconds = _int("SP_TICK_SECONDS", 30)
        self.shop_product_url = e("SHOP_PRODUCT_URL_TEMPLATE", "https://budacdz.com/product/{slug}")

        # Meta (فيسبوك + انستغرام)
        self.graph_version = e("META_GRAPH_VERSION", "v26.0")
        self.fb_page_id = e("FB_PAGE_ID", "")
        self.fb_page_token = e("FB_PAGE_TOKEN", "")
        self.ig_user_id = e("IG_USER_ID", "")
        self.ig_token = e("IG_ACCESS_TOKEN", "") or self.fb_page_token

        # TikTok
        self.tt_client_key = e("TIKTOK_CLIENT_KEY", "")
        self.tt_client_secret = e("TIKTOK_CLIENT_SECRET", "")
        self.tt_redirect_uri = e("TIKTOK_REDIRECT_URI", "")
        self.tt_mode = e("TIKTOK_MODE", "inbox")  # inbox = مسودة في التطبيق | direct = نشر مباشر (يحتاج audit)
        self.tt_privacy = e("TIKTOK_PRIVACY", "SELF_ONLY")

        self.loaded = True

    def ensure_dirs(self):
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def secrets(self):
        """القيم الحساسة — نستعملها باش نخبّيوها من رسائل الأخطاء."""
        vals = [self.fb_page_token, self.ig_token, self.tt_client_secret, self.secret]
        return [v for v in vals if v and len(v) > 6]


cfg = Config()


def redact(text):
    """نمسح المفاتيح من أي نص قبل ما نسجّلوه أو نعرضوه."""
    text = str(text)
    for s in cfg.secrets():
        text = text.replace(s, "***")
    return text
