# -*- coding: utf-8 -*-
"""
اختبار **تشغيل** JavaScript، ماشي غير فحص نحوي.

⚠️ علاش هذا موجود؟
`node --check` يقول أن الكود سليم نحويًا، لكن ما يقولش أنه **يشتغل**.
كود رفع الصور كان سليمًا نحويًا لكن محشورًا داخل IIFE يخرج بـ`return`
مبكّر — فما كان يسجّل حتى مستمع. `--check` عدّاه، والزبون هو اللي
اكتشف العطل.

هنا نشغّلو الكتلة فعلاً فبيئة DOM وهمية ونتأكد أنها سجّلت مستمعاتها.

⚠️ يحتاج `node` مثبّت. إذا ماكانش، الاختبار يتجاوز بلا فشل.

التشغيل: python tests/test_js.py
"""
import os, re, sys, shutil, subprocess, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEMPLATES = os.path.join(os.path.dirname(__file__), "..", "templates")

PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name + ("  " + detail if not ok else ""))


# بيئة DOM وهمية — تعدّ المستمعات اللي يسجّلها الكود
DOM_SHIM = """
var listeners = {};

function El() {
    this.dataset = {};
    this.style = {};
    this.classList = { add: function () {}, remove: function () {},
                       contains: function () { return false; } };
    // ⚠️ `[]` بدل نص فارغ: القوالب تدير `JSON.parse(el.textContent)`
    // لقراءة بيانات مضمّنة. نص فارغ يطيّح المحاكاة بلا سبب حقيقي.
    this.textContent = '[]';
    this.value = '';
    this.hidden = true;
    this.innerHTML = '';
    this.files = [];
    this.disabled = false;
    this.addEventListener = function (t) {
        listeners[t] = (listeners[t] || 0) + 1;
    };
    this.querySelector = function () { return new El(); };
    this.querySelectorAll = function () { return []; };
    this.appendChild = function () {};
    this.closest = function () { return new El(); };
    this.blur = function () {};
    this.focus = function () {};
    this.setAttribute = function () {};
    this.getAttribute = function () { return '[]'; };
    this.submit = function () {};
    this.insertBefore = function () {};
    this.removeChild = function () {};
    this.remove = function () {};
    this.parentNode = this;
    this.scrollIntoView = function () {};
    this.getBoundingClientRect = function () {
        return { top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 };
    };
}

// ⚠️ المتصفح يرجّع `null` للعنصر اللي ماكانش. محاكاة ترجّع عنصرًا
// لأي محدّد تخبّي أخطاء حقيقية: كود يقول `if (!el) return;` يبان
// أنه يشتغل وهو ما يشتغلش فالواقع. نعرّفو المحدّدات الموجودة فعلاً.
globalThis.__KNOWN__ = [];

function known(sel) {
    if (!globalThis.__KNOWN__.length) return true;   // بلا قائمة: كلش موجود
    return globalThis.__KNOWN__.some(function (k) { return sel.indexOf(k) !== -1; });
}

globalThis.document = {
    querySelector: function (sel) { return known(sel) ? new El() : null; },
    querySelectorAll: function () { return []; },
    getElementById: function (id) { return known(id) ? new El() : null; },
    createElement: function () { return new El(); },
    addEventListener: function (t) {
        listeners['doc:' + t] = (listeners['doc:' + t] || 0) + 1;
    },
    body: new El(),
    head: new El(),
    documentElement: new El(),
    // مقتطفات الطرف الثالث (بيكسل فيسبوك/تيك توك) تنادي هاذو
    getElementsByTagName: function () { return [new El()]; },
    getElementsByClassName: function () { return [new El()]; },
    cookie: '',
    readyState: 'complete',
};

globalThis.window = {
    addEventListener: function (t) { listeners['win:' + t] = 1; },
    getCsrf: function () { return 'tok'; },
    document: globalThis.document,
    innerWidth: 1280,
    innerHeight: 800,
    scrollY: 0,
    matchMedia: function () {
        return { matches: false, addEventListener: function () {} };
    },
};

globalThis.getCsrf = function () { return 'tok'; };
globalThis.location = { reload: function () {}, href: '' };
globalThis.alert = function () {};
globalThis.confirm = function () { return true; };
globalThis.FormData = function () { this.append = function () {}; };
globalThis.XMLHttpRequest = function () {
    this.open = function () {};
    this.send = function () {};
    this.setRequestHeader = function () {};
    this.addEventListener = function () {};
    this.upload = { addEventListener: function () {} };
};
globalThis.fetch = function () {
    return { then: function () { return this; }, catch: function () { return this; } };
};

// ⚠️ المؤقتات ما تتنفّذش فالاختبار — بلاها، سلايدر بـsetInterval
// يخلّي node يعلّق للأبد.
globalThis.setTimeout = function () { return 0; };
globalThis.setInterval = function () { return 0; };
globalThis.clearTimeout = function () {};
globalThis.clearInterval = function () {};
globalThis.requestAnimationFrame = function () { return 0; };
globalThis.IntersectionObserver = function () {
    this.observe = function () {}; this.disconnect = function () {};
};
globalThis.MutationObserver = function () {
    this.observe = function () {}; this.disconnect = function () {};
};
globalThis.ResizeObserver = function () {
    this.observe = function () {}; this.disconnect = function () {};
};
globalThis.navigator = { clipboard: { writeText: function () {} }, share: null };
globalThis.localStorage = {
    getItem: function () { return '[]'; }, setItem: function () {},
    removeItem: function () {},
};
globalThis.sessionStorage = globalThis.localStorage;
"""


def strip_jinja(code):
    """
    يشيل تعابير Jinja ويحطّ قيمًا صالحة مكانها.

    ⚠️ `{{ x }}` كثير ما يكون **داخل** علامات تنصيص فالكود:
    `fbq('init', '{{ pixel }}')`. لو حطّينا `'x'` يولّي `''x''`
    وهذا خطأ نحوي. نفحصو الجار: إذا التعبير محاط بتنصيص، نحطّو
    نصًا عاديًا؛ إذا لا، نحطّو قيمة JS صالحة.
    """
    def repl(m):
        before = code[m.start() - 1:m.start()]
        after = code[m.end():m.end() + 1]
        if before in ("'", '"', "`") and after in ("'", '"', "`"):
            return "x"          # داخل تنصيص أصلاً
        return "'x'"

    code = re.sub(r"\{\{.*?\}\}", repl, code, flags=re.S)
    code = re.sub(r"\{%.*?%\}", "", code, flags=re.S)
    code = re.sub(r"\{#.*?#\}", "", code, flags=re.S)
    return code


def run_block(code, assertions):
    """يشغّل كتلة JS فبيئة وهمية ويرجّع (نجح، الخرج)."""
    tmp = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                      encoding="utf-8")
    tmp.write(DOM_SHIM + strip_jinja(code) + assertions)
    tmp.close()
    try:
        r = subprocess.run(["node", tmp.name], capture_output=True,
                           text=True, timeout=15)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, "علّق أكثر من 15 ثانية (حلقة لا نهائية؟)"
    finally:
        os.unlink(tmp.name)


def blocks_of(path):
    """
    يرجّع كتل <script> اللي فيها **كود** فقط.

    ⚠️ نستثنيو `src=` (ملفات خارجية) و`type="application/ld+json"`
    (بيانات منظمة لجوجل — JSON ماشي JavaScript، node يرفضها).
    """
    src = open(path, encoding="utf-8").read()
    out = []
    for m in re.finditer(r"<script([^>]*)>(.*?)</script>", src, re.S):
        attrs = m.group(1)
        if "src=" in attrs or "ld+json" in attrs:
            continue
        out.append(m.group(2))
    return out


def main():
    if not shutil.which("node"):
        print("⏭️  node ماشي مثبّت — تجاوزنا اختبار تشغيل JS")
        return 0

    print("=" * 60)
    print("تشغيل كتل JavaScript فبيئة DOM وهمية")
    print("=" * 60)

    form = os.path.join(TEMPLATES, "admin", "product_form.html")
    blks = blocks_of(form)

    # ---------- رافع الصور ----------
    up = [b for b in blks if "imagePicker" in b]
    check("كتلة الرافع موجودة مرة وحدة", len(up) == 1, f"{len(up)} كتلة")

    if up:
        # ⚠️ نقولو للمحاكاة بالضبط أي عناصر موجودة فالقالب. لو الكود
        # يدوّر على محدّد ماشي موجود ويخرج، الاختبار يفشل — وهذا
        # بالضبط الخلل اللي عدّى قبل.
        real = """
            globalThis.__KNOWN__ = ['.uploader', 'imagePicker',
                'uploadProgress', 'uploadBarFill', 'uploadStatus',
                'uploadList', 'productForm'];
        """
        ok, out = run_block(real + up[0], """
            if (!listeners['change'])
                { console.error('ما سجّلش مستمع change'); process.exit(1); }
            if (!listeners['win:beforeunload'])
                { console.error('ما سجّلش beforeunload'); process.exit(1); }
        """)
        check("الرافع يسجّل change + beforeunload", ok, out[:200])

    # ---------- اسم الموديل ----------
    lab = [b for b in blks if "vlabel-save" in b]
    if lab:
        ok, out = run_block(lab[0], """
            if (!listeners['doc:click'])
                { console.error('ما سجّلش click'); process.exit(1); }
        """)
        check("محرّر اسم الموديل يسجّل مستمعاتو", ok, out[:200])

    # ---------- سعر الموديل ----------
    pri = [b for b in blks if "vprice-save" in b]
    if pri:
        ok, out = run_block(pri[0], "")
        check("محرّر سعر الموديل يشتغل بلا خطأ", ok, out[:200])

    # ---------- كل كتل كل القوالب تشتغل بلا استثناء ----------
    print("\n" + "=" * 60)
    print("كل كتل JS فكل القوالب")
    print("=" * 60)

    total, broken = 0, []
    for root, _, files in os.walk(TEMPLATES):
        for f in sorted(files):
            if not f.endswith(".html"):
                continue
            path = os.path.join(root, f)
            for i, b in enumerate(blocks_of(path)):
                if not b.strip():
                    continue

                # ⚠️ مقتطفات الطرف الثالث (بيكسل فيسبوك/تيك توك، جوجل)
                # مكتوبة للمتصفح ويعرّفو دوالهم على `window` — وفـnode
                # `window` ماشي النطاق العام، فيفشلو بلا ما يكون فيهم
                # عيب. ماشي كودنا، نتجاوزوهم.
                if any(k in b for k in ("fbq(", "ttq.", "gtag(", "dataLayer")):
                    continue

                total += 1
                ok, out = run_block(b, "")
                if not ok:
                    rel = os.path.relpath(path, TEMPLATES)
                    broken.append(f"{rel}#{i}: {out.splitlines()[0][:80]}"
                                  if out else f"{rel}#{i}")

    check(f"{total} كتلة تشتغل بلا خطأ", not broken, str(broken[:3]))

    print("\n" + "=" * 60)
    print(f"نجح: {len(PASSED)}/{len(PASSED) + len(FAILED)}")
    if FAILED:
        print("\n🔴 فشل:")
        for f in FAILED:
            print("  •", f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
