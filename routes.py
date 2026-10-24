"""مسارات لوحة النشر. كل مسار إداري محمي بـ admin_required تاع المتجر."""
import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, Response, abort, jsonify, redirect, render_template, request,
                   send_file)

from . import db
from . import media as M
from . import service
from .config import cfg, redact
from .platforms import facebook, instagram, tiktok
from .platforms.base import graph
from .service import ACTIVE, PLATFORMS

log = logging.getLogger("social_publisher")

LABELS = {"facebook": "فيسبوك", "instagram": "انستغرام", "tiktok": "تيك توك"}
STATUS_AR = {"scheduled": "مجدول", "publishing": "راهو ينتشر", "done": "تنشر ✓", "partial": "نجح جزئيًا",
             "failed": "فشل", "cancelled": "ملغى", "pending": "في الانتظار"}


def _tz():
    return timezone(timedelta(hours=cfg.tz_offset_hours))


def _parse_local(value):
    """'2026-10-25T18:30' (توقيت الجزائر) -> datetime بـ UTC."""
    dt = datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=_tz())
    return dt.astimezone(timezone.utc)


def _state(secret_ts=None):
    ts = str(int(secret_ts or time.time()))
    sig = hmac.new(cfg.secret.encode(), ts.encode(), hashlib.sha256).hexdigest()[:20]
    return f"{ts}.{sig}"


def _state_ok(state):
    try:
        ts, sig = state.split(".")
        return hmac.compare_digest(_state(ts), state) and time.time() - int(ts) < 600
    except Exception:
        return False


def create_blueprint(admin_required, adapter):
    bp = Blueprint("social_publisher", __name__, template_folder="templates")
    P = cfg.admin_prefix

    @bp.app_template_filter("sp_local")
    def sp_local(dt):
        return dt.astimezone(_tz()).strftime("%Y-%m-%d %H:%M") if dt else ""

    @bp.app_template_filter("sp_status")
    def sp_status(s):
        return STATUS_AR.get(s, s)

    def _ensure_base_url():
        """لو SP_PUBLIC_BASE_URL ما تحطش، نستعملو عنوان الموقع من الطلب (ProxyFix يعطي https)."""
        if not cfg.public_base_url:
            cfg.public_base_url = request.url_root.rstrip("/")

    @bp.before_request
    def _before():
        if request.path.startswith(P):
            _ensure_base_url()

    def _setup_status():
        """حالة الربط لكل منصة (أسماء المتغيرات برك — ما نعرضوش القيم أبدًا)."""
        def row(name, ok):
            return {"name": name, "ok": bool(ok)}
        return [
            {"key": "facebook", "ok": facebook.configured(),
             "vars": [row("FB_PAGE_ID", cfg.fb_page_id), row("FB_PAGE_TOKEN", cfg.fb_page_token)]},
            {"key": "instagram", "ok": instagram.configured(),
             "vars": [row("IG_USER_ID", cfg.ig_user_id), row("FB_PAGE_TOKEN (نفسو يخدم)", cfg.ig_token)]},
            {"key": "tiktok", "ok": tiktok.configured(),
             "vars": [row("TIKTOK_CLIENT_KEY", cfg.tt_client_key), row("TIKTOK_CLIENT_SECRET", cfg.tt_client_secret),
                      row("TIKTOK_REDIRECT_URI", cfg.tt_redirect_uri)]},
        ]

    def _context(**extra):
        with db.cursor(commit=False) as cur:
            cur.execute("""
                SELECT p.*, (SELECT count(*) FROM sp_media m WHERE m.post_id=p.id) AS media_count,
                  (SELECT json_agg(json_build_object('platform', r.platform, 'status', r.status, 'error', r.error)
                                   ORDER BY r.platform) FROM sp_results r WHERE r.post_id=p.id) AS results
                FROM sp_posts p ORDER BY p.id DESC LIMIT 40""")
            posts = cur.fetchall()
        ctx = dict(
            prefix=P, labels=LABELS, posts=posts,
            configured={n: m.configured() for n, m in PLATFORMS.items()},
            tiktok_connect=tiktok.oauth_ready(), setup=_setup_status(),
            public_base_url=cfg.public_base_url, tiktok_mode=cfg.tt_mode,
            tiktok_redirect_suggestion=f"{cfg.public_base_url}{P}/tiktok/callback",
            all_connected=all(m.configured() for m in (facebook, instagram)),
            has_active=any(p["status"] in ("scheduled", "publishing") for p in posts),
            max_images=cfg.max_images, max_video_mb=cfg.max_video_mb,
            msg=request.args.get("msg"), err=request.args.get("err"), form={})
        ctx.update(extra)
        return ctx

    @bp.get(P + "/")
    @admin_required
    def index():
        return render_template("social_publisher/index.html", **_context())

    # ---------- بحث المنتجات ----------
    @bp.get(P + "/api/products")
    @admin_required
    def api_products():
        return jsonify(adapter.search_products(request.args.get("q", "").strip()))

    @bp.get(P + "/api/products/<pid>")
    @admin_required
    def api_product(pid):
        p = adapter.get_product(pid)
        if not p:
            abort(404)
        return jsonify({"id": p["id"], "name": p["name"], "price": p["price"], "url": p["url"],
                        "images": [{"id": i["id"], "url": i["url"], "thumb": i.get("thumb") or i["url"]}
                               for i in p["images"]]})

    # ---------- إنشاء منشور ----------
    @bp.post(P + "/posts")
    @admin_required
    def create():
        f = request.form
        caption = (f.get("caption") or "").strip()
        platforms = [p for p in f.getlist("platforms") if p in PLATFORMS]
        mode = f.get("mode", "now")
        form_state = {"caption": caption, "platforms": platforms, "mode": mode,
                      "scheduled_at": f.get("scheduled_at", "")}

        def fail(msg, code=400):
            return render_template("social_publisher/index.html", **_context(err=msg, form=form_state)), code

        if request.content_length and request.content_length > (cfg.max_video_mb + 60) * 1024 * 1024:
            return fail("الملفات كبيرة بزاف", 413)

        media, new_files = [], []
        try:
            product_id = f.get("product_id") or None
            if product_id:
                product = adapter.get_product(product_id)
                if not product:
                    return fail("المنتج ما لقيتوش")
                wanted = set(f.getlist("gallery"))
                for img in product["images"]:  # نختارو من صور المنتج الحقيقية برك (ما نثقوش في الرابط تاع المتصفح)
                    if img["id"] in wanted:
                        media.append({"source": "gallery", "kind": "image", "url": img["url"],
                                      "local_path": img.get("local_path"), "sha256": None})
            for up in request.files.getlist("files"):
                if up and up.filename:
                    info = M.save_upload(up)
                    new_files.append(info["path"])
                    media.append({"source": "upload", "kind": info["kind"], "url": None,
                                  "local_path": info["path"], "sha256": info["sha256"]})

            err = service.validate(caption, platforms, media)
            scheduled_at = None
            if not err and mode == "schedule":
                try:
                    scheduled_at = _parse_local(f.get("scheduled_at", ""))
                except ValueError:
                    err = "اختار تاريخ ووقت صحيح للجدولة"
                else:
                    if scheduled_at <= datetime.now(timezone.utc) + timedelta(seconds=20):
                        err = "وقت الجدولة لازم يكون في المستقبل"
            if err:
                service.discard_unreferenced(new_files)
                return fail(err)
            service.create_post(caption, platforms, scheduled_at, product_id, media)
        except M.MediaError as e:
            service.discard_unreferenced(new_files)
            return fail(str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("فشل إنشاء المنشور")
            service.discard_unreferenced(new_files)
            return fail("صرا خطأ: " + redact(e), 500)

        if mode == "schedule":
            return redirect(f"{P}/?msg=تجدولت المنشور#posts")
        service.kick()
        return redirect(f"{P}/?msg=المنشور راهو ينتشر درك — تابع حالتو هنا#posts")

    @bp.post(P + "/posts/<int:pid>/<action>")
    @admin_required
    def post_action(pid, action):
        fn = {"retry": service.retry_post, "cancel": service.cancel_post, "delete": service.delete_post}.get(action)
        if not fn:
            abort(404)
        ok = fn(pid)
        if ok and action == "retry":
            service.kick()
        return redirect(f"{P}/?" + ("msg=تمت العملية" if ok else "err=ما نقدرش ندير هاد العملية على هاد المنشور") + "#posts")

    # ---------- فحص الاتصال ----------
    @bp.get(P + "/check")
    @admin_required
    def check():
        out = {}
        for name, mod in PLATFORMS.items():
            if name == "tiktok" and not mod.configured():
                out[name] = {"ok": False, "msg": "ما هوش مربوط"}
                continue
            if name != "tiktok" and not mod.configured():
                out[name] = {"ok": False, "msg": "المفاتيح ناقصة في .env"}
                continue
            try:
                out[name] = {"ok": True, "msg": mod.check()}
            except Exception as e:  # noqa: BLE001
                out[name] = {"ok": False, "msg": redact(e)}
        return jsonify(out)

    @bp.get(P + "/ig-lookup")
    @admin_required
    def ig_lookup():
        """يجيب IG_USER_ID من صفحة فيسبوك (باش ما تقلّبش عليه في Graph Explorer)."""
        if not facebook.configured():
            return jsonify(ok=False, msg="حط FB_PAGE_ID و FB_PAGE_TOKEN فـ Railway قبل")
        try:
            js = graph("GET", cfg.fb_page_id, cfg.fb_page_token,
                       params={"fields": "instagram_business_account{id,username}"})
        except Exception as e:  # noqa: BLE001
            return jsonify(ok=False, msg=redact(e))
        acc = js.get("instagram_business_account")
        if not acc:
            return jsonify(ok=False, msg="هاد الصفحة ما عندهاش حساب انستغرام Business/Creator مربوط. اربطو من إعدادات الصفحة وعاود.")
        return jsonify(ok=True, id=acc["id"], username=acc.get("username"))

    # ---------- ربط تيك توك ----------
    @bp.get(P + "/tiktok/connect")
    @admin_required
    def tiktok_connect():
        if not tiktok.oauth_ready():
            return redirect(f"{P}/?err=TIKTOK_CLIENT_KEY / SECRET / REDIRECT_URI ناقصين في Railway#setup")
        return redirect(tiktok.authorize_url(_state()))

    @bp.get(P + "/tiktok/callback")
    @admin_required
    def tiktok_callback():
        if request.args.get("error") or not _state_ok(request.args.get("state", "")):
            return redirect(f"{P}/?err=ربط تيك توك ما كملش#setup")
        try:
            tiktok.exchange_code(request.args.get("code", ""))
        except Exception as e:  # noqa: BLE001
            return redirect(f"{P}/?err={redact(e)[:150]}#setup")
        return redirect(f"{P}/?msg=تيك توك تربط ✓#setup")

    # ---------- الميديا العامة (فيسبوك/انستغرام يجبدو منها) ----------
    @bp.get("/sp/pub/<token>/<int:mid>.<ext>")
    def pub(token, mid, ext):
        if not M.verify_token(mid, token):
            abort(404)
        with db.cursor(commit=False) as cur:
            cur.execute("""SELECT m.*, p.status AS post_status FROM sp_media m
                           JOIN sp_posts p ON p.id=m.post_id WHERE m.id=%s""", (mid,))
            m = cur.fetchone()
        # الرابط يخدم غير ما دام المنشور ما كملش
        if not m or m["purged"] or m["post_status"] not in ACTIVE:
            abort(404)
        headers = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}
        if m["kind"] == "image":
            try:
                data = M.render_jpeg(m, ig=request.args.get("ig") == "1")
            except Exception:
                abort(404)
            return Response(data, mimetype="image/jpeg", headers=headers)
        if not m["local_path"]:
            abort(404)
        mime = "video/quicktime" if m["local_path"].lower().endswith(".mov") else "video/mp4"
        resp = send_file(m["local_path"], mimetype=mime, conditional=True)
        resp.headers.update(headers)
        return resp

    return bp
