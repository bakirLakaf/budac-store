"""
اختبارات منطق الأعمال — المخزون، الأسعار، الكوبونات، الإرسال.

⚠️ هاد الملف يحمي من نوع الأخطاء اللي كلّفت فلوسًا حقيقية:
صفحات هبوط ترفض كل طلبية، كوبون يتخطّى حدّو، طرد يتبعث مرتين.
"""

import pytest

from models import db, Product, Order, Coupon


def _valid_landing_form(**overrides):
    form = {
        "name": "علي بن علي",
        "phone": "0666255200",
        "quantity": "1",
        "wilaya_code": "39",
        "commune": "الوادي",
        "carrier_code": "zrexpress",
        "delivery_type": "home",
    }
    form.update(overrides)
    return form


# ══════════════════════════════════════════════════════════════
# H-2 — صفحات الهبوط كانت ترفض كل طلبية
# ══════════════════════════════════════════════════════════════

def test_landing_accepts_order_when_stock_tracking_off(client, app, seed):
    """
    🔴 العطل الأصلي: تتبّع المخزون مطفي (is_stock_tracked = False)
    فـstock_quantity = 0 لكل منتج. الفحص القديم كان `1 > 0` = صحيح
    دائمًا، فـ**كل** طلبية من صفحة الهبوط تترفض بـ400
    «المخزون المتوفر: 0 فقط» — والإعلانات تخلّص على فراغ.
    """
    response = client.post(f"/lp/{seed['page_slug']}", data=_valid_landing_form())

    body = response.get_data(as_text=True)
    assert "المخزون المتوفر: 0" not in body
    assert response.status_code == 302, f"الطلبية اترفضت: {body[:400]}"

    with app.app_context():
        assert Order.query.count() == 1


def test_landing_order_is_owned_by_the_browser_that_placed_it(client, app, seed):
    """بعد الطلب، صفحة الشكر لازم تعرض التفاصيل لصاحبها."""
    client.post(f"/lp/{seed['page_slug']}", data=_valid_landing_form())

    with app.app_context():
        number = Order.query.first().order_number

    body = client.get(
        f"/lp/{seed['page_slug']}/thanks?order={number}"
    ).get_data(as_text=True)
    assert number in body


def test_landing_respects_real_stock_when_tracking_is_on(client, app, seed, monkeypatch):
    """
    ⚠️ الإصلاح ما يخصّوش يلغي المخزون — يخصّو يحترم العلم.
    كي التتبّع يولّي ON، الكميات لازم تُحترم فعلاً.
    """
    monkeypatch.setattr(Product, "is_stock_tracked", True)

    with app.app_context():
        product = db.session.get(Product, seed["product_id"])
        product.stock_quantity = 3
        db.session.commit()

    # كمية داخل المخزون → تعدّي
    ok = client.post(f"/lp/{seed['page_slug']}", data=_valid_landing_form(quantity="2"))
    assert ok.status_code == 302

    # كمية فوق المخزون المتبقّي → تترفض
    with app.app_context():
        product = db.session.get(Product, seed["product_id"])
        product.stock_quantity = 1
        db.session.commit()

    blocked = client.post(f"/lp/{seed['page_slug']}", data=_valid_landing_form(quantity="5"))
    assert blocked.status_code == 400
    assert "متوفر منو غير" in blocked.get_data(as_text=True)


def test_landing_respects_max_quantity(client, seed):
    """حد الكمية تاع صفحة الهبوط يبقى يخدم."""
    response = client.post(
        f"/lp/{seed['page_slug']}", data=_valid_landing_form(quantity="9999")
    )
    assert response.status_code == 400
    assert "أقصى كمية" in response.get_data(as_text=True)


def test_stock_service_is_the_single_source_of_truth(app, seed, monkeypatch):
    """
    ⚠️ اختبار للسبب الجذري ماشي للعرَض: القاعدة كانت مكرّرة فـ4
    أماكن ووحدة نستها. نتأكدو أن الدالة المركزية تتصرّف صح
    فالحالتين، وأن كل من يحتاجها يناديها.
    """
    from services import stock as stock_service

    with app.app_context():
        product = db.session.get(Product, seed["product_id"])
        product.stock_quantity = 0
        db.session.commit()

        # التتبّع مطفي → 0 ما يمنعش
        assert stock_service.tracking_enabled() is False
        assert stock_service.check_availability(product, None, 5) is None
        assert stock_service.available_for(product) is None

        # التتبّع مفعّل → 0 يمنع
        monkeypatch.setattr(Product, "is_stock_tracked", True)
        assert stock_service.tracking_enabled() is True
        assert stock_service.check_availability(product, None, 1) is not None


# ══════════════════════════════════════════════════════════════
# الأسعار — الزبون ما يقدرش يبدّلها
# ══════════════════════════════════════════════════════════════

def test_client_cannot_forge_product_price(client, app, seed):
    """السعر يجي من قاعدة البيانات، ماشي من الفورم."""
    client.post("/cart/add", data={
        "kind": "product", "item_id": seed["product_id"],
        "quantity": "1", "price": "1",       # محاولة تزوير
        "unit_price": "1", "total": "1",
    })

    response = client.get("/api/cart")
    data = response.get_json()
    assert data["subtotal"] == 1200, f"السعر اتزوّر: {data}"


def test_client_cannot_forge_shipping_price(client, app, seed):
    """سعر التوصيل يُعاد حسابو فالسيرفر."""
    client.post("/cart/add", data={
        "kind": "product", "item_id": seed["product_id"], "quantity": "1",
    })

    client.post("/checkout", data={
        "name": "علي بن علي", "phone": "0666255200",
        "wilaya_code": "39", "commune": "الوادي",
        "carrier_code": "zrexpress", "delivery_type": "home",
        "shipping_price": "0", "total": "1",     # محاولة تزوير
    })

    with app.app_context():
        order = Order.query.first()
        assert order is not None, "الطلبية ما تسجّلاتش"
        assert order.shipping_price == 600, "سعر التوصيل اتزوّر"
        assert order.total == 1800


def test_landing_price_comes_from_the_page_not_the_form(client, app, seed):
    """نفس القاعدة على صفحات الهبوط."""
    client.post(f"/lp/{seed['page_slug']}", data=_valid_landing_form(
        unit_price="1", total="1", shipping_price="0",
    ))

    with app.app_context():
        order = Order.query.first()
        assert order.subtotal == 1200
        assert order.shipping_price == 600


# ══════════════════════════════════════════════════════════════
# M-2 — حد استعمال الكوبون
# ══════════════════════════════════════════════════════════════

def test_coupon_usage_limit_is_enforced(client, app, seed):
    """كوبون بحد 1 ما يتستعملش مرتين."""
    with app.app_context():
        db.session.add(Coupon(
            code="TEST10", discount_type="percent", value=10,
            is_active=True, usage_limit=1, used_count=0,
        ))
        db.session.commit()

    def place_order():
        client.post("/cart/add", data={
            "kind": "product", "item_id": seed["product_id"], "quantity": "1",
        })
        client.post("/cart/coupon", data={"code": "TEST10"})
        return client.post("/checkout", data={
            "name": "علي بن علي", "phone": "0666255200",
            "wilaya_code": "39", "commune": "الوادي",
            "carrier_code": "zrexpress", "delivery_type": "home",
        })

    place_order()
    place_order()

    with app.app_context():
        coupon = Coupon.query.filter_by(code="TEST10").first()
        assert coupon.used_count <= 1, f"الحد اتخطّى: {coupon.used_count}"


def test_coupon_counter_is_incremented_atomically(app, seed):
    """
    ⚠️ الطريقة القديمة (`used_count += 1`) تقرا ثم تكتب: طلبيات
    متوازية تقرا كلها نفس القيمة وتكتب نفس الزيادة، فالحد يتخطّى.

    نحاكيو الحالة: نضبطو العدّاد على الحد بالضبط، ثم نحاولو زيادة.
    الزيادة الذرية (UPDATE ... WHERE used_count < limit) لازم تفشل.
    """
    from sqlalchemy import update

    with app.app_context():
        coupon = Coupon(
            code="ATOMIC", discount_type="fixed", value=100,
            is_active=True, usage_limit=5, used_count=5,
        )
        db.session.add(coupon)
        db.session.commit()

        stmt = (
            update(Coupon)
            .where(Coupon.id == coupon.id, Coupon.used_count < Coupon.usage_limit)
            .values(used_count=Coupon.used_count + 1)
        )
        assert db.session.execute(stmt).rowcount == 0


def test_coupon_counter_not_incremented_when_order_fails(client, app, seed):
    """
    ⚠️ الوجه الثاني: العدّاد ما يخصّوش يزيد إذا الطلبية فشلت.
    الزيادة داخل نفس الـtransaction، فالـrollback يرجّعها.
    """
    with app.app_context():
        db.session.add(Coupon(
            code="ROLLBK", discount_type="percent", value=10,
            is_active=True, usage_limit=100, used_count=0,
        ))
        db.session.commit()

    from services.orders import create_order, OrderError
    from services.cart import CartLine

    with app.app_context():
        coupon = Coupon.query.filter_by(code="ROLLBK").first()
        bad_line = CartLine(
            key="product:999999:0", kind="product", name="غير موجود",
            unit_price=100, quantity=1, product_id=999999,
        )
        with pytest.raises(OrderError):
            create_order(
                lines=[bad_line],
                customer_data={
                    "name": "علي", "phone": "0666255200",
                    "wilaya_code": "39", "wilaya_name": "الوادي",
                    "commune": "الوادي", "address_note": None,
                },
                shipping_info={
                    "carrier_code": "zrexpress", "carrier_name": "ZR",
                    "delivery_type": "home", "price": 600,
                },
                coupon=coupon,
            )

        db.session.rollback()
        assert Coupon.query.filter_by(code="ROLLBK").first().used_count == 0


# ══════════════════════════════════════════════════════════════
# M-1 — إرسال مزدوج لشركة التوصيل
# ══════════════════════════════════════════════════════════════

def test_zr_claim_is_granted_only_once(app, seed):
    """
    🔴 نمط «افحص ثم نفّذ»: نقرة مزدوجة كانت تخلق **طردين حقيقيين**
    عند ZR — تخليص مرتين وزبون ياخذ نفس السلعة مرتين.

    الحجز درك يمرّ من قاعدة البيانات: واحد ينجح، الثاني يترفض.
    """
    from routes.admin import _claim_order_for_zr

    with app.app_context():
        order = Order(
            order_number="BD-90009000", customer_name="علي",
            customer_phone="0666255200", wilaya_code="39", wilaya_name="الوادي",
            subtotal=1200, shipping_price=600, total=1800,
            status=Order.STATUS_PENDING,
        )
        db.session.add(order)
        db.session.commit()
        order_id = order.id

        first_ok, _ = _claim_order_for_zr(order_id)
        second_ok, message = _claim_order_for_zr(order_id)

        assert first_ok is True
        assert second_ok is False, "الحجز الثاني نجح — ثغرة الإرسال المزدوج مازالت!"
        assert "الإرسال" in message


def test_failed_zr_send_releases_the_claim(app, seed):
    """
    ⚠️ الحجز ما يخصّوش يعلّق الطلبية للأبد لو فشل الـAPI.
    بعد الفشل، إعادة المحاولة لازم تخدم فورًا.
    """
    from routes.admin import _claim_order_for_zr, _release_zr_claim

    with app.app_context():
        order = Order(
            order_number="BD-91009100", customer_name="علي",
            customer_phone="0666255200", wilaya_code="39", wilaya_name="الوادي",
            subtotal=1200, shipping_price=600, total=1800,
            status=Order.STATUS_PENDING,
        )
        db.session.add(order)
        db.session.commit()
        order_id = order.id

        assert _claim_order_for_zr(order_id)[0] is True
        _release_zr_claim(order_id, "ZR ما جاوبش")

        retry_ok, _ = _claim_order_for_zr(order_id)
        assert retry_ok is True, "الطلبية بقات عالقة بعد فشل — ما تقدرش تعاود"

        assert db.session.get(Order, order_id).send_state == Order.ZR_SENDING


def test_already_sent_order_cannot_be_claimed(app, seed):
    """طلبية عندها رقم تتبّع = تبعثت خلاص، ما تتبعثش مرة ثانية."""
    from routes.admin import _claim_order_for_zr

    with app.app_context():
        order = Order(
            order_number="BD-92009200", customer_name="علي",
            customer_phone="0666255200", wilaya_code="39", wilaya_name="الوادي",
            subtotal=1200, shipping_price=600, total=1800,
            status=Order.STATUS_CONFIRMED,
            tracking_number="ZR123456", send_state=Order.ZR_SENT,
        )
        db.session.add(order)
        db.session.commit()

        ok, message = _claim_order_for_zr(order.id)
        assert ok is False
        assert "ZR123456" in message


# ══════════════════════════════════════════════════════════════
# أرقام الطلبيات
# ══════════════════════════════════════════════════════════════

def test_order_numbers_use_a_secure_generator(app):
    """
    ⚠️ الرقم ماشي كلمة سر (الصلاحية من الجلسة)، لكن مولّد آمن
    وفضاء أكبر يقلّلو التصادم والتوقّع.
    """
    from services.orders import generate_order_number

    with app.app_context():
        numbers = {generate_order_number() for _ in range(200)}

    assert len(numbers) == 200, "تصادم فأرقام الطلبيات"
    for number in numbers:
        assert number.startswith("BD-")
        assert len(number) == 11        # BD- + 8 أرقام
        assert number[3:].isdigit()


def test_old_short_order_numbers_still_work(client, app):
    """
    ⚠️ الطلبيات القديمة (BD-123456) لازم تبقى تتلقى — ما نكسروش
    بيانات موجودة باش نطوّلو الأرقام الجديدة.
    """
    with app.app_context():
        db.session.add(Order(
            order_number="BD-123456", customer_name="علي",
            customer_phone="0666255200", wilaya_code="39", wilaya_name="الوادي",
            subtotal=1200, shipping_price=600, total=1800,
            status=Order.STATUS_PENDING,
        ))
        db.session.commit()

    from services.order_access import find_order_by_phone
    with app.app_context():
        assert find_order_by_phone("BD-123456", "0666255200") is not None
        # صيغة دولية لنفس الرقم لازم تخدم كذلك
        assert find_order_by_phone("BD-123456", "+213666255200") is not None
        # هاتف غالط = لا
        assert find_order_by_phone("BD-123456", "0555000000") is None


# ══════════════════════════════════════════════════════════════
# اختبار تزامن حقيقي للكوبون
#
# ⚠️ لماذا هاد الاختبار موجود؟
# الاختبارات فوق (`test_coupon_usage_limit_is_enforced`) كانت تنجح
# **حتى مع الكود القديم غير الذري** — أثبتنا هذا باختبار طفرة
# (رجّعنا `used_count += 1` والاختبارات عدّات كلها).
#
# السبب: طلبات متسلسلة ما تكشفش سباقًا. كل طلب يقرا القيمة
# المحدَّثة، فالحد يبان محترمًا. السباق يبان **فقط** كي يقرا اثنان
# نفس القيمة قبل ما يكتب واحد منهم.
#
# فهنا نحاكيو ذلك صراحة بجلستين منفصلتين.
# ══════════════════════════════════════════════════════════════

def test_coupon_increment_detects_concurrent_stale_read(app):
    """
    السيناريو: كوبون بحد 5، مستعمل 4 مرات.
      • الجلسة A تقرا الكوبون (تشوف 4) — طلبية زبون أول
      • الجلسة B تحجز الاستعمال الأخير وتحفظ (يولّي 5)
      • الجلسة A تحاول تحجز

    الكود القديم (`used_count += 1`): A تكتب 5 اعتمادًا على قراءتها
    القديمة → طلبيتان استعملو خانة وحدة → الحد اتخطّى فعليًا.

    الكود الذري (`UPDATE ... WHERE used_count < limit`): A تلقى
    rowcount = 0 وترفض. هذا هو السلوك الصحيح.
    """
    from sqlalchemy import update
    from sqlalchemy.orm import Session

    with app.app_context():
        db.session.add(Coupon(
            code="RACE", discount_type="percent", value=10,
            is_active=True, usage_limit=5, used_count=4,
        ))
        db.session.commit()
        coupon_id = Coupon.query.filter_by(code="RACE").first().id
        engine = db.engine

    # جلستان مستقلتان تمامًا — كيما خيطان gunicorn المتوازية
    session_a = Session(engine)
    session_b = Session(engine)

    try:
        # A تقرا الكوبون (تشوف used_count = 4)
        coupon_a = session_a.get(Coupon, coupon_id)
        assert coupon_a.used_count == 4

        # B تحجز الاستعمال الأخير وتحفظ
        session_b.execute(
            update(Coupon)
            .where(Coupon.id == coupon_id, Coupon.used_count < Coupon.usage_limit)
            .values(used_count=Coupon.used_count + 1)
        )
        session_b.commit()

        # A تحاول تحجز بنفس الطريقة الذرية اللي فـcreate_order
        result = session_a.execute(
            update(Coupon)
            .where(Coupon.id == coupon_id, Coupon.used_count < Coupon.usage_limit)
            .values(used_count=Coupon.used_count + 1)
        )
        session_a.commit()

        assert result.rowcount == 0, (
            "الجلسة A نجحات تحجز رغم أن الكوبون كمل — الحد يتخطّى"
        )
    finally:
        session_a.close()
        session_b.close()

    with app.app_context():
        final = db.session.get(Coupon, coupon_id)
        assert final.used_count == 5, f"العدّاد تخطّى الحد: {final.used_count}"


def test_create_order_uses_atomic_coupon_update(app, seed):
    """
    ⚠️ اختبار بنيوي مكمّل: نتأكدو أن `create_order` تستعمل فعلاً
    الزيادة الذرية وماشي `+= 1`.

    اختبار السلوك وحدو ما يكفيش هنا — أثبتنا بالطفرة أنو يعدّي
    على الكود المكسور. فنفحصو المصدر مباشرة.
    """
    import inspect
    from services import orders

    source = inspect.getsource(orders.create_order)

    # ⚠️ نحيّدو التعليقات: التعليق اللي يشرح الغلطة القديمة يذكرها
    # نصًا، فالفحص الساذج يطيح عليه.
    code_only = "\n".join(
        line for line in source.split("\n")
        if not line.strip().startswith("#")
    )

    assert "coupon.used_count += 1" not in code_only, (
        "رجعت الزيادة غير الذرية — السباق يرجع معاها"
    )
    assert "update(Coupon)" in code_only
    assert "rowcount" in code_only
    assert "Coupon.used_count <" in code_only
