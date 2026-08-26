"""
أدوات مساعدة عامة للمشروع.
"""

import html
import re
from html.parser import HTMLParser


class _HTMLToTextParser(HTMLParser):
    """
    يحول HTML لنص عادي مقروء، ويحافظ على فقرات وقوائم منظمة
    (بلا ما يحتاج مكتبة خارجية كيما BeautifulSoup).
    """

    BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "br", "li"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.list_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.parts.append("\n• ")
        elif tag == "br":
            self.parts.append("\n")
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"):
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        raw = "".join(self.parts)
        # نبدلو المسافات المتتالية بمسافة وحدة، ونحافظو على الأسطر الجديدة
        lines = [line.strip() for line in raw.split("\n")]
        lines = [re.sub(r"\s+", " ", line) for line in lines if line.strip()]
        return "\n".join(lines).strip()


def html_to_text(raw_html):
    """
    يحول نص HTML (وصف منتج مثلاً) لنص عادي واضح ومنظم بفقرات وقوائم.
    يرجع سلسلة فارغة إذا الإدخال فارغ.
    """
    if not raw_html:
        return ""

    # أحيانًا الوصف يجي مُرمّز مرتين (HTML entities) — نفك الترميز أولاً
    decoded = html.unescape(raw_html)

    parser = _HTMLToTextParser()
    try:
        parser.feed(decoded)
    except Exception:
        # فحالة فشل التحليل، نرجعو غير تنظيف بسيط بالـregex كحل احتياطي
        text = re.sub(r"<[^>]+>", " ", decoded)
        return re.sub(r"\s+", " ", text).strip()

    return parser.get_text()


def slugify(text, fallback="item"):
    """
    يحول نص لرابط صالح (slug).

    ⚠️ ملاحظة مهمة للعربية: الحروف العربية ما تتحولش لحروف لاتينية هنا، وregex
    العادي \\w يقبلهم فوضع Unicode — فالنتيجة تبقى عربية (مثلاً "أشرطة-المرايا").
    هذا مقبول تمامًا فالروابط الحديثة (المتصفح يرمّزهم تلقائيًا)، لكن إذا حبيت
    روابط لاتينية فقط، حدد الـslug يدويًا من لوحة التحكم.
    """
    if not text:
        return fallback

    cleaned = re.sub(r"[^\w\s-]", "", str(text), flags=re.UNICODE).strip().lower()
    cleaned = re.sub(r"[\s_-]+", "-", cleaned)
    cleaned = cleaned.strip("-")

    return cleaned or fallback
