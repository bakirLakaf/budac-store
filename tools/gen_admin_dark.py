"""يولّد static/css/admin-dark.css من admin.css (الدارك مود تاع لوحة التحكم).

الفكرة: admin.css فيه ~270 استعمال لمتغيرات --a-* (تتبدّل وحدها فالدارك) و~60 لون ثابت (أبيض/بيج…).
هاد السكريبت يقرا كل قاعدة فيها لون فاتح ثابت (خلفية/حد/نص) ويولّد لها نسخة داكنة تحت html[data-theme=dark].

الاستعمال (من جذر المشروع):   python tools/gen_admin_dark.py
⚠️ كل ما تبدّل admin.css بألوان جديدة، عاود شغّلو. الملف المولَّد ما يتعدّلش بيدك (استعمل admin-dark-extra.css).
"""
import colorsys
import pathlib
import re
import sys

import tinycss2

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "static" / "css" / "admin.css"
OUT = ROOT / "static" / "css" / "admin-dark.css"

HEX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
LAV = "#D9CCF5"  # بنفسجي فاتح بديل لـ var(--a-primary) كنص/حد على خلفية داكنة


def rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def hls(h):
    r, g, b = rgb(h)
    return colorsys.rgb_to_hls(r, g, b)  # (h, l, s)


def to_hex(r, g, b):
    return "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))


def tint(h):
    """لون فاتح مشبّع (نجاح/خطأ/تحذير…) → نسخة داكنة شفافة بنفس الصبغة."""
    hh, l, s = hls(h)
    r, g, b = colorsys.hls_to_rgb(hh, 0.5, max(s, 0.6))
    return f"rgba({round(r*255)},{round(g*255)},{round(b*255)},0.20)"


def dark_bg(h):
    hh, l, s = hls(h)
    if l < 0.78:
        return h  # خلفية متوسطة/داكنة: نخلّيوها
    if s > 0.35 and l < 0.97 and not (0.08 < hh < 0.17 and s < 0.6):  # لون دلالي (أخضر/أحمر/أزرق…)
        return tint(h)
    if (0.08 < hh < 0.17) and s >= 0.6 and l < 0.97:  # أصفر/ذهبي فاتح
        return tint(h)
    return "var(--a-surface)" if l > 0.965 else "var(--a-surface-2)"


def dark_text(h):
    hh, l, s = hls(h)
    if l > 0.6:
        return h
    if s < 0.35 or 0.7 < hh < 0.85:  # رمادي/بنفسجي غامق → ألوان النص العامة
        if 0.7 < hh < 0.85 and l < 0.3:
            return LAV
        return "var(--a-text)" if l < 0.3 else "var(--a-text-soft)"
    r, g, b = colorsys.hls_to_rgb(hh, 0.68, min(s, 0.75))  # لون دلالي غامق → نفتّحوه
    return to_hex(r, g, b)


def dark_border(h):
    return "var(--a-border)" if hls(h)[1] > 0.7 else h


def sub(value, fn):
    return HEX.sub(lambda m: fn(m.group(0)) if len(m.group(0)) in (4, 7) else m.group(0), value)


def convert(name, value):
    v = value.strip()
    low = v.lower()
    if name in ("background", "background-color", "background-image"):
        v2 = sub(v, dark_bg)
        v2 = re.sub(r"\bwhite\b", "var(--a-surface)", v2) if name != "background-image" else v2
        return v2 if v2 != v else None
    if name == "color":
        if "var(--a-primary" in low:
            return LAV
        v2 = sub(v, dark_text)
        return v2 if v2 != v else None
    if name.startswith("border") or name == "outline":
        v2 = v.replace("var(--a-primary-light)", LAV).replace("var(--a-primary)", LAV)
        v2 = sub(v2, dark_border)
        return v2 if v2 != v else None
    return None


def walk(rules, out):
    for rule in rules:
        if rule.type == "qualified-rule":
            sel = tinycss2.serialize(rule.prelude).strip()
            if not sel or "@" in sel:
                continue
            decls = []
            for d in tinycss2.parse_declaration_list(rule.content, skip_comments=True, skip_whitespace=True):
                if d.type != "declaration":
                    continue
                nv = convert(d.lower_name, tinycss2.serialize(d.value))
                if nv:
                    decls.append(f"    {d.name}: {nv} !important;" if "var(--a-primary)" not in nv else f"    {d.name}: {nv};")
            if decls:
                sels = ",\n".join("html[data-theme=\"dark\"] " + s.strip() for s in sel.split(",") if s.strip())
                out.append(f"{sels} {{\n" + "\n".join(decls) + "\n}\n")
        elif rule.type == "at-rule" and rule.lower_at_keyword in ("media", "supports") and rule.content:
            walk(tinycss2.parse_rule_list(rule.content, skip_comments=True, skip_whitespace=True), out)


def main():
    rules = tinycss2.parse_stylesheet(SRC.read_text(encoding="utf-8"), skip_comments=True, skip_whitespace=True)
    out = []
    walk(rules, out)
    header = (ROOT / "tools" / "admin-dark-base.css").read_text(encoding="utf-8")
    OUT.write_text(header + "\n/* ═══ مولَّد آليًا من admin.css — python tools/gen_admin_dark.py ═══ */\n\n" + "\n".join(out), encoding="utf-8")
    print(f"{len(out)} قاعدة → {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
