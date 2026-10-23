"""الميديا:
- صور المتجر (gallery): ما نكوبيوهاش أبدًا، نستعملو مسارها/رابطها كيما هي.
- الملفات الخارجية (upload): نحفظوها مؤقتًا باسم sha256 (نفس الملف = نسخة وحدة)، وتتحذف بعد النشر.
"""
import hashlib
import hmac
import io
import os
import uuid
from pathlib import Path

import requests
from PIL import Image, ImageOps

from .config import cfg

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov"}
IG_MIN_RATIO, IG_MAX_RATIO = 0.8, 1.91  # حدود نسبة العرض/الطول المقبولة في انستغرام


class MediaError(Exception):
    pass


# ---------- روابط موقّعة ----------
def token_for(media_id):
    return hmac.new(cfg.secret.encode(), f"m{media_id}".encode(), hashlib.sha256).hexdigest()[:24]


def verify_token(media_id, token):
    return hmac.compare_digest(token_for(media_id), token or "")


def public_url(m, ig=False):
    """الرابط العام اللي منو فيسبوك/انستغرام يجبدو الملف."""
    if not cfg.public_base_url:
        raise MediaError("SP_PUBLIC_BASE_URL ناقص في .env")
    ext = "jpg" if m["kind"] == "image" else Path(m["local_path"] or "x.mp4").suffix.lstrip(".").lower() or "mp4"
    url = f"{cfg.public_base_url}/sp/pub/{token_for(m['id'])}/{m['id']}.{ext}"
    return url + ("?ig=1" if ig and m["kind"] == "image" else "")


# ---------- الرفع المؤقت ----------
def save_upload(storage):
    """يحفظ ملف مرفوع. يرجّع dict: kind, sha256, path. ينقص حجم الصور ويكرر بلا نسخ زايدة."""
    name = (storage.filename or "").lower()
    ext = Path(name).suffix
    if ext in IMAGE_EXT:
        return _save_image(storage)
    if ext in VIDEO_EXT:
        return _save_video(storage, ext)
    raise MediaError(f"نوع الملف ما هوش مدعوم ({ext or 'بلا امتداد'}). المقبول: jpg, png, webp, mp4, mov")


def _save_image(storage):
    raw = storage.read(cfg.max_image_mb * 1024 * 1024 + 1)
    if len(raw) > cfg.max_image_mb * 1024 * 1024:
        raise MediaError(f"الصورة كبيرة بزاف (الحد {cfg.max_image_mb}MB)")
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise MediaError("الصورة تالفة أو ما نقدرش نقراها")
    img.thumbnail((1440, 1440))  # انستغرام ما يحتاجش أكثر + نوفّرو مساحة السيرفر
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85, optimize=True)
    data = buf.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    path = cfg.temp_dir / f"{sha}.jpg"
    if not path.exists():  # نفس الصورة موجودة؟ ما نكتبوهاش مرة ثانية
        tmp = cfg.temp_dir / f"_tmp_{uuid.uuid4().hex}"
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return {"kind": "image", "sha256": sha, "path": str(path)}


def _save_video(storage, ext):
    limit = cfg.max_video_mb * 1024 * 1024
    tmp = cfg.temp_dir / f"_tmp_{uuid.uuid4().hex}"
    h, size = hashlib.sha256(), 0
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = storage.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise MediaError(f"الفيديو كبير بزاف (الحد {cfg.max_video_mb}MB)")
                h.update(chunk)
                f.write(chunk)
        if size == 0:
            raise MediaError("الفيديو فارغ")
        sha = h.hexdigest()
        path = cfg.temp_dir / f"{sha}{ext}"
        if path.exists():
            os.remove(tmp)
        else:
            os.replace(tmp, path)
        return {"kind": "video", "sha256": sha, "path": str(path)}
    finally:
        if tmp.exists():
            os.remove(tmp)


def delete_file(path):
    """نحذف ملف مؤقت، وبس لو كان داخل مجلد الأداة (أمان: ما نمسحوش ملفات المتجر أبدًا)."""
    try:
        p = Path(path).resolve()
        if cfg.temp_dir in p.parents and p.exists():
            p.unlink()
            return True
    except OSError:
        pass
    return False


# ---------- التحويل لـ JPEG (بالذاكرة، بلا حفظ نسخة) ----------
def _load_image(m):
    if m["local_path"] and os.path.exists(m["local_path"]):
        return Image.open(m["local_path"])
    if m["url"]:
        r = requests.get(m["url"], timeout=20, stream=True)
        r.raise_for_status()
        raw = r.raw.read(15 * 1024 * 1024 + 1, decode_content=True)
        if len(raw) > 15 * 1024 * 1024:
            raise MediaError("الصورة كبيرة بزاف")
        return Image.open(io.BytesIO(raw))
    raise MediaError("الصورة ما بقاتش موجودة")


def render_jpeg(m, ig=False):
    """يرجّع bytes JPEG. لو ig=True نضبطو النسبة باش تقبلها انستغرام (حشو أبيض)."""
    img = ImageOps.exif_transpose(_load_image(m)).convert("RGB")
    img.thumbnail((1440, 1440))
    if ig:
        w, h = img.size
        ratio = w / h
        if ratio < IG_MIN_RATIO:
            nw, nh = int(h * IG_MIN_RATIO), h
        elif ratio > IG_MAX_RATIO:
            nw, nh = w, int(w / IG_MAX_RATIO)
        else:
            nw, nh = w, h
        if (nw, nh) != (w, h):
            canvas = Image.new("RGB", (nw, nh), "white")
            canvas.paste(img, ((nw - w) // 2, (nh - h) // 2))
            img = canvas
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()
