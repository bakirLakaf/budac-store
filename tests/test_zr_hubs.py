"""اختبارات بحث مكاتب ZR — الترقيم والمطابقة (تشخيص BD-69869090)."""
import pytest

from services import zrexpress as zr


def make_hubs(n=150, special_idx=140):
    """n hub؛ «Hub Ain Temouchent 46» فآخر القائمة (بعد الصفحة الأولى)."""
    hubs = []
    for i in range(n):
        hubs.append({
            "id": f"id-{i}", "name": f"Hub Filler {i % 58 + 1:02d} مكتب {i}",
            "isPickupPoint": True,
            "address": {"postalCode": f"{i % 58 + 1:02d}000"},
        })
    hubs[special_idx] = {
        "id": "ain-temouchent-uuid",
        "name": "Hub Ain Temouchent 46 مكتب عين تموشنت",
        "isPickupPoint": True, "address": {"postalCode": "46000"},
    }
    return hubs


@pytest.fixture(autouse=True)
def _clean():
    zr.clear_hubs_cache()
    yield
    zr.clear_hubs_cache()


def fake_server(monkeypatch, hubs, cap=100, honors_page=True, page_key="pageNumber"):
    calls = []

    def fake_request(method, path, **kw):
        assert path == "/hubs/search"
        body = kw["json"]
        calls.append(body)
        size = min(body.get("pageSize", cap), cap)
        page = body.get(page_key, 1) if honors_page else 1
        start = (page - 1) * size
        return {"items": hubs[start:start + size]}

    monkeypatch.setattr(zr, "_request", fake_request)
    return calls


def test_finds_hub_beyond_first_page(monkeypatch):
    """الغلطة الأصلية: ZR يقصّ الصفحة لـ100 والمكتب فالصفحة الثانية."""
    calls = fake_server(monkeypatch, make_hubs())
    hub_id, name = zr.find_hub_id("Hub Ain Temouchent 46 مكتب عين تموشنت", "46")
    assert hub_id == "ain-temouchent-uuid"
    assert len(zr.search_hubs()) == 150
    assert len(calls) >= 2


def test_cap_below_requested_page_size_still_paginates(monkeypatch):
    fake_server(monkeypatch, make_hubs(130, 125), cap=50)
    assert len(zr.search_hubs()) == 130


def test_single_page_stops_without_trying_other_styles(monkeypatch):
    calls = fake_server(monkeypatch, make_hubs(60, 10))
    assert len(zr.search_hubs()) == 60
    assert len(calls) == 2                      # الصفحة 1 + الصفحة الفارغة
    assert all("pageNumber" in c for c in calls)


def test_server_ignoring_page_param_does_not_loop_forever(monkeypatch):
    calls = fake_server(monkeypatch, make_hubs(), honors_page=False)
    hubs = zr.search_hubs()
    assert len(hubs) == 100                     # الصفحة الأولى برك، بلا تكرار
    assert len({h["id"] for h in hubs}) == 100
    assert len(calls) < 40


def test_alternate_pagination_style_is_discovered(monkeypatch):
    calls = []
    hubs = make_hubs()

    def fake_request(method, path, **kw):
        body = kw["json"]
        calls.append(body)
        if "pageIndex" in body:                  # الأسلوب الوحيد اللي يخدم
            start = (body["pageIndex"] - 1) * 100
            return {"items": hubs[start:start + 100]}
        return {"items": hubs[:100]}             # الباقي يتجاهل الصفحة

    monkeypatch.setattr(zr, "_request", fake_request)
    assert len(zr.search_hubs()) == 150


def test_hubs_are_cached(monkeypatch):
    calls = fake_server(monkeypatch, make_hubs(60, 10))
    zr.search_hubs()
    n = len(calls)
    zr.search_hubs()
    assert len(calls) == n


def test_api_error_propagates_when_nothing_loaded(monkeypatch):
    def boom(*a, **k):
        raise zr.ZRError("down")
    monkeypatch.setattr(zr, "_request", boom)
    with pytest.raises(zr.ZRError):
        zr.search_hubs()


# ── المطابقة ──

def test_match_ignores_accents_spacing_and_case():
    hubs = make_hubs()
    assert zr.find_hub_id("hub  ain temouchent 46 مكتب عين تموشنت", "46", hubs)[0] == "ain-temouchent-uuid"


def test_match_survives_small_rename_in_same_wilaya():
    hubs = make_hubs()
    got = zr.find_hub_id("Hub Ain Temouchent 46 مكتب عين تيموشنت", "46", hubs)
    assert got[0] == "ain-temouchent-uuid"


def test_single_pickup_hub_in_wilaya_is_used():
    hubs = make_hubs(40, 30)      # ولاية 46 فيها مكتب وحيد
    assert zr.find_hub_id("اسم مختلف تماما", "46", hubs)[0] == "ain-temouchent-uuid"


def test_ambiguity_returns_none_not_a_guess():
    hubs = make_hubs() + [{
        "id": "second-46", "name": "Hub Beni Saf 46 مكتب بني صاف",
        "isPickupPoint": True, "address": {"postalCode": "46000"}}]
    assert zr.find_hub_id("اسم مختلف تماما", "46", hubs) == (None, None)


def test_never_matches_other_wilaya_by_similarity():
    hubs = make_hubs()
    assert zr.find_hub_id("Hub Ain Temouchent 99 xxx", "99", hubs) == (None, None)


def test_resolve_hub_id_error_is_informative(monkeypatch):
    import types
    fake_server(monkeypatch, make_hubs(60, 10))
    order = types.SimpleNamespace(
        delivery_type="office", hub_external_id=None, pickup_point_id=None,
        pickup_point_name="Hub Inexistant 77 xx", wilaya_code="77")
    hub_id, reason = zr.resolve_hub_id(order)
    assert hub_id is None and "جبنا 60 hub" in reason
