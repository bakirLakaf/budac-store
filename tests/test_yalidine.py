"""اختبارات تكامل Yalidine Express — بلا شبكة: نزيّفو requests.request."""
import types

import pytest
import requests

from services import yalidine as yal


class FakeResp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self._data = data
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


WILAYAS = {"has_more": False, "data": [
    {"id": 16, "name": "Alger", "is_deliverable": 1},
    {"id": 5, "name": "Batna", "is_deliverable": 1},
]}
COMMUNES_16 = {"has_more": False, "data": [
    {"name": "Bab Ezzouar", "wilaya_id": 16, "wilaya_name": "Alger",
     "has_stop_desk": 1, "is_deliverable": 1},
    {"name": "Draria", "wilaya_id": 16, "wilaya_name": "Alger",
     "has_stop_desk": 0, "is_deliverable": 1},
    {"name": "Ben Aknoun", "wilaya_id": 16, "wilaya_name": "Alger",
     "has_stop_desk": 1, "is_deliverable": 0},
]}
CENTERS = {"has_more": False, "data": [
    {"center_id": 160101, "name": "Yalidine Bab Ezzouar", "address": "Cité 5",
     "commune_name": "Bab Ezzouar", "wilaya_id": 16, "wilaya_name": "Alger"},
    {"center_id": 160102, "name": "Yalidine Draria", "address": "RN 1",
     "commune_name": "Draria", "wilaya_id": 16, "wilaya_name": "Alger"},
]}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("YALIDINE_API_ID", "test-id-not-real")
    monkeypatch.setenv("YALIDINE_API_TOKEN", "test-token-not-real")
    monkeypatch.setenv("YALIDINE_FROM_WILAYA", "Batna")
    for k in ("YALIDINE_BASE_URL", "YALIDINE_FREESHIPPING", "YALIDINE_INSURANCE",
              "YALIDINE_WEIGHT", "YALIDINE_LENGTH", "YALIDINE_WIDTH", "YALIDINE_HEIGHT"):
        monkeypatch.delenv(k, raising=False)
    yal.clear_cache()
    yield
    yal.clear_cache()


@pytest.fixture
def api(monkeypatch):
    """يسجّل النداءات ويرجّع الردود حسب المسار (الأطول أولًا)."""
    calls = []
    routes = {}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        calls.append({"method": method, "url": url, "params": params,
                      "json": json, "headers": headers})
        path = url.split("/v1", 1)[1]
        for key in sorted(routes, key=len, reverse=True):
            if (key[0] is None or key[0] == method) and path.startswith(key[1]):
                resp = routes[key]
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"نداء غير متوقّع: {method} {url}")

    monkeypatch.setattr(yal.requests, "request", fake_request)

    class Routes(dict):
        def set(self, path, resp, method=None):
            self[(method, path)] = resp

    r = Routes()
    routes = r
    r.set("/wilayas/", FakeResp(200, WILAYAS))
    r.set("/communes/", FakeResp(200, COMMUNES_16))
    r.set("/centers/", FakeResp(200, CENTERS))
    return types.SimpleNamespace(calls=calls, routes=r)


def posts(api):
    return [c for c in api.calls if c["method"] == "POST" and c["url"].endswith("/parcels/")]


OK_CREATE = {"BD-1001": {"success": True, "order_id": "BD-1001", "tracking": "yal-AAA111",
                         "label": "https://yalidine.app/app/bordereau.php?tracking=yal-AAA111",
                         "message": ""}}


# ── الهاتف والاسم والفحص ──

@pytest.mark.parametrize("raw,expected", [
    ("0555 12 34 56", "0555123456"), ("+213555123456", "0555123456"),
    ("00213 6 61 23 45 67", "0661234567"), ("023456789", "023456789"),
    ("12345", None), ("", None), (None, None),
])
def test_normalize_phone(raw, expected):
    assert yal.normalize_phone(raw) == expected


def test_split_name():
    assert yal.split_name("محمد بن علي") == ("محمد", "بن علي")
    assert yal.split_name("Brahim Mohamed") == ("Brahim", "Mohamed")
    assert yal.split_name("كريم") == ("كريم", "كريم")


def test_check_order_data_collects_problems():
    problems = yal.check_order_data(fake_order(
        customer_phone="123", customer_name="ا", wilaya_code="99", total=0))
    assert len(problems) == 4


def test_check_order_data_ok_and_price_limit():
    assert yal.check_order_data(fake_order()) == []
    assert any("يفوق" in p for p in yal.check_order_data(fake_order(total=200000)))


def test_missing_from_wilaya_is_reported(monkeypatch):
    monkeypatch.delenv("YALIDINE_FROM_WILAYA")
    assert any("YALIDINE_FROM_WILAYA" in p for p in yal.check_order_data(fake_order()))


# ── البلديات ──

def test_find_commune_ignores_case_and_punctuation(api):
    assert yal.find_commune(16, ["bab-ezzouar"])["name"] == "Bab Ezzouar"


def test_find_commune_skips_undeliverable_and_unknown(api):
    assert yal.find_commune(16, ["Ben Aknoun"]) is None
    assert yal.find_commune(16, ["Nowhere"]) is None


def test_arabic_commune_is_translated_through_local_file(api):
    # «أدرار» موجودة فملف البلديات المحلي → Adrar
    order = fake_order(wilaya_code="01", commune="أدرار")
    assert "Adrar" in yal.commune_candidates(order)


def test_communes_are_cached(api):
    yal.get_communes(16)
    yal.get_communes(16)
    assert len([c for c in api.calls if "/communes/" in c["url"]]) == 1


def test_pagination_follows_has_more(api):
    api.routes.set("/centers/", FakeResp(200, {"has_more": True, "data": [{"center_id": 1}]}))
    pages = iter([
        FakeResp(200, {"has_more": True, "data": [{"center_id": 1, "name": "A"}]}),
        FakeResp(200, {"has_more": False, "data": [{"center_id": 2, "name": "B"}]}),
    ])

    def paged(*a, **k):
        return next(pages)

    import services.yalidine as m
    orig = m.requests.request

    def req(method, url, params=None, **kw):
        if "/centers/" in url:
            return paged()
        return orig(method, url, params=params, **kw)

    m.requests.request = req
    try:
        assert [c["center_id"] for c in yal.get_centers(force=True)] == [1, 2]
    finally:
        m.requests.request = orig


# ── إنشاء الطرد ──

def test_create_home_order_sends_expected_parcel(api):
    api.routes.set("/parcels/", FakeResp(200, OK_CREATE), "POST")
    result = yal.create_order(fake_order())
    assert result["tracking_number"] == "yal-AAA111"

    call = posts(api)[0]
    assert call["url"] == "https://api.yalidine.app/v1/parcels/"
    assert call["headers"]["X-API-ID"] == "test-id-not-real"
    assert call["headers"]["X-API-TOKEN"] == "test-token-not-real"
    assert isinstance(call["json"], list) and len(call["json"]) == 1
    p = call["json"][0]
    assert p["order_id"] == "BD-1001" and p["from_wilaya_name"] == "Batna"
    assert p["firstname"] == "محمد" and p["familyname"] == "بن علي"
    assert p["contact_phone"] == "0555123456"
    assert p["to_commune_name"] == "Bab Ezzouar" and p["to_wilaya_name"] == "Alger"
    assert p["price"] == 2500 and p["declared_value"] == 2500
    assert p["is_stopdesk"] is False and "stopdesk_id" not in p
    # القيم المنطقية لازم boolean حقيقية (Yalidine ترفض غير هكذا)
    for key in ("do_insurance", "freeshipping", "is_stopdesk", "has_exchange"):
        assert isinstance(p[key], bool), key
    assert p["freeshipping"] is True and p["do_insurance"] is False
    assert (p["weight"], p["length"], p["width"], p["height"]) == (1, 20, 15, 10)


def test_env_overrides_apply(api, monkeypatch):
    monkeypatch.setenv("YALIDINE_FREESHIPPING", "0")
    monkeypatch.setenv("YALIDINE_INSURANCE", "1")
    monkeypatch.setenv("YALIDINE_WEIGHT", "3")
    api.routes.set("/parcels/", FakeResp(200, OK_CREATE), "POST")
    yal.create_order(fake_order())
    p = posts(api)[0]["json"][0]
    assert p["freeshipping"] is False and p["do_insurance"] is True and p["weight"] == 3


def test_office_order_uses_center_and_stopdesk_id(api):
    api.routes.set("/parcels/", FakeResp(200, OK_CREATE), "POST")
    yal.create_order(fake_order(delivery_type="office",
                                pickup_point_name="Yalidine Bab Ezzouar", commune=None))
    p = posts(api)[0]["json"][0]
    assert p["is_stopdesk"] is True and p["stopdesk_id"] == 160101
    assert p["to_commune_name"] == "Bab Ezzouar"


def test_office_in_commune_without_stopdesk_is_refused(api):
    with pytest.raises(yal.YalidineError, match="استلام من المكتب"):
        yal.create_order(fake_order(delivery_type="office",
                                    pickup_point_name="Yalidine Draria", commune=None))
    assert posts(api) == []


def test_unknown_office_is_refused(api):
    with pytest.raises(yal.YalidineError, match="ما لقيناهش"):
        yal.create_order(fake_order(delivery_type="office",
                                    pickup_point_name="مكتب وهمي", commune=None))


def test_unknown_commune_is_refused_before_calling_api(api):
    with pytest.raises(yal.YalidineError, match="ما لقيناهاش"):
        yal.create_order(fake_order(commune="Nowhere"))
    assert posts(api) == []


def test_bad_data_is_rejected_before_calling_api(api):
    with pytest.raises(yal.YalidineError):
        yal.create_order(fake_order(customer_phone="1"))
    assert posts(api) == []


def test_parcel_failure_with_http_200_is_an_error(api):
    api.routes.set("/parcels/", FakeResp(200, {"BD-1001": {
        "success": False, "order_id": "BD-1001", "tracking": None,
        "message": "The do_insurance parameter must be of type boolean"}}), "POST")
    with pytest.raises(yal.YalidineError, match="do_insurance") as exc:
        yal.create_order(fake_order())
    assert exc.value.parcel_may_exist is False


def test_success_without_tracking_is_flagged(api):
    api.routes.set("/parcels/", FakeResp(200, {"BD-1001": {"success": True, "tracking": None}}),
                   "POST")
    with pytest.raises(yal.YalidineError) as exc:
        yal.create_order(fake_order())
    assert exc.value.parcel_may_exist is True


def test_read_timeout_recovers_if_parcel_exists(api):
    api.routes.set("/parcels/", requests.exceptions.ReadTimeout(), "POST")
    api.routes.set("/parcels/", FakeResp(200, {"data": [
        {"order_id": "BD-1001", "tracking": "yal-FOUND1"}]}), "GET")
    assert yal.create_order(fake_order())["tracking_number"] == "yal-FOUND1"


def test_read_timeout_flags_possible_duplicate_when_not_found(api):
    api.routes.set("/parcels/", requests.exceptions.ReadTimeout(), "POST")
    api.routes.set("/parcels/", FakeResp(200, {"data": []}), "GET")
    with pytest.raises(yal.YalidineError) as exc:
        yal.create_order(fake_order())
    assert exc.value.parcel_may_exist is True


def test_lookup_ignores_rows_of_other_orders(api):
    # لو الفلتر ما اشتغلش وجابت كل الطرود، ما ناخذوش طرد غيرنا
    api.routes.set("/parcels/", requests.exceptions.ReadTimeout(), "POST")
    api.routes.set("/parcels/", FakeResp(200, {"data": [
        {"order_id": "BD-9999", "tracking": "yal-OTHER"}]}), "GET")
    with pytest.raises(yal.YalidineError):
        yal.create_order(fake_order())


def test_connect_timeout_is_safe_to_retry(api):
    api.routes.set("/parcels/", requests.exceptions.ConnectTimeout(), "POST")
    with pytest.raises(yal.YalidineError) as exc:
        yal.create_order(fake_order())
    assert exc.value.parcel_may_exist is False


def test_server_error_on_create_is_flagged(api):
    api.routes.set("/parcels/", FakeResp(502, {}), "POST")
    api.routes.set("/parcels/", FakeResp(200, {"data": []}), "GET")
    with pytest.raises(yal.YalidineError) as exc:
        yal.create_order(fake_order())
    assert exc.value.parcel_may_exist is True


def test_bad_credentials_message_never_contains_secrets(api):
    api.routes.set("/parcels/", FakeResp(401, {"message": "Unauthorized"}), "POST")
    with pytest.raises(yal.YalidineError) as exc:
        yal.create_order(fake_order())
    msg = str(exc.value)
    assert "test-token-not-real" not in msg and "test-id-not-real" not in msg


def test_rate_limit_message(api):
    api.routes.set("/parcels/", FakeResp(429, {}, {"Retry-After": "30"}), "POST")
    with pytest.raises(yal.YalidineError, match="30"):
        yal.create_order(fake_order())


def test_missing_keys_raise(monkeypatch):
    monkeypatch.delenv("YALIDINE_API_TOKEN")
    assert yal.is_configured() is False
    with pytest.raises(yal.YalidineError):
        yal.get_wilayas()


def test_insecure_base_url_is_ignored(monkeypatch):
    monkeypatch.setenv("YALIDINE_BASE_URL", "http://evil.example")
    assert yal._base_url() == yal.DEFAULT_BASE_URL


# ── التتبّع والبوردرو والمكاتب ──

def test_get_order_status_reads_last_status(api):
    api.routes.set("/parcels/yal-AAA111/", FakeResp(200, {"data": [
        {"tracking": "yal-AAA111", "last_status": "Sorti en livraison"}]}))
    assert yal.get_order_status("yal-AAA111")["state"] == "Sorti en livraison"


def test_get_order_status_falls_back_to_histories(api):
    api.routes.set("/parcels/yal-AAA111/", FakeResp(200, {"data": [
        {"tracking": "yal-AAA111"}]}))
    api.routes.set("/histories/", FakeResp(200, {"data": [
        {"status": "Expédié", "date_creation": "2026-10-01 10:00:00"},
        {"status": "Livré", "date_creation": "2026-10-03 10:00:00"}]}))
    assert yal.get_order_status("yal-AAA111")["state"] == "Livré"


def test_label_url_must_be_yalidine_https(api):
    api.routes.set("/parcels/yal-AAA111/", FakeResp(200, {"data": [
        {"tracking": "yal-AAA111", "label": "https://yalidine.app/app/bordereau.php?tracking=x"}]}))
    assert yal.get_label_url("yal-AAA111").startswith("https://yalidine.app/")
    for bad in ("http://yalidine.app/x", "https://evil.com/yalidine.app", "https://yalidine.app.evil.com/x", ""):
        api.routes.set("/parcels/yal-AAA111/", FakeResp(200, {"data": [
            {"tracking": "yal-AAA111", "label": bad}]}))
        with pytest.raises(yal.YalidineError):
            yal.get_label_url("yal-AAA111")


def test_delete_parcel(api):
    api.routes.set("/parcels/yal-AAA111/", FakeResp(200, [
        {"tracking": "yal-AAA111", "deleted": True}]), "DELETE")
    assert yal.delete_parcel("yal-AAA111") is True


def test_centers_as_offices_shape(api):
    offices = yal.centers_as_offices()
    assert {o["name"] for o in offices} == {"Yalidine Bab Ezzouar", "Yalidine Draria"}
    first = offices[0]
    assert first["wilaya_code"] == "16" and first["pickup"] is True
    assert first["center_id"] == 160101


def test_offices_feed_the_shop_through_carrier_offices(api):
    from services.carrier_offices import get_pickup_points, has_offices
    assert has_offices("yalidine") is True
    points = get_pickup_points("yalidine", "16")
    assert len(points) == 2 and points[0]["wilaya_code"] == "16"
    assert get_pickup_points("yalidine", "05") == []


def test_offices_empty_when_not_configured(monkeypatch):
    monkeypatch.delenv("YALIDINE_API_ID")
    from services.carrier_offices import has_offices
    assert has_offices("yalidine") is False


def test_offices_empty_when_network_down(api):
    api.routes.set("/centers/", requests.exceptions.ConnectionError())
    from services.carrier_offices import has_offices
    assert has_offices("yalidine") is False


def test_test_connection_ok(api):
    ok, msg = yal.test_connection()
    assert ok is True and "صالحة" in msg


def test_test_connection_wrong_origin_wilaya(api, monkeypatch):
    monkeypatch.setenv("YALIDINE_FROM_WILAYA", "Atlantis")
    ok, msg = yal.test_connection()
    assert ok is False and "Atlantis" in msg


def test_test_connection_bad_keys(api):
    api.routes.set("/wilayas/", FakeResp(401, {}))
    ok, msg = yal.test_connection()
    assert ok is False and "test-token-not-real" not in msg


# ── ترجمة الحالات ──

@pytest.mark.parametrize("state,expected", [
    ("En préparation", None), ("En alerte", None), ("Echec livraison", None),
    ("Expédié", "shipped"), ("Transfert", "shipped"), ("Centre", "shipped"),
    ("Vers Wilaya", "shipped"), ("Sorti en livraison", "shipped"),
    ("En attente du client", "shipped"), ("Tentative échouée", "shipped"),
    ("Livré", "delivered"),
    ("Retour vers centre", None), ("Retourné au centre", None),
    ("Retour vers vendeur", None),
    ("Retourné au vendeur", "returned"),
    ("حاجة غريبة", None), ("", None), (None, None),
])
def test_map_state(state, expected):
    assert yal.map_state_to_order_status(state) == expected


# ════════════════ مسارات الأدمن (نفس ضمانات ZR/World) ════════════════

from models import Order, db   # noqa: E402


def _yal_order(app, number="BD-Y1", **kw):
    with app.app_context():
        data = dict(
            order_number=number, customer_name="زبون تجريبي", customer_phone="0555123456",
            wilaya_code="16", wilaya_name="الجزائر", commune="Bab Ezzouar",
            address_note="حي 5 جويلية", subtotal=2000, shipping_price=500, total=2500,
            status=Order.STATUS_PENDING, delivery_type="home",
            carrier_code="yalidine", carrier_name="Yalidine",
        )
        data.update(kw)
        o = Order(**data)
        db.session.add(o)
        db.session.commit()
        return o.id


def _ok_for(number):
    return FakeResp(200, {number: {"success": True, "order_id": number,
                                   "tracking": "yal-" + number.split("-")[1] + "ZZ", "label": "https://yalidine.app/x"}})


def test_send_yalidine_order_saves_tracking_and_confirms(logged_in_admin, app, seed, api):
    api.routes.set("/parcels/", _ok_for("BD-Y1"), "POST")
    oid = _yal_order(app)
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    with app.app_context():
        o = db.session.get(Order, oid)
        assert o.tracking_number == "yal-Y1ZZ"
        assert o.send_state == Order.ZR_SENT
        assert o.status == Order.STATUS_CONFIRMED
        assert o.carrier_state == "en_preparation"
    assert len(posts(api)) == 1


def test_double_send_creates_only_one_parcel(logged_in_admin, app, seed, api):
    api.routes.set("/parcels/", _ok_for("BD-Y2"), "POST")
    oid = _yal_order(app, "BD-Y2")
    for _ in range(2):
        logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    assert len(posts(api)) == 1


def test_failed_send_leaves_order_untouched_and_retryable(logged_in_admin, app, seed, api):
    api.routes.set("/parcels/", FakeResp(200, {"BD-Y3": {
        "success": False, "tracking": None, "message": "Phone invalide"}}), "POST")
    oid = _yal_order(app, "BD-Y3")
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    with app.app_context():
        o = db.session.get(Order, oid)
        assert o.tracking_number is None
        assert o.status == Order.STATUS_PENDING
        assert o.send_state == Order.ZR_FAILED
        assert "Phone invalide" in (o.send_error or "")
    api.routes.set("/parcels/", _ok_for("BD-Y3"), "POST")
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    with app.app_context():
        assert db.session.get(Order, oid).tracking_number == "yal-Y3ZZ"


def test_timeout_blocks_immediate_retry_to_avoid_duplicate(logged_in_admin, app, seed, api):
    api.routes.set("/parcels/", requests.exceptions.ReadTimeout(), "POST")
    api.routes.set("/parcels/", FakeResp(200, {"data": []}), "GET")
    oid = _yal_order(app, "BD-Y4")
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    with app.app_context():
        o = db.session.get(Order, oid)
        assert o.tracking_number is None
        assert o.send_state == Order.ZR_SENDING
        assert "Yalidine" in (o.send_error or "")
    api.routes.set("/parcels/", _ok_for("BD-Y4"), "POST")
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    assert len(posts(api)) == 1


def test_yalidine_order_never_goes_to_zr_or_world(logged_in_admin, app, seed, api, monkeypatch):
    called = []
    import services.zrexpress as zr
    import services.worldexpress as world
    monkeypatch.setattr(zr, "create_parcel", lambda *a, **k: called.append("zr"))
    monkeypatch.setattr(world, "create_order", lambda *a, **k: called.append("world"))
    api.routes.set("/parcels/", _ok_for("BD-Y5"), "POST")
    oid = _yal_order(app, "BD-Y5")
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    assert called == []


def test_zr_order_never_goes_to_yalidine(logged_in_admin, app, seed, api):
    oid = _yal_order(app, "BD-Z1", carrier_code="zrexpress", carrier_name="ZR Express")
    logged_in_admin.post(f"/admin/orders/{oid}/send-to-carrier", follow_redirects=True)
    assert posts(api) == []


def test_refresh_moves_order_to_delivered(logged_in_admin, app, seed, api):
    api.routes.set("/parcels/yal-X1/", FakeResp(200, {"data": [
        {"tracking": "yal-X1", "last_status": "Livré"}]}))
    oid = _yal_order(app, "BD-Y6", status=Order.STATUS_SHIPPED, tracking_number="yal-X1")
    logged_in_admin.post(f"/admin/orders/{oid}/refresh-tracking", follow_redirects=True)
    with app.app_context():
        o = db.session.get(Order, oid)
        assert o.carrier_state == "Livré"
        assert o.status == Order.STATUS_DELIVERED


def test_refresh_return_in_transit_does_not_restock(logged_in_admin, app, seed, api):
    api.routes.set("/parcels/yal-X1/", FakeResp(200, {"data": [
        {"tracking": "yal-X1", "last_status": "Retour vers vendeur"}]}))
    oid = _yal_order(app, "BD-Y7", status=Order.STATUS_SHIPPED, tracking_number="yal-X1")
    logged_in_admin.post(f"/admin/orders/{oid}/refresh-tracking", follow_redirects=True)
    with app.app_context():
        assert db.session.get(Order, oid).status == Order.STATUS_SHIPPED


def test_bulk_refresh_uses_the_right_carrier(logged_in_admin, app, seed, api, monkeypatch):
    # ZR لازم تكون «معمّرة» باش الزر الجماعي يخدم — نزيّفوها، وطلبيتنا تتفحص عند Yalidine
    import services.zrexpress as zr
    monkeypatch.setattr(zr, "is_configured", lambda: True)
    monkeypatch.setattr(zr, "get_parcel_status",
                        lambda t: (_ for _ in ()).throw(AssertionError("ما يمشيش لـZR")))
    api.routes.set("/parcels/yal-X2/", FakeResp(200, {"data": [
        {"tracking": "yal-X2", "last_status": "Livré"}]}))
    oid = _yal_order(app, "BD-Y8", status=Order.STATUS_SHIPPED, tracking_number="yal-X2")
    logged_in_admin.post("/admin/orders/refresh-all-tracking", follow_redirects=True)
    with app.app_context():
        assert db.session.get(Order, oid).status == Order.STATUS_DELIVERED


def test_label_route_redirects_to_yalidine(logged_in_admin, app, seed, api):
    api.routes.set("/parcels/yal-X1/", FakeResp(200, {"data": [
        {"tracking": "yal-X1", "label": "https://yalidine.app/app/bordereau.php?tracking=yal-X1"}]}))
    oid = _yal_order(app, "BD-Y9", tracking_number="yal-X1")
    r = logged_in_admin.get(f"/admin/orders/{oid}/yalidine-label")
    assert r.status_code == 302 and r.headers["Location"].startswith("https://yalidine.app/")


def test_label_route_requires_login_and_yalidine_order(client, logged_in_admin, app, seed, api):
    oid = _yal_order(app, "BD-Y10", tracking_number="yal-X1")
    assert client.get(f"/admin/orders/{oid}/yalidine-label").status_code == 302
    other = _yal_order(app, "BD-Z2", carrier_code="zrexpress", tracking_number="zr-1")
    r = logged_in_admin.get(f"/admin/orders/{other}/yalidine-label")
    assert r.status_code == 302 and "yalidine.app" not in r.headers["Location"]


def test_order_page_shows_yalidine_buttons(logged_in_admin, app, seed):
    oid = _yal_order(app, "BD-Y11", tracking_number="yal-X1")
    html = logged_in_admin.get(f"/admin/orders/{oid}").get_data(as_text=True)
    assert "بوردرو Yalidine الرسمي" in html
    assert "بوردرو ZR الرسمي" not in html and "بوردرو World الرسمي" not in html


def test_order_page_unsent_shows_send_button(logged_in_admin, app, seed):
    oid = _yal_order(app, "BD-Y12")
    html = logged_in_admin.get(f"/admin/orders/{oid}").get_data(as_text=True)
    assert "ابعث لـYalidine" in html and "YALIDINE_API_ID" in html


def test_integrations_page_tests_yalidine_connection(logged_in_admin, seed, api):
    html = logged_in_admin.post("/admin/integrations", data={"test": "yalidine"},
                                follow_redirects=True).get_data(as_text=True)
    assert "المفاتيح صالحة" in html


def test_from_wilaya_is_sent_with_yalidine_official_spelling(api, monkeypatch):
    api.routes.set("/wilayas/", FakeResp(200, {"has_more": False, "data": [
        {"id": 4, "name": "Oum El Bouaghi"}, {"id": 16, "name": "Alger"}]}))
    monkeypatch.setenv("YALIDINE_FROM_WILAYA", "Oum El-Bouaghi")
    api.routes.set("/parcels/", FakeResp(200, OK_CREATE), "POST")
    yal.create_order(fake_order())
    assert posts(api)[0]["json"][0]["from_wilaya_name"] == "Oum El Bouaghi"


# ── أسعار العقد ──

def test_rates_file_covers_all_58_wilayas_with_sane_prices():
    import json, os
    path = os.path.join(os.path.dirname(yal.__file__), "..", "data", "yalidine_rates.json")
    rows = json.load(open(path, encoding="utf-8"))
    assert len(rows) == 58 and len({r["wilaya_code"] for r in rows}) == 58
    assert all(0 < r["office"] < r["home"] for r in rows)
    by = {r["wilaya_code"]: r for r in rows}
    assert (by["04"]["home"], by["04"]["office"]) == (590, 450)      # أم البواقي
    assert (by["16"]["home"], by["16"]["office"]) == (700, 550)      # الجزائر
    assert (by["11"]["home"], by["11"]["office"]) == (1600, 1400)    # تمنراست


def test_sync_yalidine_rates_updates_only_what_differs(logged_in_admin, app, seed):
    from models import Carrier, ShippingRate
    with app.app_context():
        carrier = Carrier.query.filter_by(code="yalidine").first()
        if carrier is None:
            carrier = Carrier(code="yalidine", name="Yalidine Express", is_active=True)
            db.session.add(carrier)
            db.session.flush()
        db.session.add(ShippingRate(carrier_id=carrier.id, wilaya_code="04",
                                    wilaya_name="أم البواقي", home_price=999, office_price=999))
        db.session.commit()
    logged_in_admin.post("/admin/shipping/sync-file/yalidine", follow_redirects=True)
    with app.app_context():
        carrier = Carrier.query.filter_by(code="yalidine").first()
        rate = ShippingRate.query.filter_by(carrier_id=carrier.id, wilaya_code="04").first()
        assert (rate.home_price, rate.office_price) == (590, 450)
