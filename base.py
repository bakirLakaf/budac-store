"""أدوات مشتركة بين المنصات."""
import requests

from ..config import cfg, redact


class PlatformError(Exception):
    """خطأ من منصة (رسالتو تتعرض في اللوحة، بعد ما نمسحو المفاتيح)."""


def http(method, url, **kw):
    """كل الطلبات تدوز من هنا (باش نقدرو نجرّبو الكود بلا انترنت)."""
    kw.setdefault("timeout", 60)
    return requests.request(method, url, **kw)


def graph(method, path, token, data=None, params=None):
    """طلب لـ Meta Graph API مع معالجة الأخطاء."""
    url = f"https://graph.facebook.com/{cfg.graph_version}/{path.lstrip('/')}"
    if method.upper() == "GET":
        r = http("GET", url, params={**(params or {}), "access_token": token})
    else:
        r = http("POST", url, data={**(data or {}), "access_token": token})
    try:
        js = r.json()
    except ValueError:
        raise PlatformError(f"رد غير مفهوم من Meta (HTTP {r.status_code})")
    if r.status_code >= 400 or "error" in js:
        err = js.get("error", {})
        msg = err.get("error_user_msg") or err.get("message") or str(js)
        raise PlatformError(redact(f"Meta: {msg} (code {err.get('code')}/{err.get('error_subcode')})"))
    return js
