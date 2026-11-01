"""v188: زر «فحص شامل» فالإعدادات — تقرير بلا أسرار وبلا بيانات زبائن."""
import time

import pytest

from services import diagnostics as dg


# ───────────── إخفاء الأسرار وبيانات الزبائن ─────────────
def test_redact_hides_env_secrets_and_token_patterns(monkeypatch):
    monkeypatch.setenv("ZR_API_KEY", "supersecret-key-12345")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:AAH-abcdefghijklmnopqrstuvwxyz0123")
    text = ("fail key=supersecret-key-12345 url=https://x?api_token=abcDEF123&y=1 "
            "Bearer EAAGm0PX4ZCpsBA https://api.telegram.org/bot123456789:AAH-abcdefghijklmnopqrstuvwxyz0123/getMe "
            "postgresql://user:pa55word@host/db")
    out = dg.redact(text)
    for leaked in ("supersecret-key-12345", "abcDEF123", "EAAGm0PX4ZCpsBA", "AAH-abcdefghijklmnop", "pa55word"):
        assert leaked not in out
    # قيمة سرّية وحدها (بلا key=) تتعوّض باسم المتغيّر باش نفهمو أنهي وحدة
    assert dg.redact("الخطأ من supersecret-key-12345 هنا") == "الخطأ من <ZR_API_KEY> هنا"


def test_mask_phone_and_name():
    assert dg.mask_phone("0555123456") == "05******56"
    assert dg.mask_phone("12") == "***"
    assert dg.mask_name("أمين بن علي") == "أ***"


def test_mask_obj_hides_pii_but_keeps_diagnostic_fields():
    raw = {
        "tracking": "ZR-1", "state": {"name": "Livré", "description": "Livré au client"},
        "customer": {"name": "Karim Ben", "phone": {"number1": "0770000000"}},
        "contact_phone": "0555123456", "firstname": "Sara", "address": "حي 5 جويلية",
        "note": "اتصل قبل", "amount": 2500, "to_wilaya_name": "Batna",
        "comment": None, "free": "رقمي 0661234567 اتصل",
    }
    out = dg.mask_obj(raw)
    assert out["tracking"] == "ZR-1" and out["amount"] == 2500 and out["to_wilaya_name"] == "Batna"
    assert out["state"] == {"name": "Livré", "description": "Livré au client"}   # حالة الشركة تبقى
    assert out["customer"]["name"] == "K***" and out["customer"]["phone"]["number1"] == "07******00"
    assert out["contact_phone"] == "05******56" and out["firstname"] == "S***"
    assert out["address"] == "***" and out["note"] == "***"
    assert "0661234567" not in out["free"]


# ───────────── التقرير ─────────────
def _fake(name, status, detail="", block=""):
    return (name, lambda: dg.Check(name, status, detail, block))


@pytest.fixture
def fake_network(monkeypatch):
    monkeypatch.setattr(dg, "NETWORK_CHECKS", (
        _fake("ZR Express", "fail", "key=supersecret-key-12345 غالط"),
        _fake("Yalidine", "ok", "", "- 12 طرد"),
        _fake("Telegram (إشعارات الطلبيات)", "skip", "غير مربوط"),
    ))
    monkeypatch.setenv("ZR_API_KEY", "supersecret-key-12345")


def test_collect_runs_local_checks_on_real_db(app, seed, fake_network):
    with app.app_context():
        checks = dg.collect(app)
    names = [c.name for c in checks]
    for expected in ("النظام", "قاعدة البيانات", "سلامة بيانات المتجر", "المهام الخلفية",
                     "صندوق واتساب التلقائي", "الصور والملفات", "ZR Express", "Yalidine"):
        assert any(expected in n for n in names), expected
    db_check = next(c for c in checks if c.name == "قاعدة البيانات")
    assert db_check.status == "ok" and "| orders |" in db_check.block
    assert all(c.status in dg.STATUS_ICON for c in checks)


def test_report_puts_problems_first_and_leaks_nothing(app, seed, fake_network):
    with app.app_context():
        report, counts = dg.build_report(dg.collect(app), site="https://budacdz.com")
    assert "supersecret-key-12345" not in report
    assert counts["fail"] >= 1 and counts["skip"] >= 1
    first = report.index("## الأهم أولاً")
    assert report.index("ZR Express", first) < report.index("## كل الفحوص")
    assert "❌" in report.split("## كل الفحوص")[0]


def test_hanging_check_times_out_instead_of_blocking(app, seed, monkeypatch):
    monkeypatch.setattr(dg, "TOTAL_TIMEOUT", 0.4)
    monkeypatch.setattr(dg, "NETWORK_CHECKS", (("بطيء", lambda: (time.sleep(3), dg.Check("بطيء", "ok"))[1]),))
    start = time.time()
    with app.app_context():
        checks = dg.collect(app)
    assert time.time() - start < 2.5
    slow = next(c for c in checks if c.name == "بطيء")
    assert slow.status == "fail" and "ما ردّتش" in slow.detail


def test_exception_in_a_check_becomes_a_failure(app, seed, monkeypatch):
    def boom():
        raise RuntimeError("timeout key=abc123")
    monkeypatch.setattr(dg, "NETWORK_CHECKS", (("ZR Express", boom),))
    with app.app_context():
        checks = dg.collect(app)
    zr = next(c for c in checks if c.name == "ZR Express")
    assert zr.status == "fail" and "RuntimeError" in zr.detail and "abc123" not in zr.detail


def test_carrier_check_flags_empty_fields_and_masks_sample(monkeypatch):
    from services import carrier_parcels as cp
    cp.clear_cache()
    monkeypatch.setitem(cp._FETCHERS, "world", lambda: ([
        {"tracking": f"W{i}", "status": "en_hub", "client": "زبون سرّي", "phone": "0555123456"} for i in range(5)
    ], None))
    check = dg._carrier("world", "World Express", lambda: (True, "✓ التوكن صالح"))
    cp.clear_cache()
    assert check.status == "warn" and "wilaya" in check.detail           # الولاية فارغة فكل الأسطر
    assert "زبون سرّي" not in check.block and "0555123456" not in check.block
    assert "05******56" in check.block


def test_carrier_check_fails_cleanly_when_keys_invalid():
    check = dg._carrier("zr", "ZR Express", lambda: (False, "المفاتيح غالطة"))
    assert check.status == "fail" and "غالطة" in check.detail


# ───────────── المسار ─────────────
def test_route_requires_login(client):
    r = client.post("/admin/settings/diagnostics")
    assert r.status_code == 302 and "login" in r.headers["Location"]


def test_route_returns_downloadable_markdown(logged_in_admin, seed, fake_network):
    r = logged_in_admin.post("/admin/settings/diagnostics")
    assert r.status_code == 200
    assert "attachment" in r.headers["Content-Disposition"] and ".md" in r.headers["Content-Disposition"]
    assert r.headers["Content-Type"].startswith("text/markdown") and "no-store" in r.headers["Cache-Control"]
    assert "fail=" in r.headers["X-Diag-Summary"]
    body = r.get_data(as_text=True)
    assert "# تقرير الفحص الشامل" in body and "supersecret-key-12345" not in body


def test_route_refuses_parallel_runs(logged_in_admin, seed, fake_network):
    assert dg._run_lock.acquire(blocking=False)
    try:
        r = logged_in_admin.post("/admin/settings/diagnostics")
        assert r.status_code == 409 and "يخدم" in r.get_json()["error"]
    finally:
        dg._run_lock.release()


def test_settings_page_has_the_button(logged_in_admin, seed):
    html = logged_in_admin.get("/admin/settings").get_data(as_text=True)
    assert 'id="diag-run"' in html and "فحص شامل للنظام" in html
