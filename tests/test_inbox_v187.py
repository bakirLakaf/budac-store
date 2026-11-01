"""v187: صندوق الرسائل (فيسبوك + انستغرام) — بلا انترنت: نبدّلو graph بدالة مزيّفة."""
import json

import pytest

from social_publisher import inbox
from social_publisher.config import cfg
from social_publisher.platforms.base import PlatformError


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    cfg.load()   # cfg يتعمّر برك عند load() — بلاها الاختبار يطيح كي يتشغّل وحدو
    monkeypatch.setattr(cfg, "fb_page_id", "PAGE1")
    monkeypatch.setattr(cfg, "fb_page_token", "tok-abcdefghij")
    monkeypatch.setattr(cfg, "ig_user_id", "IG1")
    inbox.clear_cache()
    yield
    inbox.clear_cache()


def test_list_conversations_picks_customer_not_page(monkeypatch):
    def fake(method, path, token, data=None, params=None):
        assert path == "PAGE1/conversations"
        platform = params["platform"]
        return {"data": [{
            "id": "t_1", "updated_time": "2026-10-31T10:00:00+0000", "snippet": "سلام", "unread_count": 2,
            "participants": {"data": [{"id": "PAGE1", "name": "Budac"}, {"id": "U9", "name": "زبون " + platform}]}}]}
    monkeypatch.setattr(inbox, "graph", fake)
    fb = inbox.list_conversations("facebook")
    assert fb["ok"] and fb["items"][0]["user_id"] == "U9" and fb["items"][0]["unread"] == 2
    ig = inbox.list_conversations("instagram")
    assert ig["items"][0]["name"] == "زبون instagram"


def test_unread_count_unsupported_falls_back(monkeypatch):
    calls = []

    def fake(method, path, token, data=None, params=None):
        calls.append(params["fields"])
        if "unread_count" in params["fields"]:
            raise PlatformError("Meta: (#100) Tried accessing nonexisting field (code 100/None)")
        return {"data": [{"id": "c1", "participants": {"data": [{"id": "U1", "username": "sara"}]}}]}
    monkeypatch.setattr(inbox, "graph", fake)
    res = inbox.list_conversations("instagram")
    assert res["ok"] and res["items"][0]["username"] == "sara" and len(calls) == 2


def test_not_configured_and_platform_isolation(monkeypatch):
    monkeypatch.setattr(cfg, "ig_user_id", "")
    assert inbox.list_conversations("instagram")["configured"] is False
    monkeypatch.setattr(inbox, "graph", lambda *a, **k: (_ for _ in ()).throw(PlatformError("Meta: boom (code 190/None)")))
    res = inbox.list_conversations("facebook")
    assert res["ok"] is False and "توكن" in res["error"]       # تلميح الكود 190


def test_get_messages_oldest_first_and_mine_flag(monkeypatch):
    monkeypatch.setattr(inbox, "graph", lambda *a, **k: {"messages": {"data": [
        {"id": "m2", "message": "مرحبا بيك", "from": {"id": "PAGE1"}, "created_time": "2026-10-31T10:01:00+0000"},
        {"id": "m1", "message": "بصح واش كاين؟", "from": {"id": "U9", "name": "زبون"}, "created_time": "2026-10-31T10:00:00+0000",
         "attachments": {"data": [{"id": "a"}]}},
    ]}})
    msgs = inbox.get_messages("t_123")
    assert [m["id"] for m in msgs] == ["m1", "m2"]
    assert msgs[0]["mine"] is False and msgs[0]["has_attachment"] and msgs[1]["mine"] is True


def test_invalid_ids_rejected():
    for bad in ("../etc", "a b", "", "x" * 200, "t_1/messages"):
        assert not inbox.valid_id(bad)
    with pytest.raises(PlatformError):
        inbox.get_messages("../me")
    with pytest.raises(PlatformError):
        inbox.send_reply("facebook", "a/b", "سلام")


def test_send_reply_payload_and_limits(monkeypatch):
    sent = {}

    def fake(method, path, token, data=None, params=None):
        sent.update(method=method, path=path, data=data)
        return {"message_id": "mid.1"}
    monkeypatch.setattr(inbox, "graph", fake)
    assert inbox.send_reply("instagram", "U9", "  مرحبا  ") == "mid.1"
    assert sent["method"] == "POST" and sent["path"] == "PAGE1/messages"
    assert json.loads(sent["data"]["recipient"]) == {"id": "U9"}
    assert json.loads(sent["data"]["message"]) == {"text": "مرحبا"}
    assert sent["data"]["messaging_type"] == "RESPONSE"

    with pytest.raises(PlatformError):
        inbox.send_reply("facebook", "U9", "   ")
    with pytest.raises(PlatformError):
        inbox.send_reply("facebook", "U9", "ا" * 600)          # 1200 بايت > 1000
    with pytest.raises(PlatformError):
        inbox.send_reply("tiktok", "U9", "سلام")


def test_24h_window_error_gets_friendly_hint(monkeypatch):
    monkeypatch.setattr(inbox, "graph", lambda *a, **k: (_ for _ in ()).throw(
        PlatformError("Meta: outside window (code 10/2018278)")))
    with pytest.raises(PlatformError) as e:
        inbox.send_reply("facebook", "U9", "سلام")
    assert "24 ساعة" in str(e.value)


def test_tokens_are_redacted_from_errors(monkeypatch):
    monkeypatch.setattr(inbox, "graph", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("failed with tok-abcdefghij inside")))
    res = inbox.list_conversations("facebook")
    assert "tok-abcdefghij" not in res["error"]


def test_inbox_tab_renders_in_publisher_page(app):
    """القالب يتعرض بلا أخطاء وفيه التبويب واللوحة وشرح الصلاحيات (بلا PostgreSQL)."""
    import os

    from flask import Blueprint, render_template

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app.register_blueprint(Blueprint("sp_tmpl", "social_publisher",
                                     template_folder=os.path.join(root, "social_publisher", "templates")))
    app.jinja_env.filters["sp_status"] = lambda x: x
    app.jinja_env.filters["sp_local"] = lambda x: x
    with app.test_request_context("/admin/orders"):   # endpoint حقيقي باش base_admin يتعرض
        html = render_template(
            "social_publisher/index.html", prefix="/admin/social",
            labels={"facebook": "فيسبوك", "instagram": "انستغرام", "tiktok": "تيك توك"},
            posts=[], configured={}, tiktok_connect=False, setup=[], public_base_url="https://x",
            tiktok_mode="inbox", tiktok_redirect_suggestion="", all_connected=True, has_active=False,
            max_images=10, max_video_mb=100, msg=None, err=None, form={}, store_name="BUDAC")
    assert 'data-tab="inbox"' in html and 'data-panel="inbox"' in html
    assert "pages_messaging" in html and "instagram_manage_messages" in html
    assert "['compose', 'posts', 'inbox', 'setup']" in html
