"""
يجيب Access Token من YouCan عبر OAuth — تشغيل مرة وحدة على جهازك.

⚠️ ليش هاد السكربت؟
تطبيق YouCan يعطيك Client ID و Client Secret فقط، ماشي توكن جاهز.
باش تاخذ التوكن، لازم:
  1. تثبّت التطبيق على متجرك (موافقة يدوية فالمتصفح)
  2. YouCan يرجّعلك «code» مؤقت
  3. تبدّلو بـAccess Token

هاد السكربت يدير الثلاثة تلقائيًا: يفتح المتصفح، يلقط الـcode
بسيرفر محلي صغير، ويطبعلك التوكن جاهز.

═══════════════ قبل التشغيل ═══════════════

1) فـpartners.youcan.shop ← تطبيقك ← Overview:
   حط فـ«Allowed redirect URLs» بالضبط:

       http://localhost:8123/callback

2) شغّل:  python get_youcan_token.py

3) عمّر CLIENT_ID و CLIENT_SECRET تحت (ولا فـ.env)
"""

import http.server
import os
import secrets
import threading
import urllib.parse
import webbrowser

import requests
from dotenv import load_dotenv

load_dotenv()

# ⚙️ عمّرهم هنا، ولا حطّهم فملف .env
CLIENT_ID = os.environ.get("YOUCAN_CLIENT_ID", "").strip() or "3042"
CLIENT_SECRET = os.environ.get("YOUCAN_CLIENT_SECRET", "").strip() or ""

# ⚠️ المنفذ يجي من ملف .bat (يسأل المستخدم)، وإلا 8123
PORT = int(os.environ.get("YOUCAN_CALLBACK_PORT", "8123"))
REDIRECT_URI = f"http://localhost:{PORT}/callback"

# الصلاحيات اللي حدّدتها فالتطبيق
SCOPES = "read-products read-categories"

# ⚠️ YouCan ما عندوش توثيق ثابت لمسارات OAuth، ونسخهم تبدّلت.
# نجرّبو الاحتمالات المعروفة ونستعملو أول واحد يخدم.
AUTHORIZE_URLS = [
    "https://seller-area.youcan.shop/admin/oauth/authorize",
    "https://api.youcan.shop/oauth/authorize",
]
TOKEN_URLS = [
    "https://api.youcan.shop/oauth/token",
    "https://seller-area.youcan.shop/oauth/token",
]

# نتشاركو النتيجة بين السيرفر والخيط الرئيسي
_result = {"code": None, "error": None, "state": None}
_done = threading.Event()

STATE = secrets.token_urlsafe(16)   # حماية من CSRF


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """يلقط الـcode اللي يرجّعو YouCan بعد الموافقة."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        _result["code"] = (params.get("code") or [None])[0]
        _result["state"] = (params.get("state") or [None])[0]
        _result["error"] = (params.get("error_description")
                            or params.get("error") or [None])[0]

        ok = bool(_result["code"])
        message = (
            "تمام! رجع للترمينال — التوكن غادي يبان تماك."
            if ok else
            f"فشل: {_result['error'] or 'ماكانش code'}"
        )

        body = f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<title>YouCan</title>
<style>
 body {{ font-family: system-ui, sans-serif; background:#FBF6EE; color:#221C2B;
        display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
 .box {{ background:#fff; padding:40px; border-radius:16px; text-align:center;
         box-shadow:0 10px 30px -12px rgba(0,0,0,.25); max-width:420px; }}
 .mark {{ font-size:44px; }}
</style></head>
<body><div class="box">
  <div class="mark">{'✅' if ok else '🔴'}</div>
  <h2>{message}</h2>
  <p>تقدر تسكّر هاد الصفحة.</p>
</div></body></html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

        _done.set()

    def log_message(self, *args):
        pass   # نسكتو سجلات السيرفر — ما تهمناش


def exchange_code(code):
    """يبدّل الـcode بـaccess token. يجرّب المسارات المحتملة."""
    payload = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }

    errors = []
    for url in TOKEN_URLS:
        try:
            r = requests.post(url, json=payload, timeout=25,
                              headers={"Accept": "application/json"})
        except Exception as e:
            errors.append(f"{url}: {e}")
            continue

        if r.status_code == 200:
            try:
                data = r.json()
            except Exception:
                errors.append(f"{url}: رد ماشي JSON")
                continue

            token = (data.get("access_token") or data.get("accessToken")
                     or (data.get("data") or {}).get("access_token"))
            if token:
                return token, data, None

            errors.append(f"{url}: ماكانش access_token فالرد — {str(data)[:200]}")
        else:
            errors.append(f"{url}: {r.status_code} — {r.text[:200]}")

    return None, None, errors


def main():
    print("=" * 56)
    print("  🔑 الحصول على توكن YouCan")
    print("=" * 56)

    if not CLIENT_SECRET:
        print("\n🔴 CLIENT_SECRET فارغ.")
        print("   حطّو فملف .env هكذا:")
        print("      YOUCAN_CLIENT_ID=3042")
        print("      YOUCAN_CLIENT_SECRET=xxxxxxxx")
        print("   ولا عمّرو مباشرة فبداية هاد الملف.")
        return

    # نشغّلو السيرفر المحلي
    server = http.server.HTTPServer(("localhost", PORT), CallbackHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"\n👂 نستنّاو على {REDIRECT_URI}")

    query = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": STATE,
    })
    auth_url = f"{AUTHORIZE_URLS[0]}?{query}"

    print("\n🌐 نفتحو المتصفح — وافق على تثبيت التطبيق فمتجرك.")
    print(f"\n   إذا ما تفتحش وحدو، افتح هاد الرابط يدويًا:\n   {auth_url}\n")

    # الرابط البديل — مفيد إذا الأول رد 404
    print(f"   (بديل إذا الأول ما خدمش:\n    {AUTHORIZE_URLS[1]}?{query} )\n")

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    print("⏳ نستنّاو الموافقة... (3 دقائق)")
    if not _done.wait(timeout=180):
        print("\n🔴 الوقت خلص. جرّب مرة أخرى، ولا افتح الرابط يدويًا.")
        server.shutdown()
        return

    server.shutdown()

    if not _result["code"]:
        print(f"\n🔴 ما وصلناش code: {_result['error']}")
        return

    # ⚠️ نتأكدو أن الرد جاي من نفس الطلب اللي بعثناه
    if _result["state"] != STATE:
        print("\n🔴 state ماشي مطابق — نوقفو (احتمال طلب مزوّر).")
        return

    print("\n✅ وصلنا الـcode. نبدّلوه بتوكن...")

    token, data, errors = exchange_code(_result["code"])

    if not token:
        print("\n🔴 ما قدرناش ناخذو التوكن:")
        for e in (errors or []):
            print(f"   • {e}")
        print("\n   ابعث هاد الرسائل لكلود ونصلحو المسار.")
        return

    print("\n" + "=" * 56)
    print("  ✅ التوكن جاهز")
    print("=" * 56)
    print(f"\nYOUCAN_ACCESS_TOKEN={token}\n")

    if data.get("expires_in"):
        hours = int(data["expires_in"]) // 3600
        print(f"⏱️  يخلص بعد ~{hours} ساعة")
    if data.get("refresh_token"):
        print(f"🔄 refresh_token={data['refresh_token']}")

    print("\n📋 الخطوة الجاية:")
    print("   حط السطر فوق فمتغيرات Railway، وبعدها شغّل:")
    print("      python import_from_youcan.py --wipe")

    # نحفظوه محليًا كذلك باش ما يضيعش
    try:
        with open("youcan_token.txt", "w", encoding="utf-8") as f:
            f.write(f"YOUCAN_ACCESS_TOKEN={token}\n")
        print("\n💾 تحفظ كذلك فـyoucan_token.txt")
        print("   ⚠️ ما ترفعوش لـGitHub — فيه مفتاح سري.")
    except Exception:
        pass


if __name__ == "__main__":
    main()
