"""v181 — رابط تتبّع خاص، واتساب تلقائي (Outbox + webhook)، مزامنة التتبّع، حفظ المعلومات."""
import hashlib
import hmac
import json

import pytest

from models import db, Order, WhatsAppOutbox, StoreSetting


@pytest.fixture
def wa_env(monkeypatch):
    monkeypatch.setenv("WA_CLOUD_TOKEN", "tok")
    monkeypatch.setenv("WA_PHONE_NUMBER_ID", "123")
    monkeypatch.setenv("WA_APP_SECRET", "appsecret")
    monkeypatch.setenv("WA_VERIFY_TOKEN", "verify-me")


def _order(**kw):
    data = dict(order_number="BD-81818181", customer_name="علي بن علي",
                customer_phone="0666255200", wilaya_code="16", wilaya_name="الجزائر",
                subtotal=1200, shipping_price=600, total=1800,
                status=Order.STATUS_PENDING)
    data.update(kw)
    o = Order(**data)
    db.session.add(o)
    db.session.commit()
    return o


# ───────────── رابط التتبّع الخاص ─────────────

def test_new_order_gets_unguessable_token(app):
    o = _order()
    assert o.track_token and len(o.track_token) >= 20
    assert _order(order_number="BD-2").track_token != o.track_token


def test_private_link_opens_without_phone_and_is_noindex(client, app):
    o = _order()
    r = client.get(f"/t/{o.track_token}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "BD-81818181" in html and 'name="phone"' not in html
    assert "noindex" in r.headers["X-Robots-Tag"]
    assert "no-store" in r.headers["Cache-Control"]


def test_wrong_token_and_order_number_as_token_are_404(client, app):
    o = _order()
    assert client.get("/t/xxxxxxxxxxxxxxxxxxxxxx").status_code == 404
    assert client.get(f"/t/{o.order_number}").status_code == 404   # الرقم ≠ كلمة سر


def test_old_order_without_token_gets_one_lazily(app):
    o = _order()
    o.track_token = None
    db.session.commit()
    url = o.track_url("https://x.dz")
    assert url.startswith("https://x.dz/t/") and o.track_token


def test_wa_message_contains_private_link(app):
    from services import wa_templates as wa
    o = _order()
    text = wa.render_for_order(o, "shipped", base_url="https://budacdz.com")
    assert f"https://budacdz.com/t/{o.track_token}" in text


# ───────────── واتساب: الطابور ─────────────

def test_nothing_queued_when_api_not_configured(app, monkeypatch):
    monkeypatch.delenv("WA_CLOUD_TOKEN", raising=False)
    from services import whatsapp_api as api
    assert api.enqueue(_order(), "confirm") is None


def test_enqueue_is_idempotent_and_respects_toggles(app, wa_env):
    from services import whatsapp_api as api
    o = _order()
    assert api.enqueue(o, "confirm") is not None
    db.session.commit()
    assert api.enqueue(o, "confirm") is None                 # مكرّرة
    assert WhatsAppOutbox.query.count() == 1
    assert api.enqueue(o, "thanks") is None                  # مطفية افتراضيًا
    StoreSetting.set("wa_auto_master", "0")
    assert api.enqueue(o, "shipped") is None                 # المفتاح العام


def test_invalid_phone_not_queued(app, wa_env):
    from services import whatsapp_api as api
    assert api.enqueue(_order(customer_phone="123"), "confirm") is None


def test_status_change_queues_matching_message_once(app, wa_env):
    from services.order_status import change_order_status
    o = _order(status=Order.STATUS_PREPARED)
    change_order_status(o, Order.STATUS_SHIPPED)
    db.session.commit()
    change_order_status(o, Order.STATUS_SHIPPED)             # تعاودت
    db.session.commit()
    assert [(m.kind, m.status) for m in WhatsAppOutbox.query.all()] == [("shipped", "pending")]


def test_payload_shape_for_confirm_has_quick_reply_payloads(app, wa_env):
    from services import whatsapp_api as api
    o = _order()
    p = api.build_payload(o, "confirm")
    assert p["to"] == "213666255200" and p["template"]["name"] == "budac_order_confirm"
    comps = p["template"]["components"]
    assert [c["parameters"][0]["text"] for c in comps[:1]][0] == "علي بن علي"
    assert comps[1]["parameters"][0]["payload"] == "CONFIRM:BD-81818181"
    assert comps[2]["parameters"][0]["payload"] == "CANCEL:BD-81818181"
    assert f"/t/{o.track_token}" in comps[0]["parameters"][3]["text"]


def test_process_outbox_sends_and_marks_sent(app, wa_env, monkeypatch):
    from services import whatsapp_api as api
    o = _order()
    api.enqueue(o, "confirm"); db.session.commit()
    monkeypatch.setattr(api, "send_message", lambda order, kind: "wamid.X")
    assert api.process_outbox() == (1, 0)
    row = WhatsAppOutbox.query.one()
    assert row.status == "sent" and row.provider_id == "wamid.X"
    assert api.process_outbox() == (0, 0)                     # ما تتبعثش مرتين


def test_failures_retry_then_fail_permanently(app, wa_env, monkeypatch):
    from services import whatsapp_api as api
    api.enqueue(_order(), "confirm"); db.session.commit()

    def boom(order, kind):
        raise RuntimeError("Meta 400")
    monkeypatch.setattr(api, "send_message", boom)
    for _ in range(3):
        api.process_outbox()
    row = WhatsAppOutbox.query.one()
    assert row.status == "failed" and row.attempts == 3 and "Meta 400" in row.error


# ───────────── Webhook ─────────────

def _signed(client, body, secret="appsecret", sign=True):
    raw = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(
            secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/webhooks/whatsapp", data=raw, headers=headers)


def _reply(payload, sender="213666255200"):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": sender, "type": "button", "button": {"payload": payload}}]}}]}]}


def test_webhook_verification(client, wa_env):
    ok = client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=42")
    assert ok.status_code == 200 and ok.get_data(as_text=True) == "42"
    assert client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=no&hub.challenge=1").status_code == 403


def test_webhook_rejects_unsigned_and_wrong_signature(client, app, wa_env):
    o = _order()
    body = _reply(f"CONFIRM:{o.order_number}")
    assert _signed(client, body, sign=False).status_code == 403
    assert _signed(client, body, secret="wrong").status_code == 403
    assert db.session.get(Order, o.id).status == Order.STATUS_PENDING


def test_webhook_closed_without_app_secret(client, app, monkeypatch):
    monkeypatch.delenv("WA_APP_SECRET", raising=False)
    assert _signed(client, {}, secret="x").status_code == 403


def test_confirm_button_confirms_pending_order(client, app, wa_env):
    o = _order()
    assert _signed(client, _reply(f"CONFIRM:{o.order_number}")).status_code == 200
    assert db.session.get(Order, o.id).status == Order.STATUS_CONFIRMED


def test_confirm_from_other_number_is_ignored(client, app, wa_env):
    o = _order()
    _signed(client, _reply(f"CONFIRM:{o.order_number}", sender="213777000000"))
    assert db.session.get(Order, o.id).status == Order.STATUS_PENDING


def test_cancel_button_only_adds_admin_note(client, app, wa_env):
    o = _order()
    _signed(client, _reply(f"CANCEL:{o.order_number}"))
    o = db.session.get(Order, o.id)
    assert o.status == Order.STATUS_PENDING and "طلب الإلغاء" in o.admin_note


# ───────────── مزامنة شركات التوصيل ─────────────

def test_sync_updates_status_and_queues_whatsapp(app, wa_env, monkeypatch):
    from services import tracking_sync, zrexpress
    o = _order(status=Order.STATUS_PREPARED, tracking_number="ZR1", carrier_code="zrexpress")
    monkeypatch.setattr(zrexpress, "is_configured", lambda: True)
    monkeypatch.setattr(zrexpress, "get_parcel_status", lambda t: {"state": "Livré"})
    stats = tracking_sync.sync_active()
    o = db.session.get(Order, o.id)
    assert stats["changed"] == 1 and o.status == Order.STATUS_DELIVERED
    assert o.last_synced_at is not None and o.carrier_state == "Livré"


def test_sync_skips_recently_synced_and_survives_failures(app, monkeypatch):
    from services import tracking_sync, zrexpress
    from models import utcnow
    _order(order_number="BD-A", tracking_number="A", carrier_code="zrexpress",
           status=Order.STATUS_SHIPPED, last_synced_at=utcnow())
    _order(order_number="BD-B", tracking_number="B", carrier_code="zrexpress",
           status=Order.STATUS_SHIPPED)
    monkeypatch.setattr(zrexpress, "is_configured", lambda: True)

    def boom(t):
        raise RuntimeError("down")
    monkeypatch.setattr(zrexpress, "get_parcel_status", boom)
    stats = tracking_sync.sync_active()
    assert stats["failed"] == 1 and stats["checked"] == 0     # A تخطّيناها، B فشلت بلا انهيار
    assert tracking_sync.sync_active()["failed"] == 0          # B سجّلنا المحاولة، ما نعاودوهاش فورًا


def test_sync_never_moves_status_backwards(app, monkeypatch):
    from services import tracking_sync, zrexpress
    o = _order(status=Order.STATUS_SHIPPED, tracking_number="Z", carrier_code="zrexpress")
    monkeypatch.setattr(zrexpress, "is_configured", lambda: True)
    monkeypatch.setattr(zrexpress, "get_parcel_status", lambda t: {"state": "Prêt à expédier"})
    tracking_sync.sync_active()
    assert db.session.get(Order, o.id).status == Order.STATUS_SHIPPED


# ───────────── cron + أدمن + حفظ المعلومات ─────────────

def test_cron_endpoint_closed_without_secret_and_protected(client, monkeypatch):
    monkeypatch.delenv("JOBS_SECRET", raising=False)
    assert client.get("/internal/jobs/run?key=x").status_code == 404
    monkeypatch.setenv("JOBS_SECRET", "s" * 20)
    assert client.get("/internal/jobs/run?key=bad").status_code == 403
    assert client.get("/internal/jobs/run?key=" + "s" * 20).status_code == 200


def test_admin_wa_auto_page_and_order_detail_show_link(logged_in_admin, app, wa_env):
    o = _order()
    r = logged_in_admin.get("/admin/whatsapp-auto")
    assert r.status_code == 200 and "مضبوط" in r.get_data(as_text=True)
    d = logged_in_admin.get(f"/admin/orders/{o.id}").get_data(as_text=True)
    assert f"/t/{o.track_token}" in d


def test_save_info_checkbox_present_and_checked_by_default(client, app):
    from flask import render_template
    with app.test_request_context():
        part = render_template("partials/save_info.html")
    assert 'id="saveInfoCheck" checked' in part and "saved-info.js" in part


def test_landing_order_queues_confirmation_and_exposes_private_link(client, app, seed, wa_env):
    """طلبية حقيقية من صفحة الهبوط ← رسالة تأكيد فالطابور + صفحة الشكر تعرض رابط التتبّع."""
    from tests.test_landing_v179 import _make, _form
    *_, v2 = _make(app)
    r = client.post("/lp/ac", data=_form(variant_id=str(v2)))
    assert r.status_code == 302, r.get_data(as_text=True)[:300]
    order = Order.query.one()
    rows = WhatsAppOutbox.query.all()
    assert [(m.order_id, m.kind, m.status) for m in rows] == [(order.id, "confirm", "pending")]
    assert order.track_token


def test_landing_order_without_wa_config_still_works(client, app, seed, monkeypatch):
    monkeypatch.delenv("WA_CLOUD_TOKEN", raising=False)
    from tests.test_landing_v179 import _make, _form
    *_, v2 = _make(app)
    assert client.post("/lp/ac", data=_form(variant_id=str(v2))).status_code == 302
    assert WhatsAppOutbox.query.count() == 0


def test_landing_page_has_save_info_checkbox(client, app):
    from tests.test_landing_v179 import _make
    _make(app)
    assert 'id="saveInfoCheck" checked' in client.get("/lp/ac").get_data(as_text=True)
