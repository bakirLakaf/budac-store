"""get_office لازم يقبل الاسم القصير والشكل المخزّن «الاسم — العنوان»."""
from services.carrier_offices import get_office


def test_short_name():
    assert get_office("worldexpress", "Tebessa", "12")["commune"] == "Tebessa"


def test_stored_label_with_address():
    label = "Tebessa — حي 414 سكن بجانب مركز البريد النهضة"
    assert get_office("worldexpress", label, "12")["commune"] == "Tebessa"


def test_wrong_wilaya_not_found():
    assert get_office("worldexpress", "Tebessa — x", "16") is None


def test_unknown_office():
    assert get_office("worldexpress", "Nowhere — x", "12") is None
