"""لوحة الطلبيات: تبويب «الكل» ما يوريش تم التسليم والراجعة."""
from models import Order, db


def _make(num, status, name):
    db.session.add(Order(
        order_number=num, customer_name=name, customer_phone="0666000000",
        wilaya_code="16", wilaya_name="الجزائر", subtotal=1000, shipping_price=400,
        total=1400, status=status))


def _seed_orders(app):
    with app.app_context():
        _make("BD-00000001", Order.STATUS_PENDING, "زبون جديد")
        _make("BD-00000002", Order.STATUS_SHIPPED, "زبون فالطريق")
        _make("BD-00000003", Order.STATUS_DELIVERED, "زبون مسلَّم")
        _make("BD-00000004", Order.STATUS_RETURNED, "زبون راجع")
        _make("BD-00000005", Order.STATUS_CANCELLED, "زبون ملغي")
        db.session.commit()


def test_all_tab_hides_delivered_and_returned(logged_in_admin, app, seed):
    _seed_orders(app)
    html = logged_in_admin.get("/admin/orders").get_data(as_text=True)
    assert "زبون جديد" in html and "زبون فالطريق" in html and "زبون ملغي" in html
    assert "زبون مسلَّم" not in html and "زبون راجع" not in html


def test_all_tab_count_matches_visible_orders(logged_in_admin, app, seed):
    _seed_orders(app)
    html = logged_in_admin.get("/admin/orders").get_data(as_text=True)
    assert 'الكل <span class="tab-count">3</span>' in html


def test_delivered_and_returned_tabs_still_show_them(logged_in_admin, app, seed):
    _seed_orders(app)
    d = logged_in_admin.get("/admin/orders?status=delivered").get_data(as_text=True)
    r = logged_in_admin.get("/admin/orders?status=returned").get_data(as_text=True)
    assert "زبون مسلَّم" in d and "زبون راجع" in r


def test_search_still_finds_delivered_orders(logged_in_admin, app, seed):
    _seed_orders(app)
    html = logged_in_admin.get("/admin/orders?q=BD-00000003").get_data(as_text=True)
    assert "زبون مسلَّم" in html
