"""v188: واتساب سريع — رقم + أي رسالة (بلا طلبية)."""
import pytest

from services import wa_quick


# ───────────── الرقم ─────────────
@pytest.mark.parametrize("raw,expected", [
    ("0555123456", "213555123456"),
    ("555123456", "213555123456"),
    ("0555 12 34 56", "213555123456"),
    ("+213 555 12 34 56", "213555123456"),
    ("+2130555123456", "213555123456"),       # صفر زايد بعد +213
    ("00213555123456", "213555123456"),
    ("213555123456", "213555123456"),
    ("٠٥٥٥١٢٣٤٥٦", "213555123456"),           # أرقام عربية
    ("0770-12-34-56", "213770123456"),
    ("+33612345678", "33612345678"),           # رقم أجنبي يتقبّل
])
def test_parse_phone_ok(raw, expected):
    assert wa_quick.parse_phone(raw) == (expected, "")


@pytest.mark.parametrize("raw", ["", "   ", "abc", "05551", "0555123456789", "+33", "0212345678"])
def test_parse_phone_errors(raw):
    phone, err = wa_quick.parse_phone(raw)
    assert phone is None and err


def test_landline_gets_specific_message():
    _, err = wa_quick.parse_phone("0212345678")   # رقم ثابت (الجزائر العاصمة)
    assert "ثابت" in err


def test_pretty_phone():
    assert wa_quick.pretty_phone("213555123456") == "0555 12 34 56"
    assert wa_quick.pretty_phone("33612345678") == "+33612345678"


# ───────────── النص ─────────────
def test_check_text_rules():
    assert wa_quick.check_text("سلام") == ""
    assert "اكتب" in wa_quick.check_text("   ")
    assert "طويلة" in wa_quick.check_text("ا" * (wa_quick.MAX_LEN + 1))
    assert "إيموجي" in wa_quick.check_text("شكرا 😀")        # 4 بايت
    assert "إيموجي" in wa_quick.check_text("تم ✅")          # ملوّن (BMP لكن مشكوك)
    assert wa_quick.check_text("تم ✓ شكرا ✿ ★") == ""       # الرموز النصّية مقبولة


def test_render_message_with_and_without_name():
    text = "{hello}\n\nشوف {link} {heart}"
    with_name = wa_quick.render_message(text, "أمين", "https://x.dz")
    assert with_name.startswith("سلام أمين،\nمعاك BUDAC.") and "https://x.dz" in with_name and "✿" in with_name
    no_name = wa_quick.render_message(text, "", "https://x.dz")
    assert no_name.startswith("سلام،\nمعاك BUDAC.")


def test_render_message_cleans_leftover_name_placeholder():
    assert wa_quick.render_message("سلام {name}،\nواش راك", "", "").startswith("سلام،")


def test_every_preset_is_sendable():
    """كل النماذج الجاهزة لازم تعدّي التحقق (بلا إيموجي ملوّن) وتتعوّض متغيّراتها."""
    ctx = wa_quick.ui_context(link="https://budacdz.com")
    assert len(ctx["presets"]) >= 8
    for p in ctx["presets"]:
        ok, payload = wa_quick.prepare("0555123456", p["text"], "أمين", "https://budacdz.com")
        assert ok, (p["key"], payload)
        assert "{" not in payload["text"], p["key"]          # ما بقاش متغيّر ما تعوّضش
        assert payload["url"].startswith("https://wa.me/213555123456?text=")


def test_prepare_url_is_fully_encoded():
    ok, payload = wa_quick.prepare("0555123456", "سلام\nواش & راك؟", "", "")
    assert ok and "\n" not in payload["url"] and "&" not in payload["url"].split("?text=")[1]
    assert payload["phone_display"] == "0555 12 34 56"


def test_prepare_errors():
    assert wa_quick.prepare("", "سلام")[0] is False
    assert wa_quick.prepare("0555123456", "")[0] is False
    ok, payload = wa_quick.prepare("0555123456", "تم ✅")
    assert not ok and "إيموجي" in payload["error"]


# ───────────── المسار والواجهة ─────────────
def test_quick_route_requires_login(client):
    r = client.post("/admin/whatsapp/quick", json={"phone": "0555123456", "text": "سلام"})
    assert r.status_code == 302 and "login" in r.headers["Location"]


def test_quick_route_returns_link(logged_in_admin, seed):
    r = logged_in_admin.post("/admin/whatsapp/quick",
                             json={"phone": "+213 555 12 34 56", "name": "أمين", "text": "{hello}\n\nمرحبا {heart}"})
    js = r.get_json()
    assert r.status_code == 200 and js["ok"] and js["phone"] == "213555123456"
    assert js["url"].startswith("https://wa.me/213555123456?text=") and "أمين" in js["text"]


def test_quick_route_validation_errors(logged_in_admin, seed):
    r = logged_in_admin.post("/admin/whatsapp/quick", json={"phone": "123", "text": "سلام"})
    assert r.status_code == 400 and r.get_json()["ok"] is False
    r = logged_in_admin.post("/admin/whatsapp/quick", json={"phone": "0555123456", "text": "😀"})
    assert r.status_code == 400 and "إيموجي" in r.get_json()["error"]
    assert logged_in_admin.post("/admin/whatsapp/quick", data="not json").status_code == 400


def test_dashboard_shows_quick_box(logged_in_admin, seed):
    html = logged_in_admin.get("/admin/").get_data(as_text=True)
    assert 'id="waq"' in html and "واتساب سريع" in html
    assert "ترحيب" in html and "رسالة حرّة" in html and "waq-symbols" in html
