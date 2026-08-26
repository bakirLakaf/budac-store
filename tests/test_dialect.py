# -*- coding: utf-8 -*-
"""
حارس اللهجة — يمنع الكلمات المغربية من الرجوع للكود والموقع.

🔴 علاش هاد الملف موجود؟
تسرّبات مغربية دخلت للنصوص اللي يشوفها الزبون («ديال»، «شنو»،
«غادي»، «كيحبوه»). المتجر جزائري، والزبون الجزائري يحسّ بالفرق
على طول. الكلمة الوحدة الغالطة تخسّر الثقة.

الفحص يمشي على كل ملف .py و.html و.js و.css و.md، ويطيح إذا
لقى أي كلمة من القائمة — فالنص الظاهر أو حتى فالتعليقات.

التشغيل: python tests/test_dialect.py
"""
import os, re, sys, pathlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# الكلمة المغربية → المقابل الجزائري
# ⚠️ الحدود مهمة: «فين» ممنوعة لكن «فينا» و«مختلفين» عاديين.
BANNED = [
    (r"ديال",                                        "تاع"),
    (r"شنو",                                         "واش"),
    (r"دابا",                                        "درك"),
    (r"غادي",                                        "راح"),
    (r"بغيت",                                        "حبيت"),
    (r"(?<![\w\u0621-\u064A])بغ[اى](?![\w\u0621-\u064A])", "حب / حاب"),
    (r"خاصك",                                        "لازمك"),
    (r"(?<![\w\u0621-\u064A])فين(?![\w\u0621-\u064A])",    "وين"),
    (r"لفين(?![\w\u0621-\u064A])",                   "لوين"),
    (r"واخا",                                        "صح / مليح"),
    (r"مزيان",                                       "مليح"),
    (r"عافاك",                                       "من فضلك"),
    (r"زوين",                                        "شباب"),
    # سابقة الفعل المغربية: كيحب، كيقول، كتشوف…
    (r"(?<![\w\u0621-\u064A])ك[يت](حب|قول|شوف|خدم|بان|دير|عرف|جي)",
     "بلا سابقة «كي»"),
]

EXTS = {".py", ".html", ".js", ".css", ".md"}
SKIP = {"__pycache__", ".git", "node_modules", "venv"}

# ⚠️ هاد الملف نفسو فيه الكلمات الممنوعة (هي القائمة) — نتجاهلوه
SELF = os.path.basename(__file__)

PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name
          + ("  → " + str(detail) if not ok and detail != "" else ""))


def main():
    root = pathlib.Path(os.path.join(os.path.dirname(__file__), ".."))
    hits = {}

    for path in root.rglob("*"):
        if path.suffix not in EXTS:
            continue
        if any(part in SKIP for part in path.parts):
            continue
        if path.name == SELF:
            continue

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue

        for num, line in enumerate(lines, 1):
            for pattern, better in BANNED:
                if re.search(pattern, line):
                    word = re.search(pattern, line).group()
                    rel = path.relative_to(root)
                    hits.setdefault(word, []).append(
                        f"{rel}:{num} (استعمل: {better})"
                    )

    print("\n" + "=" * 60)
    print("حارس اللهجة — ماكانش كلمات مغربية")
    print("=" * 60)

    for pattern, better in BANNED:
        label = pattern.split("(")[0].strip("\\b") or pattern
        found = [w for w in hits if re.fullmatch(pattern, w)]
        detail = hits[found[0]][:3] if found else ""
        check(f"«{label}» ماشي موجودة", not found, detail)

    print("\n" + "=" * 60)
    print(f"نجح: {len(PASSED)}  ·  فشل: {len(FAILED)}")
    print("=" * 60)

    if hits:
        print("\nالمواضع:")
        for word, places in hits.items():
            print(f"\n  «{word}» — {len(places)} موضع")
            for p in places[:10]:
                print(f"    · {p}")

    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
