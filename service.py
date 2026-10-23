"""قلب الأداة: إنشاء المنشورات، نشرها على المنصات، وتنظيف الملفات المؤقتة بعد النشر."""
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from . import db
from . import media as M
from .config import cfg, redact
from .platforms import facebook, instagram, tiktok

log = logging.getLogger("social_publisher")

PLATFORMS = {"facebook": facebook, "instagram": instagram, "tiktok": tiktok}
ACTIVE = ("scheduled", "publishing", "partial", "failed")  # حالات نحتاج فيها الملفات
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sp-publish")


# ---------- التحقق ----------
def validate(caption, platforms, media):
    if not platforms:
        return "اختار منصة وحدة على الأقل"
    kinds = {m["kind"] for m in media}
    if len(kinds) > 1:
        return "ما تخلطش صور مع فيديو في منشور واحد"
    if "video" in kinds and len(media) > 1:
        return "فيديو واحد برك في كل منشور"
    if len(media) > cfg.max_images:
        return f"الحد الأقصى {cfg.max_images} صور"
    if not caption.strip() and not media:
        return "اكتب نص ولا حط صورة/فيديو"
    if "instagram" in platforms:
        if not media:
            return "انستغرام لازم صورة أو فيديو"
        if len(caption) > 2200:
            return "نص انستغرام الحد الأقصى 2200 حرف"
    if "tiktok" in platforms and not media:
        return "تيك توك لازم فيديو أو صور"
    return None


# ---------- إنشاء ----------
def create_post(caption, platforms, scheduled_at, product_id, media):
    """media: قائمة dict (source, kind, url, local_path, sha256). scheduled_at: datetime بـ UTC أو None=درك."""
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO sp_posts (caption, platforms, scheduled_at, product_id)
               VALUES (%s, %s, COALESCE(%s, now()), %s) RETURNING id""",
            (caption, platforms, scheduled_at, product_id))
        pid = cur.fetchone()["id"]
        for pos, m in enumerate(media):
            cur.execute(
                """INSERT INTO sp_media (post_id, position, source, kind, url, local_path, sha256)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (pid, pos, m["source"], m["kind"], m.get("url"), m.get("local_path"), m.get("sha256")))
        for p in platforms:
            cur.execute("INSERT INTO sp_results (post_id, platform) VALUES (%s,%s)", (pid, p))
    return pid


def kick():
    """نشغّلو الإنجاز في خيط خلفي (باش الطلب ما يبقاش معلّق، خاصة الفيديوهات)."""
    _executor.submit(run_due)


# ---------- التنفيذ ----------
def claim_due(limit=3):
    """ناخذو المنشورات اللي وقتها وصل. SKIP LOCKED = حتى لو بزاف workers، كل منشور ينتشر مرة وحدة."""
    with db.cursor() as cur:
        cur.execute(
            """UPDATE sp_posts SET status='publishing', started_at=now()
               WHERE id IN (SELECT id FROM sp_posts
                            WHERE status='scheduled' AND scheduled_at <= now()
                            ORDER BY scheduled_at LIMIT %s FOR UPDATE SKIP LOCKED)
               RETURNING id""", (limit,))
        return [r["id"] for r in cur.fetchall()]


def run_due():
    for pid in claim_due():
        try:
            run_post(pid)
        except Exception:
            log.exception("خطأ غير متوقع في المنشور %s", pid)
            _finish(pid)


def _media_rows(post_id):
    with db.cursor(commit=False) as cur:
        cur.execute("SELECT * FROM sp_media WHERE post_id=%s ORDER BY position", (post_id,))
        return cur.fetchall()


def _set_result(post_id, platform, status, remote_id=None, error=None):
    with db.cursor() as cur:
        cur.execute(
            """UPDATE sp_results SET status=%s, remote_id=%s, error=%s, attempts=attempts+1, updated_at=now()
               WHERE post_id=%s AND platform=%s""", (status, remote_id, error, post_id, platform))


def run_post(post_id):
    with db.cursor(commit=False) as cur:
        cur.execute("SELECT * FROM sp_posts WHERE id=%s", (post_id,))
        post = cur.fetchone()
        cur.execute("SELECT platform, status FROM sp_results WHERE post_id=%s", (post_id,))
        done = {r["platform"] for r in cur.fetchall() if r["status"] == "done"}
    media = _media_rows(post_id)

    for name in post["platforms"]:
        if name in done:
            continue  # نجح قبل — ما نعاودوش ننشرو (ما نكرروش المنشور)
        plat = PLATFORMS[name]
        try:
            if not plat.configured():
                raise RuntimeError("المنصة ما هيش مربوطة (شوف .env)")
            for m in media:
                if m["source"] == "upload" and not (m["local_path"] and os.path.exists(m["local_path"])):
                    raise RuntimeError("الملف المؤقت تحذف، ارفعو من جديد")
            remote = plat.publish(post, media)
            _set_result(post_id, name, "done", remote_id=remote)
        except Exception as e:  # noqa: BLE001
            log.warning("فشل النشر على %s للمنشور %s: %s", name, post_id, redact(e))
            _set_result(post_id, name, "failed", error=redact(e)[:500])
    _finish(post_id)


def _finish(post_id):
    with db.cursor() as cur:
        cur.execute("SELECT status FROM sp_results WHERE post_id=%s", (post_id,))
        sts = [r["status"] for r in cur.fetchall()]
        if sts and all(s == "done" for s in sts):
            final = "done"
        elif any(s == "done" for s in sts):
            final = "partial"
        else:
            final = "failed"
        cur.execute("UPDATE sp_posts SET status=%s, finished_at=now() WHERE id=%s", (final, post_id))
    if final == "done":
        release_media(post_id)  # نجح في الكل → نحذفو الملفات المؤقتة
    return final


# ---------- إدارة ----------
def retry_post(post_id):
    with db.cursor() as cur:
        cur.execute("""UPDATE sp_posts SET status='scheduled', scheduled_at=now()
                       WHERE id=%s AND status IN ('failed','partial') RETURNING id""", (post_id,))
        ok = cur.fetchone() is not None
        if ok:
            cur.execute("UPDATE sp_results SET status='pending', error=NULL WHERE post_id=%s AND status='failed'",
                        (post_id,))
    return ok


def cancel_post(post_id):
    with db.cursor() as cur:
        cur.execute("UPDATE sp_posts SET status='cancelled', finished_at=now() WHERE id=%s AND status='scheduled' RETURNING id",
                    (post_id,))
        ok = cur.fetchone() is not None
    if ok:
        release_media(post_id)
    return ok


def delete_post(post_id):
    with db.cursor() as cur:
        cur.execute("SELECT status FROM sp_posts WHERE id=%s", (post_id,))
        row = cur.fetchone()
        if not row or row["status"] in ("scheduled", "publishing"):
            return False
    release_media(post_id, force=True)
    with db.cursor() as cur:
        cur.execute("DELETE FROM sp_posts WHERE id=%s", (post_id,))
    return True


def release_media(post_id, force=False):
    """نحذفو الملفات المؤقتة تاع هاد المنشور، إلا لو منشور آخر باقي يحتاج نفس الملف."""
    with db.cursor() as cur:
        cur.execute("SELECT id, sha256, local_path FROM sp_media WHERE post_id=%s AND source='upload' AND NOT purged",
                    (post_id,))
        for m in cur.fetchall():
            cur.execute(
                """SELECT 1 FROM sp_media m JOIN sp_posts p ON p.id=m.post_id
                   WHERE m.sha256=%s AND m.post_id<>%s AND NOT m.purged AND p.status = ANY(%s) LIMIT 1""",
                (m["sha256"], post_id, list(ACTIVE)))
            if cur.fetchone() is None:
                M.delete_file(m["local_path"])
            cur.execute("UPDATE sp_media SET purged=TRUE WHERE id=%s", (m["id"],))


def janitor():
    """تنظيف دوري: منشورات عالقة، فاشلة قديمة، وملفات يتيمة."""
    with db.cursor() as cur:
        cur.execute("""UPDATE sp_posts SET status='failed' WHERE status='publishing'
                       AND started_at < now() - interval '30 minutes' RETURNING id""")
        for r in cur.fetchall():
            log.warning("المنشور %s علق، رجّعتو failed (تقدر تعاود)", r["id"])
        cur.execute("""SELECT id FROM sp_posts WHERE status IN ('failed','partial')
                       AND finished_at < now() - make_interval(days => %s)""", (cfg.keep_failed_days,))
        old = [r["id"] for r in cur.fetchall()]
    for pid in old:
        release_media(pid)
    # ملفات في المجلد ما عندها حتى منشور نشط (مثلا رفع تقطع في النص)
    with db.cursor(commit=False) as cur:
        cur.execute("""SELECT DISTINCT m.local_path FROM sp_media m JOIN sp_posts p ON p.id=m.post_id
                       WHERE m.source='upload' AND NOT m.purged AND p.status = ANY(%s)""", (list(ACTIVE),))
        keep = {r["local_path"] for r in cur.fetchall()}
    now = time.time()
    for f in cfg.temp_dir.iterdir():
        if str(f) not in keep and f.is_file() and now - f.stat().st_mtime > 3600:
            M.delete_file(str(f))


def discard_unreferenced(paths):
    """نحذفو ملفات اترفعت للتو ولكن المنشور ما تسجلش (خطأ في التحقق) — إلا لو منشور آخر يستعملها."""
    for path in paths:
        with db.cursor(commit=False) as cur:
            cur.execute("SELECT 1 FROM sp_media WHERE local_path=%s AND NOT purged LIMIT 1", (path,))
            used = cur.fetchone() is not None
        if not used:
            M.delete_file(path)
