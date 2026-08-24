"""
ربط YouCan عبر OAuth — من لوحة التحكم مباشرة.

⚠️ ليش من الموقع وماشي من جهاز الأدمن؟
تطبيق YouCan يعطي Client ID و Secret فقط، ماشي توكن جاهز. باش ناخذو
التوكن لازم رابط عام يستقبل رد YouCan. الموقع على Railway عندو رابط
عام أصلاً — فنستعملوه بدل ما نطلبو من الأدمن يثبّت Python على جهازو.

التوكن يتخزّن فجدول store_settings، فيبقى بعد إعادة النشر.
"""

import os

import requests

from models import db, StoreSetting

# ⚠️ YouCan ما عندوش توثيق ثابت لمسارات OAuth — نجرّبو الاحتمالات
# المعروفة ونستعملو أول واحد يخدم، بدل ما نخمّنو واحد ونفشل بصمت.
AUTHORIZE_URLS = [
    "https://seller-area.youcan.shop/admin/oauth/authorize",
    "https://api.youcan.shop/oauth/authorize",
]
TOKEN_URLS = [
    "https://api.youcan.shop/oauth/token",
    "https://seller-area.youcan.shop/oauth/token",
]

SCOPES = "read-products read-categories"

# مفاتيح الإعدادات
SETTING_CLIENT_ID = "youcan_client_id"
SETTING_APP_SECRET = "youcan_app_key2"
SETTING_OAUTH = "youcan_oauth_ref"
SETTING_RENEW = "youcan_renew_ref"
SETTING_STATE = "youcan_oauth_state"


class YouCanError(Exception):
    """خطأ فربط YouCan — رسالتو تتعرض للأدمن مباشرة."""


# ═══════════════════════════════════════════
# الإعدادات
# ═══════════════════════════════════════════

def get_client_id():
    return (StoreSetting.get(SETTING_CLIENT_ID) or "").strip()


def get_client_secret():
    return (StoreSetting.get(SETTING_APP_SECRET) or "").strip()


def get_access_token():
    """
    التوكن المحفوظ.

    ⚠️ نفضّلو متغير البيئة إذا كان معمّر — باش الأدمن يقدر يتجاوز
    قاعدة البيانات بسرعة إذا احتاج.
    """
    from_env = os.environ.get("YOUCAN_ACCESS_TOKEN", "").strip()
    if from_env:
        return from_env
    return (StoreSetting.get(SETTING_OAUTH) or "").strip()


def is_connected():
    return bool(get_access_token())


def save_credentials(client_id, client_secret):
    StoreSetting.set(SETTING_CLIENT_ID, (client_id or "").strip())
    StoreSetting.set(SETTING_APP_SECRET, (client_secret or "").strip())
    db.session.commit()


def disconnect():
    """يمسح التوكن — الاعتمادات تبقى باش يقدر يعاود الربط بسرعة."""
    StoreSetting.set(SETTING_OAUTH, "")
    StoreSetting.set(SETTING_RENEW, "")
    db.session.commit()


# ═══════════════════════════════════════════
# مسار OAuth
# ═══════════════════════════════════════════

def build_authorize_url(redirect_uri, state, variant=0):
    """
    يبني رابط الموافقة.

    variant=0 هو المسار الأساسي، و1 بديل إذا الأول رد 404.
    """
    from urllib.parse import urlencode

    client_id = get_client_id()
    if not client_id:
        raise YouCanError("عمّر Client ID أولاً")

    query = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    })

    base = AUTHORIZE_URLS[min(variant, len(AUTHORIZE_URLS) - 1)]
    return f"{base}?{query}"


def exchange_code(code, redirect_uri):
    """
    يبدّل الـcode بـaccess token ويحفظو.

    يرجع dict فيه التوكن والتفاصيل. يرمي YouCanError مع سبب واضح
    إذا فشل — ماشي رسالة عامة.
    """
    client_id = get_client_id()
    client_secret = get_client_secret()

    if not client_id or not client_secret:
        raise YouCanError("Client ID ولا Secret ناقص")

    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }

    errors = []

    for url in TOKEN_URLS:
        try:
            response = requests.post(
                url, json=payload, timeout=25,
                headers={"Accept": "application/json"},
            )
        except Exception as e:
            errors.append(f"{url}: {e}")
            continue

        if response.status_code != 200:
            errors.append(f"{url}: {response.status_code} — {response.text[:200]}")
            continue

        try:
            data = response.json()
        except Exception:
            errors.append(f"{url}: الرد ماشي JSON — {response.text[:150]}")
            continue

        # ⚠️ الرد يجي بأشكال مختلفة حسب النسخة
        token = (
            data.get("access_token")
            or data.get("accessToken")
            or (data.get("data") or {}).get("access_token")
        )

        if not token:
            errors.append(f"{url}: ماكانش access_token — {str(data)[:200]}")
            continue

        refresh = (
            data.get("refresh_token")
            or (data.get("data") or {}).get("refresh_token")
            or ""
        )

        StoreSetting.set(SETTING_OAUTH, token)
        StoreSetting.set(SETTING_RENEW, refresh)
        db.session.commit()

        return {
            "token": token,
            "expires_in": data.get("expires_in"),
            "source": url,
        }

    raise YouCanError("ما قدرناش ناخذو التوكن — " + " | ".join(errors[:4]))


def _api_get(path, token):
    """طلب GET لـAdmin API مع ترجمة الأخطاء لرسائل مفهومة."""
    try:
        response = requests.get(
            f"https://api.youcan.shop/{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=25,
        )
    except Exception as e:
        raise YouCanError(f"ما قدرناش نتصلو بـYouCan: {e}")

    if response.status_code == 401:
        raise YouCanError("التوكن غير صالح ولا خلص — عاود الربط")
    if response.status_code == 403:
        raise YouCanError(
            "التوكن ما عندوش الصلاحيات. "
            "فعّل read-products و read-categories فالتطبيق وعاود الربط"
        )
    if response.status_code != 200:
        raise YouCanError(f"YouCan رد بـ{response.status_code}: {response.text[:200]}")

    try:
        return response.json()
    except Exception:
        raise YouCanError("رد YouCan ماشي JSON")


def count_all_products(token=None, max_pages=30):
    """
    يعدّ المنتجات **الفريدة** عبر كل الصفحات.

    ⚠️ ليش ما نثقوش فـmeta.total؟
    درسنا من ZR: الـAPI ممكن يعطي رقم غلط ولا يتجاهل الترقيم ويرجّع
    نفس الصفحة. الطريقة الوحيدة المضمونة: نمشيو صفحة بصفحة ونعدّو
    المعرّفات الفريدة، ونوقفو كي ما تجيش حاجة جديدة.

    يرجع dict فيه العدد والتفاصيل — مفيد للتشخيص.
    """
    token = token or get_access_token()
    if not token:
        raise YouCanError("ماكانش توكن محفوظ")

    seen = set()
    pages = 0
    reported_total = None
    repeated = False

    for page in range(1, max_pages + 1):
        data = _api_get(f"products?page={page}", token)
        pages += 1

        items = data.get("data") or data.get("items") or []

        meta = data.get("meta")
        if reported_total is None and isinstance(meta, dict):
            reported_total = meta.get("total")

        if not items:
            break

        fresh = [
            it for it in items
            if isinstance(it, dict) and it.get("id") and it["id"] not in seen
        ]
        for it in fresh:
            seen.add(it["id"])

        # ⚠️ صفر جداد = الـAPI يتجاهل `page` ويرجّع نفس الصفحة
        if not fresh:
            repeated = True
            break

        if len(items) < 5:
            break   # صفحة شبه فارغة = الأخيرة

    return {
        "count": len(seen),
        "pages": pages,
        "reported_total": reported_total,
        "repeated": repeated,
    }


def test_token():
    """
    يجرّب التوكن ويعدّ المنتجات الحقيقية.

    ⚠️ توكن محفوظ ماشي معناه توكن صالح: ممكن يخلص ولا الصلاحيات
    تتبدّل. أحسن نتأكدو درك من ما نكتشفو وقت الاستيراد.
    """
    result = count_all_products()

    return {
        "count": result["count"],
        "pages": result["pages"],
        "reported_total": result["reported_total"],
        "repeated": result["repeated"],
    }
