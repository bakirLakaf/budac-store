"""اختبارات تكامل World Express (ECOTRACK) — بلا شبكة: نزيّفو requests.request."""
import types

import pytest
import requests

from services import worldexpress as world


class FakeResp:
    def __init__(self, status=200, data=None, content=b"", headers=None):
        self.status_code = status
        self._data = data
        self.content = content
        self.headers = headers or {}

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def fake_order(**kw):
    base = dict(
        order_number="BD-1001", customer_name="محمد بن علي",
        customer_phone="0555 12 34 56", wilaya_code="16", wilaya_name="الجزائر",
        commune="Bab Ezzouar", delivery_type="home", pickup_point_name=None,
        address_note="حي 5 جويلية", customer_note="", total=2500,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


COMMUNES = {
    "0": {"nom": "Bab Ezzouar", "wilaya_id": 16, "has_stop_desk": 1, "is_active": 1},
    "1": {"nom": "Draria", "wilaya_id": 16, "has_stop_desk": 0, "is_active": 1},
    "2": {"nom": "Boumerdes", "wilaya_id": 35, "has_stop_desk": 0, "is_active": 0},
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WORLD_API_TOKEN", "test-token-not-real")
    monkeypatch.delenv("WORLD_BASE_URL", raising=False)
    world.clear_cache()
    yield
    world.clear_cache()


@pytest.fixture
def api(monkeypatch):
    """يسجّل النداءات ويرجّع الردود حسب المسار."""
    calls = []
    routes = {}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        calls.append({"method": method, "url": url, "params": params, "headers": headers})
        for suffix, resp in routes.items():
            if url.endswith(suffix):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"نداء غير متوقّع: {method} {url}")

    monkeypatch.setattr(world.requests, "request", fake_request)
    routes["/api/v1/get/communes"] = FakeResp(200, COMMUNES)
    return types.SimpleNamespace(calls=calls, routes=routes)


# ── الهاتف والفحص ──

@pytest.mark.parametrize("raw,expected", [
    ("0555 12 34 56", "0555123456"),
    ("+213555123456", "0555123456"),
    ("00213 6 61 23 45 67", "0661234567"),
    ("12345", None), ("", None), (None, None), ("0155123456", None),
])
def test_normalize_phone(raw, expected):
    assert world.normalize_phone(raw) == expected


def test_check_order_data_collects_problems():
    problems = world.check_order_data(fake_order(
        customer_phone="123", customer_name="ا", wilaya_code="99", total=0))
    assert len(problems) == 4


def test_check_order_data_ok():
    assert world.check_order_data(fake_order()) == []


# ── البلديات ──

def test_find_commune_ignores_case_and_accents(api):
    assert world.find_commune(16, "bab ezzouar")["nom"] == "Bab Ezzouar"
    assert world.find_commune(16, "BAB-EZZOUAR")["nom"] == "Bab Ezzouar"


def test_find_commune_skips_inactive_and_unknown(api):
    assert world.find_commune(35, "Boumerdes") is None
    assert world.find_commune(16, "Nowhere") is None


def test_communes_are_cached(api):
    world.get_communes(16)
    world.get_communes(16)
    assert len([c for c in api.calls if c["url"].endswith("/get/communes")]) == 1


# ── إنشاء الطرد ──

def test_create_order_success_sends_expected_params(api):
    api.routes["/api/v1/create/order"] = FakeResp(
        200, {"success": True, "tracking": "ECW123456"})
    result = world.create_order(fake_order())
    assert result == {"tracking_number": "ECW123456"}

    call = [c for c in api.calls if c["url"].endswith("/create/order")][0]
    assert call["method"] == "POST"
    assert call["url"].startswith("https://world-express.ecotrack.dz")
    assert call["headers"]["Authorization"] == "Bearer test-token-not-real"
    p = call["params"]
    assert p["reference"] == "BD-1001"
    assert p["telephone"] == "0555123456"
    assert p["code_wilaya"] == 16 and p["commune"] == "Bab Ezzouar"
    assert p["montant"] == 2500 and p["stop_desk"] == 0 and p["type"] == 1
    assert p["produit"] == world.PRODUCT_LABEL


def test_create_order_rejects_bad_data_before_calling_api(api):
    with pytest.raises(world.WorldError, match="ما تقبلهاش"):
        world.create_order(fake_order(customer_phone="123"))
    assert not [c for c in api.calls if c["url"].endswith("/create/order")]


def test_create_order_surfaces_validation_errors(api):
    api.routes["/api/v1/create/order"] = FakeResp(422, {
        "message": "The given data was invalid.",
        "errors": {"adresse": ["Le champ adresse est obligatoire."]}})
    with pytest.raises(world.WorldError, match="adresse") as exc:
        world.create_order(fake_order())
    assert exc.value.parcel_may_exist is False


def test_create_order_success_false_with_http_200(api):
    api.routes["/api/v1/create/order"] = FakeResp(
        200, {"success": False, "error": 10002, "message": "Commune invalide"})
    with pytest.raises(world.WorldError, match="10002"):
        world.create_order(fake_order())


def test_create_order_read_timeout_flags_possible_duplicate(api):
    api.routes["/api/v1/create/order"] = requests.exceptions.ReadTimeout()
    with pytest.raises(world.WorldError) as exc:
        world.create_order(fake_order())
    assert exc.value.parcel_may_exist is True
    assert "ECOTRACK" in str(exc.value)


def test_create_order_connect_timeout_is_safe_to_retry(api):
    api.routes["/api/v1/create/order"] = requests.exceptions.ConnectTimeout()
    with pytest.raises(world.WorldError) as exc:
        world.create_order(fake_order())
    assert exc.value.parcel_may_exist is False


def test_create_order_success_without_tracking_is_flagged(api):
    api.routes["/api/v1/create/order"] = FakeResp(200, {"success": True})
    with pytest.raises(world.WorldError) as exc:
        world.create_order(fake_order())
    assert exc.value.parcel_may_exist is True


def test_server_error_on_create_is_flagged(api):
    api.routes["/api/v1/create/order"] = FakeResp(502, {"message": "bad gateway"})
    with pytest.raises(world.WorldError) as exc:
        world.create_order(fake_order())
    assert exc.value.parcel_may_exist is True


def test_office_order_uses_office_commune_and_stop_desk(api, monkeypatch):
    import services.carrier_offices as co
    monkeypatch.setattr(co, "get_office", lambda code, name, wilaya=None: {
        "name": name, "commune": "Bab Ezzouar", "address": "شارع الاستقلال"})
    api.routes["/api/v1/create/order"] = FakeResp(200, {"success": True, "tracking": "ECW1"})
    world.create_order(fake_order(delivery_type="office", commune=None,
                                  pickup_point_name="Alger 02 Bab Ezzouar"))
    call = [c for c in api.calls if c["url"].endswith("/create/order")][0]
    assert call["params"]["stop_desk"] == 1
    assert call["params"]["commune"] == "Bab Ezzouar"
    assert "شارع الاستقلال" in call["params"]["adresse"]


def test_office_without_stop_desk_commune_is_refused(api, monkeypatch):
    import services.carrier_offices as co
    monkeypatch.setattr(co, "get_office", lambda *a, **k: {"name": "x", "commune": "Draria"})
    with pytest.raises(world.WorldError, match="مكتب"):
        world.create_order(fake_order(delivery_type="office", commune=None,
                                      pickup_point_name="x"))


def test_unknown_commune_is_refused_with_clear_message(api):
    with pytest.raises(world.WorldError, match="ما لقيناهاش"):
        world.create_order(fake_order(commune="Nowhere"))


# ── أخطاء عامة ──

def test_bad_token_message_never_contains_token(api):
    api.routes["/api/v1/get/orders"] = FakeResp(401, {"message": "Unauthenticated."})
    with pytest.raises(world.WorldError) as exc:
        world.get_order_status("ECW1")
    assert "test-token-not-real" not in str(exc.value)


def test_rate_limit_message(api):
    api.routes["/api/v1/get/orders"] = FakeResp(429, {"message": "x"}, headers={"Retry-After": "33"})
    with pytest.raises(world.WorldError, match="33"):
        world.get_order_status("ECW1")


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("WORLD_API_TOKEN", raising=False)
    assert world.is_configured() is False
    with pytest.raises(world.WorldError, match="WORLD_API_TOKEN"):
        world.get_order_status("ECW1")


def test_insecure_base_url_is_ignored(monkeypatch):
    monkeypatch.setenv("WORLD_BASE_URL", "http://evil.example")
    assert world._base_url() == world.DEFAULT_BASE_URL


# ── الحالة والتأكيد والبوردرو ──

def test_get_order_status_reads_orders_list(api):
    api.routes["/api/v1/get/orders"] = FakeResp(200, {"data": [
        {"tracking": "ECW1", "status": "en_livraison"}]})
    assert world.get_order_status("ECW1")["state"] == "en_livraison"


def test_get_order_status_falls_back_to_tracking_info(api):
    api.routes["/api/v1/get/orders"] = FakeResp(200, {"data": []})
    api.routes["/api/v1/get/tracking/info"] = FakeResp(200, {"activity": [
        {"status": "picked"}, {"status": "livred"}]})
    assert world.get_order_status("ECW1")["state"] == "livred"


def test_validate_order(api):
    api.routes["/api/v1/valid/order"] = FakeResp(200, {"success": True, "message": "ok"})
    assert world.validate_order("ECW1", ask_collection=True) == "ok"
    call = [c for c in api.calls if c["url"].endswith("/valid/order")][0]
    assert call["params"] == {"tracking": "ECW1", "ask_collection": 1}


def test_label_must_be_real_pdf(api):
    api.routes["/api/v1/get/order/label"] = FakeResp(200, content=b"<html>nope</html>")
    with pytest.raises(world.WorldError, match="PDF"):
        world.get_label_pdf("ECW1")
    api.routes["/api/v1/get/order/label"] = FakeResp(200, content=b"%PDF-1.4 ...")
    assert world.get_label_pdf("ECW1").startswith(b"%PDF")


def test_test_connection(api):
    api.routes["/api/v1/validate/token"] = FakeResp(200, {"success": True, "message": "VALID_TOKEN"})
    assert world.test_connection()[0] is True
    api.routes["/api/v1/validate/token"] = FakeResp(
        200, {"success": False, "message": "INVALID_TOKEN"})
    ok, msg = world.test_connection()
    assert ok is False and "test-token-not-real" not in msg


# ── ترجمة الحالات ──

@pytest.mark.parametrize("state,expected", [
    ("prete_a_expedier", None), ("annule", None), ("suspendu", None),
    ("en_ramassage", "shipped"), ("en_livraison", "shipped"), ("vers_hub", "shipped"),
    ("livre_non_encaisse", "delivered"), ("paye_et_archive", "delivered"),
    ("payé_et_archivé", "delivered"), ("livred", "delivered"),
    ("retour_chez_livreur", None), ("retour_en_traitement", None),
    ("retour_recu", "returned"), ("retour_archive", "returned"),
    ("Return_received", "returned"),
    ("حاجة غريبة", None), ("", None), (None, None),
])
def test_map_state(state, expected):
    assert world.map_state_to_order_status(state) == expected


# ════════════════ مسارات الأدمن (نفس ضمانات ZR) ════════════════

from models import Order, db   # noqa: E402


def _world_order(app, number="BD-W1", **kw):
    with app.app_context():
        data = dict(
            order_number=number, customer_name="زبون تجريبي", customer_phone="0555123456",
            wilaya_code="16", wilaya_name="الجزائر", commune="Bab Ezzouar",
            address_note="حي 5 جويلية", subtotal=2000, shipping_price=500, total=2500,
            status=Order.STATUS_PENDING, delivery_type="home",
            carrier_code="worldexpress", carrier_name="World Express",
        )
        data.update(kw)
        o = Order(**data)
        db.session.add(o)
        db.session.commit()
        return o.id


def _creates(api):
    return [c for c in api.calls if c["url"].endswith("/create/order")]


def test_send_world_order_saves_tracking_and_confirms(logged_in_admin, app, seed, api):
    api.routes["/api/v1/create/order"] = FakeResp(200, {"success": True, "tracking": "ECW777"})
    oid = _world_order(app)
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    with app.app_context():
        o = db.session.get(Order, oid)
        assert o.tracking_number == "ECW777"
        assert o.send_state == Order.ZR_SENT
        assert o.status == Order.STATUS_CONFIRMED
        assert o.carrier_state == "prete_a_expedier"
    assert len(_creates(api)) == 1


def test_double_send_creates_only_one_parcel(logged_in_admin, app, seed, api):
    api.routes["/api/v1/create/order"] = FakeResp(200, {"success": True, "tracking": "ECW888"})
    oid = _world_order(app, "BD-W2")
    for _ in range(2):
        logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    assert len(_creates(api)) == 1


def test_failed_send_leaves_order_untouched_and_retryable(logged_in_admin, app, seed, api):
    api.routes["/api/v1/create/order"] = FakeResp(422, {
        "message": "invalid", "errors": {"telephone": ["Numéro invalide"]}})
    oid = _world_order(app, "BD-W3")
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    with app.app_context():
        o = db.session.get(Order, oid)
        assert o.tracking_number is None
        assert o.status == Order.STATUS_PENDING
        assert o.send_state == Order.ZR_FAILED
        assert "Numéro invalide" in (o.send_error or "")
    # إعادة المحاولة فورًا تخدم
    api.routes["/api/v1/create/order"] = FakeResp(200, {"success": True, "tracking": "ECW999"})
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    with app.app_context():
        assert db.session.get(Order, oid).tracking_number == "ECW999"


def test_timeout_blocks_immediate_retry_to_avoid_duplicate(logged_in_admin, app, seed, api):
    api.routes["/api/v1/create/order"] = requests.exceptions.ReadTimeout()
    oid = _world_order(app, "BD-W4")
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    with app.app_context():
        o = db.session.get(Order, oid)
        assert o.tracking_number is None
        assert o.send_state == Order.ZR_SENDING       # محجوزة 3 دقائق
        assert "ECOTRACK" in (o.send_error or "")
    api.routes["/api/v1/create/order"] = FakeResp(200, {"success": True, "tracking": "DUP1"})
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    assert len(_creates(api)) == 1                   # ما تعاودش


def test_world_order_never_goes_to_zr(logged_in_admin, app, seed, api, monkeypatch):
    called = []
    import services.zrexpress as zr
    monkeypatch.setattr(zr, "create_parcel", lambda *a, **k: called.append(1))
    api.routes["/api/v1/create/order"] = FakeResp(200, {"success": True, "tracking": "ECW1"})
    oid = _world_order(app, "BD-W5")
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    assert called == []


def test_zr_order_never_goes_to_world(logged_in_admin, app, seed, api):
    oid = _world_order(app, "BD-Z1", carrier_code="zrexpress", carrier_name="ZR Express")
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    assert _creates(api) == []


def test_refresh_world_order_moves_to_delivered(logged_in_admin, app, seed, api):
    api.routes["/api/v1/get/orders"] = FakeResp(200, {"data": [
        {"tracking": "ECW1", "status": "livre_non_encaisse"}]})
    oid = _world_order(app, "BD-W6", status=Order.STATUS_SHIPPED, tracking_number="ECW1")
    logged_in_admin.post(f"/admin/orders/{oid}/refresh-tracking", follow_redirects=True)
    with app.app_context():
        o = db.session.get(Order, oid)
        assert o.carrier_state == "livre_non_encaisse"
        assert o.status == Order.STATUS_DELIVERED


def test_refresh_world_return_in_transit_does_not_restock(logged_in_admin, app, seed, api):
    api.routes["/api/v1/get/orders"] = FakeResp(200, {"data": [
        {"tracking": "ECW1", "status": "retour_chez_livreur"}]})
    oid = _world_order(app, "BD-W7", status=Order.STATUS_SHIPPED, tracking_number="ECW1")
    logged_in_admin.post(f"/admin/orders/{oid}/refresh-tracking", follow_redirects=True)
    with app.app_context():
        assert db.session.get(Order, oid).status == Order.STATUS_SHIPPED


def test_validate_route(logged_in_admin, app, seed, api):
    api.routes["/api/v1/valid/order"] = FakeResp(200, {"success": True, "message": "ok"})
    oid = _world_order(app, "BD-W8", tracking_number="ECW1")
    logged_in_admin.post(f"/admin/orders/{oid}/world-validate", follow_redirects=True)
    with app.app_context():
        assert db.session.get(Order, oid).carrier_state == "expediee"


def test_label_route_streams_pdf(logged_in_admin, app, seed, api):
    api.routes["/api/v1/get/order/label"] = FakeResp(200, content=b"%PDF-1.4 fake")
    oid = _world_order(app, "BD-W9", tracking_number="ECW1")
    r = logged_in_admin.get(f"/admin/orders/{oid}/world-label")
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert r.data.startswith(b"%PDF")


def test_world_admin_routes_require_login(client, app, seed):
    oid = _world_order(app, "BD-W10", tracking_number="ECW1")
    assert client.post(f"/admin/orders/{oid}/world-validate").status_code in (302, 400)
    r = client.get(f"/admin/orders/{oid}/world-label")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_order_page_shows_world_buttons(logged_in_admin, app, seed):
    oid = _world_order(app, "BD-W11", tracking_number="ECW1", carrier_state="prete_a_expedier")
    html = logged_in_admin.get(f"/admin/orders/{oid}").get_data(as_text=True)
    assert "بوردرو World الرسمي" in html and "تأكيد الإرسال" in html
    assert "بوردرو ZR الرسمي" not in html


def test_integrations_page_tests_world_connection(logged_in_admin, seed, api):
    api.routes["/api/v1/validate/token"] = FakeResp(200, {"success": True, "message": "VALID_TOKEN"})
    html = logged_in_admin.post("/admin/integrations", data={"test": "world"},
                                follow_redirects=True).get_data(as_text=True)
    assert "التوكن صالح" in html
