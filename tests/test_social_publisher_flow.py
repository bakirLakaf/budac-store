"""أداة النشر على PostgreSQL حقيقي + Meta مزيّف (بلا انترنت). تتخطّى لو TEST_DATABASE_URL ما تحطش.
تشغيل: TEST_DATABASE_URL=postgresql://user:pw@localhost/test_db pytest -q tests/test_social_publisher_flow.py
(⚠️ استعمل قاعدة اختبار: تحذف جداول sp_* وتخلق جداول المتجر)
"""
import io
import json
import os
import re
from functools import wraps
from pathlib import Path

import pytest
from flask import Flask, session
from PIL import Image

DSN = os.getenv("TEST_DATABASE_URL", "")
if not DSN:
    pytest.skip("TEST_DATABASE_URL ما تحطش — اختبارات PostgreSQL متخطّاة", allow_module_level=True)


def make_app():
    """Flask فارغ + قالب base_admin مبسّط (القالب الحقيقي يتجرّب في اختبارات التكامل بالمتجر)."""
    from jinja2 import ChoiceLoader, DictLoader
    app = Flask(__name__)
    app.secret_key = "k" * 40
    app.jinja_loader = ChoiceLoader([DictLoader({"admin/base_admin.html": "{% block content %}{% endblock %}"}),
                                     app.jinja_loader])
    app.jinja_env.globals["csrf_token"] = lambda: "stub-token"
    app.jinja_env.globals["store_name"] = "TEST"
    return app


class FakeResp:
    def __init__(self, js, status=200):
        self._js, self.status_code = js, status

    def json(self):
        return self._js


class FakeMeta:
    """يقلّد Graph API ويسجّل كل الطلبات."""

    def __init__(self):
        self.calls, self.n, self.fail_publish_ig = [], 0, False

    def __call__(self, method, url, **kw):
        data = kw.get("data") or {}
        self.calls.append((method, url, dict(data), kw.get("params")))
        if "/media_publish" in url:
            if self.fail_publish_ig:
                return FakeResp({"error": {"message": "boom", "code": 9007, "access_token": "x"}}, 400)
            return FakeResp({"id": "ig_post_1"})
        if method == "GET" and "status_code" in str(kw.get("params")):
            return FakeResp({"status_code": "FINISHED"})
        self.n += 1
        if url.endswith("/photos"):
            return FakeResp({"id": f"ph{self.n}", "post_id": f"pp{self.n}"})
        return FakeResp({"id": f"id{self.n}"})

    def urls(self, suffix):
        return [c for c in self.calls if c[1].endswith(suffix) and c[0] == "POST"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    uploads = tmp_path / "shop_uploads"
    uploads.mkdir()
    Image.new("RGB", (800, 600), "red").save(uploads / "a.png")  # PNG: لازم يتحول JPEG لانستغرام

    for k, v in {"DATABASE_URL": DSN, "SP_PUBLIC_BASE_URL": "https://shop.test", "SP_SECRET": "s" * 40,
                 "SP_TEMP_DIR": str(tmp_path / "sp_tmp"), "FB_PAGE_ID": "PAGE1", "FB_PAGE_TOKEN": "fbtoken-123456",
                 "IG_USER_ID": "IG1"}.items():
        monkeypatch.setenv(k, v)

    from social_publisher import db, service
    from social_publisher.adapter import BaseAdapter
    from social_publisher.platforms import base, instagram

    class FakeAdapter(BaseAdapter):
        def search_products(self, q, limit=12):
            return [{"id": "73", "name": "رجل صندوق", "price": 120, "thumb": None}]

        def get_product(self, pid):
            if pid != "73":
                return None
            return {"id": "73", "name": "رجل صندوق", "price": 120, "url": "https://shop.test/product/x",
                    "images": [{"id": "a.png", "url": "https://shop.test/static/a.png",
                                "local_path": str(uploads / "a.png")}]}

    meta = FakeMeta()
    monkeypatch.setattr(base, "http", meta)
    monkeypatch.setattr(instagram, "POLL_SECONDS", 0)
    monkeypatch.setattr(service, "kick", lambda: None)  # نشغّل run_due يدويًا باش الاختبار يكون ثابت

    db.close_pool()
    from social_publisher import init_social_publisher
    app = make_app()

    def admin_required(fn):
        @wraps(fn)
        def w(*a, **kw):
            if not session.get("admin"):
                return ("forbidden", 403)
            return fn(*a, **kw)
        return w

    init_social_publisher(app, admin_required, adapter=FakeAdapter(), start_scheduler=False)
    with db.cursor() as cur:
        cur.execute("TRUNCATE sp_posts, sp_media, sp_results, sp_tokens RESTART IDENTITY CASCADE")

    client = app.test_client()
    with client.session_transaction() as s:
        s["admin"] = True
    yield type("Env", (), dict(client=client, meta=meta, uploads=uploads, tmp=tmp_path / "sp_tmp",
                               db=db, service=service))
    db.close_pool()


def png_bytes(color="blue", size=(1000, 1000)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


def post(env, caption="مرحبا", platforms=("facebook", "instagram"), files=(), gallery=True, **extra):
    data = {"caption": caption, "platforms": list(platforms), "mode": "now", **extra}
    if gallery:
        data.update(product_id="73", gallery=["a.png"])
    data["files"] = [(io.BytesIO(b), n) for n, b in files]
    return env.client.post("/admin/social/posts", data=data, content_type="multipart/form-data")


def status(env, pid=1):
    with env.db.cursor(commit=False) as cur:
        cur.execute("SELECT status FROM sp_posts WHERE id=%s", (pid,))
        return cur.fetchone()["status"]


def tmp_files(env):
    return sorted(p.name for p in env.tmp.iterdir())


def test_requires_admin(env):
    c = env.client.application.test_client()
    assert c.get("/admin/social/").status_code == 403
    assert c.post("/admin/social/posts").status_code == 403
    assert c.get("/admin/social/api/products").status_code == 403


def test_publish_gallery_plus_upload_then_cleanup(env):
    r = post(env, files=[("b.png", png_bytes())])
    assert r.status_code == 302
    assert len(tmp_files(env)) == 1  # صورة الرفع فقط، صورة المتجر ما تكوبيتش
    env.service.run_due()

    assert status(env) == "done"
    fb_photos = env.meta.urls("/photos")
    assert len(fb_photos) == 2 and all(c[2]["published"] == "false" for c in fb_photos)
    feed = env.meta.urls("/feed")[0][2]
    assert len(json.loads(feed["attached_media"])) == 2 and feed["message"] == "مرحبا"
    ig_children = [c for c in env.meta.urls("/media") if c[2].get("is_carousel_item")]
    assert len(ig_children) == 2 and all(c[2]["image_url"].endswith("?ig=1") for c in ig_children)
    assert any(c[2].get("media_type") == "CAROUSEL" for c in env.meta.urls("/media"))
    assert tmp_files(env) == []  # الملف المؤقت تحذف بعد النشر
    assert (env.uploads / "a.png").exists()  # صورة المتجر باقية


def test_dedup_and_shared_file_kept_until_last_post(env):
    img = ("same.png", png_bytes("green"))
    post(env, platforms=("facebook",), files=[img], gallery=False)  # #1 درك
    post(env, platforms=("facebook",), files=[img], gallery=False,
         **{"mode": "schedule", "scheduled_at": "2099-01-01T10:00"})  # #2 مجدول
    assert len(tmp_files(env)) == 1  # نفس الصورة = نسخة وحدة
    env.service.run_due()
    assert status(env, 1) == "done" and status(env, 2) == "scheduled"
    assert len(tmp_files(env)) == 1  # باقية لأن #2 يحتاجها
    assert env.service.cancel_post(2)
    assert tmp_files(env) == []


def test_partial_failure_retries_only_failed_platform(env):
    env.meta.fail_publish_ig = True
    post(env, files=[], gallery=True)
    env.service.run_due()
    assert status(env) == "partial"
    fb_before = len(env.meta.urls("/photos")) + len(env.meta.urls("/feed"))

    env.meta.fail_publish_ig = False
    assert env.service.retry_post(1)
    env.service.run_due()
    assert status(env) == "done"
    assert len(env.meta.urls("/photos")) + len(env.meta.urls("/feed")) == fb_before  # فيسبوك ما تعاودش


def test_error_messages_hide_tokens(env):
    env.meta.fail_publish_ig = True
    post(env)
    env.service.run_due()
    with env.db.cursor(commit=False) as cur:
        cur.execute("SELECT error FROM sp_results")
        assert all("fbtoken-123456" not in (r["error"] or "") for r in cur.fetchall())


def test_public_media_signed_and_expires(env):
    from social_publisher import media as M
    post(env, platforms=("facebook",), files=[("b.png", png_bytes())], gallery=False,
         **{"mode": "schedule", "scheduled_at": "2099-01-01T10:00"})
    url = M.public_url({"id": 1, "kind": "image", "local_path": None}).replace("https://shop.test", "")
    c = env.client.application.test_client()  # بلا دخول: الرابط العام لازم يخدم بالتوقيع
    r = c.get(url)
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert c.get(url.replace(M.token_for(1), "0" * 24)).status_code == 404
    env.service.cancel_post(1)
    assert c.get(url).status_code == 404  # بعد الإلغاء ما يخدمش


def test_instagram_ratio_padding(env):
    from social_publisher import media as M
    p = env.tmp.parent / "wide.png"
    Image.new("RGB", (2000, 400), "red").save(p)  # نسبة 5:1 (مرفوضة في انستغرام)
    jpg = M.render_jpeg({"local_path": str(p), "url": None}, ig=True)
    w, h = Image.open(io.BytesIO(jpg)).size
    assert 0.8 <= w / h <= 1.92


def test_validation_cleans_uploaded_files(env):
    r = post(env, platforms=("instagram",), files=[], gallery=False)
    assert r.status_code == 400 and "انستغرام" in r.get_data(as_text=True)
    r = post(env, platforms=("facebook",), files=[("x.png", png_bytes()), ("v.mp4", b"\x00" * 2048)], gallery=False)
    assert r.status_code == 400
    assert tmp_files(env) == []  # ما بقاش حتى ملف يتيم
    assert post(env, platforms=(), files=[("x.png", png_bytes())], gallery=False).status_code == 400
    assert tmp_files(env) == []


def test_rejects_bad_files_and_past_schedule(env):
    assert post(env, files=[("evil.exe", b"MZ")], gallery=False).status_code == 400
    assert post(env, files=[("fake.png", b"not an image")], gallery=False).status_code == 400
    r = post(env, **{"mode": "schedule", "scheduled_at": "2001-01-01T10:00"})
    assert r.status_code == 400 and "المستقبل" in r.get_data(as_text=True)
    with env.db.cursor(commit=False) as cur:
        cur.execute("SELECT count(*) AS n FROM sp_posts")
        assert cur.fetchone()["n"] == 0


def test_gallery_ids_validated_against_product(env):
    # صورة ما هيش من المنتج: تتجاهل (ما نثقوش في اللي يبعثو المتصفح)
    r = env.client.post("/admin/social/posts", data={
        "caption": "x", "platforms": ["facebook"], "mode": "now", "product_id": "73",
        "gallery": ["../../etc/passwd"]}, content_type="multipart/form-data")
    assert r.status_code == 302
    with env.db.cursor(commit=False) as cur:
        cur.execute("SELECT count(*) AS n FROM sp_media")
        assert cur.fetchone()["n"] == 0


def test_scheduled_only_runs_when_due(env):
    post(env, platforms=("facebook",), **{"mode": "schedule", "scheduled_at": "2099-01-01T10:00"})
    assert env.service.claim_due() == []
    with env.db.cursor() as cur:
        cur.execute("UPDATE sp_posts SET scheduled_at = now() - interval '1 minute'")
    assert env.service.claim_due() == [1]
    assert env.service.claim_due() == []  # ما يتنشرش مرتين


def test_index_renders(env):
    post(env, platforms=("facebook",))
    r = env.client.get("/admin/social/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "منشور جديد" in html and "مجدول" in html


def test_dsn_normalization():
    from social_publisher.config import normalize_dsn
    assert normalize_dsn("postgresql+psycopg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    assert normalize_dsn("postgresql+psycopg2://u:p@h/db") == "postgresql://u:p@h/db"
    assert normalize_dsn("postgres+psycopg://u@h/db") == "postgresql://u@h/db"
    assert normalize_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"
    assert normalize_dsn("") == ""


def test_limiter_exempts_public_media(env):
    pytest.importorskip("flask_limiter")
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    from social_publisher import init_social_publisher, media as M
    from social_publisher.adapter import BaseAdapter

    post(env, platforms=("facebook",), files=[("b.png", png_bytes())], gallery=False,
         **{"mode": "schedule", "scheduled_at": "2099-01-01T10:00"})
    app = make_app()
    limiter = Limiter(get_remote_address, app=app, default_limits=["1 per minute"], storage_uri="memory://")
    init_social_publisher(app, lambda f: f, adapter=BaseAdapter(), start_scheduler=False, limiter=limiter)
    url = M.public_url({"id": 1, "kind": "image", "local_path": None}).replace("https://shop.test", "")
    c = app.test_client()
    assert [c.get(url).status_code for _ in range(3)] == [200, 200, 200]  # بلا exempt الثانية تولّي 429


def test_works_with_flask_wtf_csrf(env):
    pytest.importorskip("flask_wtf")
    import re as _re
    from flask_wtf.csrf import CSRFProtect
    from social_publisher import init_social_publisher
    from social_publisher.adapter import BaseAdapter

    app = make_app()
    CSRFProtect(app)
    init_social_publisher(app, lambda f: f, adapter=BaseAdapter(), start_scheduler=False)
    c = app.test_client()
    html = c.get("/admin/social/").get_data(as_text=True)
    token = _re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    data = {"caption": "x", "platforms": ["facebook"], "mode": "schedule", "scheduled_at": "2099-01-01T10:00"}
    assert c.post("/admin/social/posts", data=data).status_code == 400  # بلا توكن: مرفوض
    assert c.post("/admin/social/posts", data={**data, "csrf_token": token}).status_code == 302


# ───────────── تكامل مع المتجر الحقيقي (create_app + PostgreSQL) ─────────────
@pytest.fixture()
def store(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("SECRET_KEY", "test-" + "k" * 40)
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("FLASK_ENV", "")
    monkeypatch.setenv("UPLOAD_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("SP_PUBLIC_BASE_URL", "https://shop.test")
    monkeypatch.setenv("FB_PAGE_ID", "PAGE1")
    monkeypatch.setenv("FB_PAGE_TOKEN", "fbtoken-123456")
    monkeypatch.delenv("SP_TEMP_DIR", raising=False)
    from social_publisher import db as spdb
    spdb.close_pool()
    import config as config_module
    importlib.reload(config_module)
    import app as app_module
    importlib.reload(app_module)
    application = app_module.create_app()
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    from models import db
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
    spdb.close_pool()


def test_store_integration_admin_only_and_sidebar_link(store):
    assert "social_publisher" in store.blueprints and store.config["SOCIAL_PUBLISHER_ENABLED"]
    c = store.test_client()
    r = c.get("/admin/social/")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]  # بلا دخول: يوجّه لصفحة الدخول
    assert c.get("/admin/social/api/products").status_code == 302
    with c.session_transaction() as s:
        s["admin_id"], s["admin_username"] = 1, "admin"
    r = c.get("/admin/social/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "منشور جديد" in html and "base_admin" not in html
    assert "📣 النشر على السوشيال" in html  # الرابط في القائمة الجانبية
    assert store.test_client().get("/").status_code == 200  # المتجر العادي يخدم


def test_store_public_media_not_rate_limited_nor_counted(store):
    from social_publisher import media as M, service
    from social_publisher.config import cfg
    c = store.test_client()
    with c.session_transaction() as s:
        s["admin_id"] = 1
    from PIL import Image
    p = cfg.temp_dir / "x.jpg"
    Image.new("RGB", (50, 50), "red").save(p)
    pid = service.create_post("x", ["facebook"], __import__("datetime").datetime(2099, 1, 1, tzinfo=__import__("datetime").timezone.utc),
                              None, [{"source": "upload", "kind": "image", "url": None, "local_path": str(p), "sha256": "a" * 64}])
    with service.db.cursor(commit=False) as cur:
        cur.execute("SELECT id FROM sp_media WHERE post_id=%s", (pid,))
        mid = cur.fetchone()["id"]
    url = M.public_url({"id": mid, "kind": "image", "local_path": None}).replace("https://shop.test", "")
    codes = [store.test_client().get(url).status_code for _ in range(5)]
    assert codes == [200] * 5
    service.cancel_post(pid)
    assert store.test_client().get(url).status_code == 404
