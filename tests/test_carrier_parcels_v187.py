"""v187: تبويب «كل طرود شركات التوصيل» — توحيد الأشكال، عزل الأخطاء، ربط طلبيات المتجر."""
import pytest

from models import Order, db
from services import carrier_parcels as cp


@pytest.fixture(autouse=True)
def _fresh_cache():
    cp.clear_cache()
    yield
    cp.clear_cache()


# ───────────── توحيد الأسطر ─────────────
def test_normalize_yalidine():
    row = cp.normalize_yalidine({
        "tracking": "yal-111", "order_id": "BD-1", "firstname": "أحمد", "familyname": "بن علي",
        "contact_phone": "0555000000", "to_wilaya_name": "Batna", "to_commune_name": "Arris",
        "price": 2500, "last_status": "Livré", "date_creation": "2026-10-30 09:15:00"})
    assert row["carrier"] == "yalidine" and row["tracking"] == "yal-111"
    assert row["name"] == "أحمد بن علي" and row["amount"] == 2500.0
    assert row["category"] == "delivered" and row["date"] == "2026-10-30 09:15"


def test_normalize_world_uses_fallback_field_names():
    row = cp.normalize_world({
        "tracking": "WE-9", "reference": "X1", "client": "فاطمة", "phone": "0666",
        "commune": "Setif", "wilaya_id": 19, "montant": "1 800", "status": "livre_non_encaisse",
        "created_at": "2026-10-29T10:00:00Z"})
    assert row["tracking"] == "WE-9" and row["amount"] == 1800.0 and row["wilaya"] == "19"
    assert row["category"] == "delivered"


def test_normalize_zr_handles_nested_state_and_phone_objects():
    row = cp.normalize_zr({
        "trackingNumber": "ZR-5", "state": {"name": "x", "description": "En transit"},
        "customer": {"name": "كريم", "phone": {"number1": "0770000000"}},
        "deliveryAddress": {"city": "Alger", "district": "Bab Ezzouar"},
        "amount": 3000, "createdAt": "2026-10-28T08:00:00Z"})
    assert row["tracking"] == "ZR-5" and row["state"] == "En transit"
    assert row["name"] == "كريم" and row["phone"] == "0770000000"
    assert row["wilaya"] == "Alger" and row["commune"] == "Bab Ezzouar"


@pytest.mark.parametrize("carrier,state,expected", [
    ("yalidine", "Retourné au vendeur", "returned"),
    ("yalidine", "Expédié", "transit"),
    ("yalidine", "Annulé", "cancelled"),
    ("yalidine", "حالة جديدة ما نعرفوهاش", "other"),
    ("world", "retour_recu", "returned"),
])
def test_categorize(carrier, state, expected):
    assert cp.categorize(carrier, state) == expected


# ───────────── عزل الأخطاء + الذاكرة ─────────────
def test_one_carrier_failing_does_not_break_others(monkeypatch):
    def boom():
        raise RuntimeError("API طايح")
    monkeypatch.setitem(cp._FETCHERS, "zr", boom)
    monkeypatch.setitem(cp._FETCHERS, "yalidine", lambda: ([{"tracking": "Y1", "last_status": "Livré"}], None))
    assert cp.get("zr")["ok"] is False and "API طايح" in cp.get("zr")["error"]
    ok = cp.get("yalidine")
    assert ok["ok"] and len(ok["rows"]) == 1


def test_stale_cache_is_served_when_refresh_fails(monkeypatch):
    monkeypatch.setitem(cp._FETCHERS, "world", lambda: ([{"tracking": "W1", "status": "en_hub"}], None))
    assert cp.get("world")["ok"]

    def boom():
        raise RuntimeError("timeout")
    monkeypatch.setitem(cp._FETCHERS, "world", boom)
    again = cp.get("world", force=True)
    assert again["ok"] and again["stale"] and again["rows"][0]["tracking"] == "W1"
    assert "timeout" in again["error"]


def test_rows_sorted_newest_first_and_rows_without_tracking_dropped(monkeypatch):
    monkeypatch.setitem(cp._FETCHERS, "yalidine", lambda: ([
        {"tracking": "OLD", "date_creation": "2026-01-01 10:00:00"},
        {"tracking": "NEW", "date_creation": "2026-10-01 10:00:00"},
        {"last_status": "بلا تتبع"},
    ], None))
    assert [r["tracking"] for r in cp.get("yalidine")["rows"]] == ["NEW", "OLD"]


def test_unknown_carrier():
    assert cp.get("nope")["ok"] is False


# ───────────── الصفحة والـAPI ─────────────
def _order(num, tracking):
    db.session.add(Order(
        order_number=num, customer_name="زبون المتجر", customer_phone="0666000000",
        wilaya_code="16", wilaya_name="الجزائر", subtotal=1000, shipping_price=400,
        total=1400, status=Order.STATUS_SHIPPED, tracking_number=tracking))


def test_page_requires_login(client):
    r = client.get("/admin/orders/parcels")
    assert r.status_code == 302 and "login" in r.headers["Location"]
    assert client.get("/admin/orders/parcels/data/zr").status_code == 302


def test_page_renders_and_orders_list_links_to_it(logged_in_admin, seed):
    html = logged_in_admin.get("/admin/orders/parcels").get_data(as_text=True)
    assert "كل طرود شركات التوصيل" in html and "ZR Express" in html and "Yalidine" in html
    assert "/admin/orders/parcels" in logged_in_admin.get("/admin/orders").get_data(as_text=True)


def test_data_endpoint_marks_store_orders(logged_in_admin, app, seed, monkeypatch):
    with app.app_context():
        _order("BD-00000777", "YAL-STORE")
        db.session.commit()
    monkeypatch.setitem(cp._FETCHERS, "yalidine", lambda: ([
        {"tracking": "YAL-STORE", "last_status": "Expédié"},
        {"tracking": "YAL-EXTERNAL", "last_status": "Livré"},
    ], None))
    js = logged_in_admin.get("/admin/orders/parcels/data/yalidine").get_json()
    by = {r["tracking"]: r for r in js["rows"]}
    assert by["YAL-STORE"]["store_order_number"] == "BD-00000777"
    assert by["YAL-EXTERNAL"]["store_order_id"] is None
    assert "raw_sample" not in js and all("ts" not in r for r in js["rows"])


def test_debug_flag_returns_raw_sample_and_error_is_json(logged_in_admin, seed, monkeypatch):
    monkeypatch.setitem(cp._FETCHERS, "world", lambda: ([{"tracking": "W1", "status": "en_hub", "x": 1}], None))
    js = logged_in_admin.get("/admin/orders/parcels/data/world?debug=1").get_json()
    assert js["raw_sample"]["x"] == 1

    def boom():
        raise cp.ParcelsError("World Express ما هيش مربوطة")
    monkeypatch.setitem(cp._FETCHERS, "zr", boom)
    js = logged_in_admin.get("/admin/orders/parcels/data/zr").get_json()
    assert js["ok"] is False and "ما هيش مربوطة" in js["error"] and js["rows"] == []


def test_unknown_carrier_404(logged_in_admin, seed):
    assert logged_in_admin.get("/admin/orders/parcels/data/ups").status_code == 404
