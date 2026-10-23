"""TikTok Content Posting API.
- TIKTOK_MODE=inbox (الافتراضي): الفيديو/الصور توصل لمسودات تطبيق تيك توك، وإنت تكمل النشر بضغطة. (scope: video.upload)
- TIKTOK_MODE=direct: نشر مباشر، لكن قبل موافقة (audit) تيك توك كل منشور يخرج خاص. (scope: video.publish)
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from .. import db
from .. import media as M
from ..config import cfg
from . import base
from .base import PlatformError

NAME = "tiktok"
API = "https://open.tiktokapis.com"
CHUNK = 10 * 1024 * 1024


def configured():
    return bool(cfg.tt_client_key and cfg.tt_client_secret and _load())


def oauth_ready():
    return bool(cfg.tt_client_key and cfg.tt_client_secret and cfg.tt_redirect_uri)


def scope():
    return "user.info.basic," + ("video.publish" if cfg.tt_mode == "direct" else "video.upload")


def authorize_url(state):
    q = {"client_key": cfg.tt_client_key, "scope": scope(), "response_type": "code",
         "redirect_uri": cfg.tt_redirect_uri, "state": state}
    return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(q)


# ---------- التوكن ----------
def _load():
    with db.cursor(commit=False) as cur:
        cur.execute("SELECT data FROM sp_tokens WHERE platform='tiktok'")
        row = cur.fetchone()
    return row["data"] if row else None


def _save(tok):
    now = datetime.now(timezone.utc)
    data = {
        "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
        "open_id": tok.get("open_id"),
        "expires_at": (now + timedelta(seconds=int(tok.get("expires_in", 86400)))).isoformat(),
    }
    with db.cursor() as cur:
        cur.execute("""INSERT INTO sp_tokens (platform, data) VALUES ('tiktok', %s)
                       ON CONFLICT (platform) DO UPDATE SET data=EXCLUDED.data, updated_at=now()""",
                    (json.dumps(data),))


def _token_request(form):
    r = base.http("POST", f"{API}/v2/oauth/token/", data=form,
             headers={"Content-Type": "application/x-www-form-urlencoded"})
    js = r.json()
    if "access_token" not in js:
        raise PlatformError(f"TikTok OAuth: {js.get('error_description') or js}")
    return js


def exchange_code(code):
    _save(_token_request({
        "client_key": cfg.tt_client_key, "client_secret": cfg.tt_client_secret, "code": code,
        "grant_type": "authorization_code", "redirect_uri": cfg.tt_redirect_uri}))


def _access_token():
    data = _load()
    if not data:
        raise PlatformError("تيك توك ما هوش مربوط — اضغط 'ربط تيك توك' في اللوحة")
    if datetime.fromisoformat(data["expires_at"]) - datetime.now(timezone.utc) < timedelta(minutes=5):
        if not data.get("refresh_token"):
            raise PlatformError("توكن تيك توك انتهى، اربط الحساب من جديد")
        tok = _token_request({"client_key": cfg.tt_client_key, "client_secret": cfg.tt_client_secret,
                              "grant_type": "refresh_token", "refresh_token": data["refresh_token"]})
        tok.setdefault("open_id", data.get("open_id"))
        _save(tok)
        data = _load()
    return data["access_token"]


def check():
    _access_token()
    return f"تيك توك: مربوط (وضع {cfg.tt_mode})"


def _post(path, token, body):
    r = base.http("POST", f"{API}{path}", json=body, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
    js = r.json()
    err = js.get("error", {})
    if r.status_code >= 400 or err.get("code") not in (None, "ok"):
        raise PlatformError(f"TikTok: {err.get('message') or js} ({err.get('code')})")
    return js["data"]


def _wait_status(publish_id, token):
    """نتابعو الحالة شوية؛ لو بقات تعالج ما نفشلوش العملية."""
    for _ in range(24):
        d = _post("/v2/post/publish/status/fetch/", token, {"publish_id": publish_id})
        st = d.get("status")
        if st in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            return
        if st == "FAILED":
            raise PlatformError(f"تيك توك فشل: {d.get('fail_reason')}")
        time.sleep(5)


def _upload_chunks(upload_url, path, size):
    ctype = "video/quicktime" if path.lower().endswith(".mov") else "video/mp4"
    chunk_size = size if size < 5 * 1024 * 1024 else CHUNK
    total = max(1, size // chunk_size)
    with open(path, "rb") as f:
        for i in range(total):
            start = i * chunk_size
            length = size - start if i == total - 1 else chunk_size  # آخر جزء ياخذ الباقي
            data = f.read(length)
            r = base.http("PUT", upload_url, data=data, headers={
                "Content-Type": ctype, "Content-Length": str(length),
                "Content-Range": f"bytes {start}-{start + length - 1}/{size}"}, timeout=300)
            if r.status_code >= 400:
                raise PlatformError(f"فشل رفع الفيديو لتيك توك (HTTP {r.status_code})")
    return total


def publish(post, media):
    token = _access_token()
    caption = post["caption"]
    videos = [m for m in media if m["kind"] == "video"]
    images = [m for m in media if m["kind"] == "image"]

    if videos:
        v = videos[0]
        if not v["local_path"] or not os.path.exists(v["local_path"]):
            raise PlatformError("ملف الفيديو ما بقاش موجود في السيرفر")
        size = os.path.getsize(v["local_path"])
        chunk_size = size if size < 5 * 1024 * 1024 else CHUNK
        body = {"source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                "chunk_size": chunk_size, "total_chunk_count": max(1, size // chunk_size)}}
        if cfg.tt_mode == "direct":
            body["post_info"] = {"title": caption[:2200], "privacy_level": cfg.tt_privacy}
            path = "/v2/post/publish/video/init/"
        else:
            path = "/v2/post/publish/inbox/video/init/"
        d = _post(path, token, body)
        _upload_chunks(d["upload_url"], v["local_path"], size)
        _wait_status(d["publish_id"], token)
        return d["publish_id"]

    if images:
        body = {
            "post_info": {"title": (caption.splitlines() or [""])[0][:90], "description": caption[:4000]},
            "source_info": {"source": "PULL_FROM_URL", "photo_cover_index": 0,
                            "photo_images": [M.public_url(m) for m in images[:35]]},
            "post_mode": "DIRECT_POST" if cfg.tt_mode == "direct" else "MEDIA_UPLOAD",
            "media_type": "PHOTO",
        }
        if cfg.tt_mode == "direct":
            body["post_info"]["privacy_level"] = cfg.tt_privacy
        d = _post("/v2/post/publish/content/init/", token, body)
        _wait_status(d["publish_id"], token)
        return d["publish_id"]

    raise PlatformError("تيك توك لازم فيديو أو صور")
