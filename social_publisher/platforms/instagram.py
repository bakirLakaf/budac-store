"""النشر على انستغرام (حساب Business/Creator مربوط بصفحة فيسبوك).
الطريقة: نصنعو container، نستناو يوصل FINISHED، بعدها media_publish."""
import time

from .. import media as M
from ..config import cfg
from .base import PlatformError, graph

NAME = "instagram"
POLL_SECONDS = 5


def configured():
    return bool(cfg.ig_user_id and cfg.ig_token)


def check():
    js = graph("GET", cfg.ig_user_id, cfg.ig_token, params={"fields": "username"})
    return f"انستغرام: @{js.get('username')}"


def _wait(container_id, timeout):
    deadline = time.time() + timeout
    while True:
        js = graph("GET", container_id, cfg.ig_token, params={"fields": "status_code,status"})
        code = js.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise PlatformError(f"انستغرام رفض الملف: {js.get('status') or code}")
        if time.time() > deadline:
            raise PlatformError("انستغرام تأخر بزاف في المعالجة، عاود بعد شوية")
        time.sleep(POLL_SECONDS)


def publish(post, media):
    uid, tok, caption = cfg.ig_user_id, cfg.ig_token, post["caption"][:2200]
    if not media:
        raise PlatformError("انستغرام لازم صورة أو فيديو")
    images = [m for m in media if m["kind"] == "image"]
    videos = [m for m in media if m["kind"] == "video"]

    if videos:
        c = graph("POST", f"{uid}/media", tok, data={
            "media_type": "REELS", "video_url": M.public_url(videos[0]),
            "caption": caption, "share_to_feed": "true"})
        _wait(c["id"], timeout=600)
    elif len(images) == 1:
        c = graph("POST", f"{uid}/media", tok,
                  data={"image_url": M.public_url(images[0], ig=True), "caption": caption})
        _wait(c["id"], timeout=120)
    else:
        children = []
        for m in images:
            ch = graph("POST", f"{uid}/media", tok,
                       data={"image_url": M.public_url(m, ig=True), "is_carousel_item": "true"})
            children.append(ch["id"])
        for cid in children:
            _wait(cid, timeout=120)
        c = graph("POST", f"{uid}/media", tok, data={
            "media_type": "CAROUSEL", "children": ",".join(children), "caption": caption})
        _wait(c["id"], timeout=120)

    js = graph("POST", f"{uid}/media_publish", tok, data={"creation_id": c["id"]})
    return str(js.get("id"))
