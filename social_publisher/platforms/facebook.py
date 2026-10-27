"""النشر على صفحة فيسبوك عن طريق Graph API."""
import json

from .. import media as M
from ..config import cfg
from .base import PlatformError, graph

NAME = "facebook"


def configured():
    return bool(cfg.fb_page_id and cfg.fb_page_token)


def check():
    """فحص الاتصال: يرجّع اسم الصفحة."""
    js = graph("GET", cfg.fb_page_id, cfg.fb_page_token, params={"fields": "name"})
    return f"صفحة: {js.get('name')}"


def publish(post, media):
    pid, tok, caption = cfg.fb_page_id, cfg.fb_page_token, post["caption"]
    images = [m for m in media if m["kind"] == "image"]
    videos = [m for m in media if m["kind"] == "video"]

    if videos:
        js = graph("POST", f"{pid}/videos", tok,
                   data={"file_url": M.public_url(videos[0]), "description": caption})
        return str(js.get("id"))
    if not images:
        if not caption:
            raise PlatformError("ما كاين لا نص لا ميديا")
        return str(graph("POST", f"{pid}/feed", tok, data={"message": caption}).get("id"))
    if len(images) == 1:
        js = graph("POST", f"{pid}/photos", tok,
                   data={"url": M.public_url(images[0]), "caption": caption, "published": "true"})
        return str(js.get("post_id") or js.get("id"))

    # عدة صور: نرفعو كل وحدة غير منشورة، بعدها بوست واحد يجمعهم
    ids = []
    for m in images:
        js = graph("POST", f"{pid}/photos", tok, data={"url": M.public_url(m), "published": "false"})
        ids.append({"media_fbid": js["id"]})
    js = graph("POST", f"{pid}/feed", tok,
               data={"message": caption, "attached_media": json.dumps(ids)})
    return str(js.get("id"))
