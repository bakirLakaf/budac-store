"""
ملكية الطلبية — من يحق له يشوف طلبية معيّنة؟

════════════════════════════════════════════════════════════════
المبدأ الأساسي:
────────────────────────────────────────────────────────────────
    رقم الطلبية ≠ صلاحية

رقم الطلبية يتقال فالتلفون، يتكتب فرسالة واتساب، ويبان فرابط.
هو **معرّف**، ماشي كلمة سر. أي نظام يعتبر معرفة الرقم دليل ملكية
هو نظام مكسور — حتى لو الرقم طويل.

عندنا طريقتان مشروعتان للإثبات:
  1. **الجلسة**: الزبون اللي دار الطلبية درك، فنفس المتصفح.
     تستعمل فصفحة النجاح وصفحة الشكر (بعد الطلب مباشرة).
  2. **الرقم + الهاتف معًا**: للرجوع بعدين (صفحة «طلبياتي» والتتبّع).
     الاثنين لازمين — الرقم وحدو ما يكفيش.

⚠️ كان `/lp/<slug>/thanks` يعتمد على الرقم وحدو، فأي واحد يجرّب
الأرقام ويقرا مبالغ كل طلبيات المتجر. هاد الملف يوحّد القاعدة باش
ما تتنساش فمسار جديد.
════════════════════════════════════════════════════════════════
"""

from flask import session

# ⚠️ نخزّنو آخر الطلبيات، ماشي وحدة برك: الزبون يقدر يدير طلبية من
# صفحة هبوط ثم وحدة من المتجر، وما نحبّوش الثانية تمسح الأولى ويطيح
# عليه رابط صفحة الشكر لو رجع للوراء.
SESSION_KEY = "my_order_numbers"
LEGACY_KEY = "last_order_number"

# سقف: الجلسة كوكي موقّع، وكوكي كبير يتقصّ. 10 كافية بزاف.
MAX_REMEMBERED = 10


def remember_order(order_number):
    """
    يسجّل فالجلسة أن هاد المتصفح دار هاد الطلبية.
    يتنادى مرة وحدة، بعد نجاح إنشاء الطلبية.
    """
    if not order_number:
        return

    numbers = [n for n in session.get(SESSION_KEY, []) if n != order_number]
    numbers.insert(0, order_number)
    session[SESSION_KEY] = numbers[:MAX_REMEMBERED]

    # ⚠️ نحافظو على المفتاح القديم: زبائن عندهم جلسة مفتوحة درك
    # وواقفين فصفحة النجاح — ما نحبّوش الرابط يطيح عليهم بعد النشر.
    session[LEGACY_KEY] = order_number
    session.modified = True


def session_owns_order(order_number):
    """
    واش هاد المتصفح هو اللي دار هاد الطلبية؟

    يرجّع True/False. ما يلمسش قاعدة البيانات — التحقق من وجود
    الطلبية مسؤولية المستدعي.
    """
    if not order_number:
        return False

    if order_number in session.get(SESSION_KEY, []):
        return True

    # توافق مع الجلسات القديمة
    return session.get(LEGACY_KEY) == order_number


def find_order_by_phone(order_number, phone):
    """
    يلقى طلبية بالرقم **مع** الهاتف — الطريقة الثانية للإثبات.

    ⚠️ نقارنو الأرقام برك (بلا مسافات ولا +213 ولا شرطات) لأن الزبون
    يقدر يكتب نفس الرقم بأشكال مختلفة. نقارنو آخر 9 أرقام باش
    «0666255200» و«+213666255200» يتوافقو.

    يرجّع Order أو None. رسالة الرفض وحدة عند المستدعي فكل الحالات،
    باش ما نكشفوش واش الرقم موجود.
    """
    from models import Order

    number = (order_number or "").strip().upper()
    phone = (phone or "").strip()
    if not number or not phone:
        return None

    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        return None

    order = Order.query.filter_by(order_number=number).first()
    if order is None:
        return None

    saved = "".join(ch for ch in (order.customer_phone or "") if ch.isdigit())
    if not saved or saved[-9:] != digits[-9:]:
        return None

    return order
