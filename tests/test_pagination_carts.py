"""
اختبارات الترقيم والسلات المتروكة (v141).
"""

import re

import pytest

from models import db, Product, Category, AbandonedCart, Order


@pytest.fixture
def catalog(app, seed):
    """23 منتجًا — يعطي 3 صفحات بـ9 فالصفحة."""
    with app.app_context():
        category = Category.query.filter_by(slug="decor").first()
        for i in range(22):
            db.session.add(Product(
                name=f"منتج {i}", slug=f"prod-{i}", price=100 + i,
                is_active=True,
                # نصفهم فالتصنيف ونصفهم بلا تصنيف — نختبرو العدّادين
                category_id=category.id if i % 2 == 0 else None,
            ))
        db.session.commit()
    return seed


# ══════════════════════════════════════════════════════════════
# الترقيم
# ══════════════════════════════════════════════════════════════

def _cards_in_grid(html):
    """
    عدد بطاقات المنتجات داخل شبكة الترقيم برك.

    ⚠️ الصفحة فيها أقسام أخرى تستعمل نفس الكلاس («الجديد»، «الأكثر
    مبيعًا»). نقصّو من فتح shopGrid حتى بداية الترقيم/الحالة الفارغة
    باش نعدّو الشبكة وحدها.
    """
    start = html.find('id="shopGrid"')
    if start == -1:
        return 0

    for marker in ('<nav class="pager"', 'class="pager-info"', '</section>'):
        end = html.find(marker, start)
        if end != -1:
            break
    else:
        end = len(html)

    return html[start:end].count('class="product-card"')


def test_first_page_shows_exactly_nine_products(client, catalog):
    from routes.shop import PRODUCTS_PER_PAGE

    body = client.get("/").get_data(as_text=True)
    assert _cards_in_grid(body) == PRODUCTS_PER_PAGE


def test_pages_two_and_three_work(client, catalog):
    for page in (2, 3):
        response = client.get(f"/?page={page}")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert f"صفحة {page} من 3" in body


def test_last_page_holds_the_remainder(client, catalog):
    """23 منتج ÷ 9 = صفحتان كاملتان + 5 فالثالثة."""
    body = client.get("/?page=3").get_data(as_text=True)
    assert _cards_in_grid(body) == 5


def test_pager_links_are_rendered_with_anchor(client, catalog):
    """
    ⚠️ الروابط لازم تحمل #products — بلاها الزبون يرجع لفوق الصفحة
    فكل نقرة ويلزمو يمرّر لتحت من جديد.
    """
    body = client.get("/?page=2").get_data(as_text=True)
    pager = re.search(r'<nav class="pager".*?</nav>', body, re.S).group(0)

    assert 'href="/?page=1#products"' in pager
    assert 'href="/?page=3#products"' in pager
    assert 'rel="prev"' in pager and 'rel="next"' in pager
    assert 'aria-current="page"' in pager


def test_first_page_has_no_prev_link(client, catalog):
    pager = re.search(
        r'<nav class="pager".*?</nav>', client.get("/").get_data(as_text=True), re.S
    ).group(0)
    assert 'rel="prev"' not in pager
    assert "pager-btn is-disabled" in pager


def test_pagination_preserves_the_category(client, catalog):
    """
    ⚠️ أهم اختبار هنا: لو الترقيم ينسى التصنيف، الزبون يفلتر ثم
    ينقر «2» ويلقى روحو فكل المنتجات — بق يبان تافهًا ويحرق الثقة.
    """
    body = client.get("/?category=decor").get_data(as_text=True)

    pager = re.search(r'<nav class="pager".*?</nav>', body, re.S)
    # ⚠️ بلا هاد التأكيد، الاختبار يمرّ بصمت لو الترقيم ما بانش
    # أصلاً — وهذا بالضبط اللي صرا: اختبار طفرة كشف أن `if pager:`
    # كان يخلّيه يعدّي على كود مكسور.
    assert pager is not None, (
        "الترقيم ما بانش — 12 منتج فالتصنيف لازم يعطيو صفحتين"
    )

    links = re.findall(r'href="([^"]+)"', pager.group(0))
    assert links, "ماكانش روابط فالترقيم"
    for link in links:
        assert "category=decor" in link, (
            f"رابط الترقيم نسي التصنيف: {link} — "
            "الزبون يفلتر، ينقر «2»، ويلقى روحو فكل المنتجات"
        )


def test_pagination_within_a_category_stays_in_that_category(client, catalog):
    """
    الوجه العملي: نتبّعو الرابط فعليًا ونتأكدو أن الصفحة 2 مازالت
    مفلترة — ماشي غير أن الرابط مكتوب صح.
    """
    body = client.get("/?category=decor&page=2").get_data(as_text=True)

    # الحالة النشطة تبقى على التصنيف
    assert re.search(r'side-cat is-active"[^>]*data-cat="decor"', body) \
        or 'data-cat="decor"' in body
    # وعدد النتائج يبقى تاع التصنيف (12) ماشي المجموع (23)
    assert "12 منتج" in body, "الصفحة 2 خرجت من التصنيف"


@pytest.mark.parametrize("query", [
    "?page=999", "?page=abc", "?page=-5", "?page=0", "?page=", "?page=1.5",
])
def test_invalid_page_numbers_do_not_crash(client, catalog, query):
    """رقم صفحة غالط = صفحة عادية، ماشي 500."""
    assert client.get("/" + query).status_code == 200


def test_out_of_range_page_shows_empty_not_error(client, catalog):
    body = client.get("/?page=999")
    assert body.status_code == 200
    assert _cards_in_grid(body.get_data(as_text=True)) == 0


def test_unknown_category_shows_helpful_empty_state(client, catalog):
    body = client.get("/?category=does-not-exist").get_data(as_text=True)
    assert "ماكاين حتى منتج فهاد التصنيف" in body
    assert "شوف كل المنتجات" in body


def test_sidebar_counts_are_real_totals_not_page_counts(client, catalog):
    """
    ⚠️ العدّاد لازم يعرض مجموع التصنيف كامل (12: منتج الـseed + 11
    من الـfixture)، ماشي عدد اللي بان فالصفحة (9). هاد الفرق هو
    السبب اللي خلّانا نحسبوه بـGROUP BY بدل ما نعدّو البطاقات.
    """
    counts = re.findall(
        r'side-cat-count">(\d+)<', client.get("/").get_data(as_text=True)
    )
    # 23 منتج كامل · 12 منهم فتصنيف decor (11 من هاد الاختبار + 1 من seed)
    assert counts[0] == "23", f"مجموع المنتجات غالط: {counts}"
    assert "12" in counts, f"عدد التصنيف غالط: {counts}"
    # ⚠️ الأهم: العدّاد ماشي 9 (عدد اللي بان فالصفحة)
    assert "9" not in counts, "العدّاد يعرض عدد الصفحة بدل مجموع التصنيف"


def test_total_count_includes_products_without_category(app, catalog):
    """
    ⚠️ منتج بلا تصنيف ما يبانش فـJOIN تاع العدّادات. لو حسبنا المجموع
    بجمع أعداد التصنيفات، يطلع أقل من الحقيقة.
    """
    from routes.shop import _category_counts

    with app.app_context():
        counts, total = _category_counts()
        assert total == 23
        assert sum(counts.values()) < total


def test_homepage_loads_only_one_page_of_products(app, catalog):
    """
    الهدف تاع الترقيم: السيرفر ما يحمّلش الكتالوج كامل.
    نتأكدو أن السياق فيه 9 منتجات برك.
    """
    from flask import template_rendered

    captured = []

    def record(sender, template, context, **extra):
        if template.name == "shop/home.html":
            captured.append(context)

    template_rendered.connect(record, app)
    try:
        app.test_client().get("/")
    finally:
        template_rendered.disconnect(record, app)

    assert captured, "القالب ما اترسمش"
    context = captured[0]
    assert len(context["products"]) == 9
    assert "all_products" not in context, (
        "all_products رجع — الكتالوج كامل يتحمّل من جديد"
    )


# ══════════════════════════════════════════════════════════════
# السلات المتروكة
# ══════════════════════════════════════════════════════════════

def _save_progress(client, phone, name="علي بن علي"):
    return client.post("/api/save-progress", json={
        "name": name, "phone": phone,
        "wilaya_name": "الوادي", "commune": "الوادي",
    })


def _fill_cart(client, seed):
    client.post("/cart/add", data={
        "kind": "product", "item_id": seed["product_id"], "quantity": "1",
    })


def test_abandoned_cart_is_saved_while_typing(client, app, seed):
    _fill_cart(client, seed)
    _save_progress(client, "0666255200")

    with app.app_context():
        assert AbandonedCart.query.count() == 1


def test_typing_the_phone_does_not_create_duplicate_carts(client, app, seed):
    """
    🔴 البق الحقيقي: /api/save-progress يتنادى **وقت الكتابة**.
    الزبون يوصل 9 أرقام → يتسجّل سطر بـ«066625520» → يكمّل الرقم
    العاشر → المقارنة النصية ما تلقاش السطر القديم → **سطر ثانٍ**.

    فزبون واحد يولّد سطرين، وكي يكمّل الطلبية واحد برك يتعلّم
    «استُرجعت» والثاني يبقى فقائمة المتابعة للأبد.
    """
    _fill_cart(client, seed)

    # نحاكيو الكتابة كيما يدير المتصفح: أرقام ناقصة ثم الرقم الكامل
    # بصيغ مختلفة
    _save_progress(client, "06662")
    _save_progress(client, "066625520")        # 9 أرقام — مازال ناقص
    _save_progress(client, "0666255200")       # كامل
    _save_progress(client, "0666 25 52 00")    # نفس الرقم بمسافات
    _save_progress(client, "+213666255200")    # نفس الرقم دوليًا

    with app.app_context():
        carts = AbandonedCart.query.all()
        assert len(carts) == 1, (
            f"تولّدو أسطر مكرّرة لنفس الزبون: "
            f"{[c.customer_phone for c in carts]}"
        )
        # ⚠️ يتخزّن موحّدًا باش الأدمن يشوف رقمًا نظيفًا يقدر يعيّط بيه
        assert carts[0].customer_phone == "0666255200"


def test_completing_checkout_clears_the_abandoned_cart(client, app, seed):
    """
    ⚠️ الطلب تاعك: كي يكمّل صاحبها الطلبية، السلة تتنحّى.
    نعلّموها «استُرجعت» — تختفي من قائمة الأدمن (اللي تعرض
    «الجديدة» افتراضيًا) وتبقى الإحصائية محفوظة.
    """
    _fill_cart(client, seed)
    _save_progress(client, "0666255200")

    with app.app_context():
        assert AbandonedCart.query.filter_by(
            status=AbandonedCart.STATUS_NEW
        ).count() == 1

    client.post("/checkout", data={
        "name": "علي بن علي", "phone": "0666255200",
        "wilaya_code": "39", "commune": "الوادي",
        "carrier_code": "zrexpress", "delivery_type": "home",
    })

    with app.app_context():
        assert Order.query.count() == 1
        assert AbandonedCart.query.filter_by(
            status=AbandonedCart.STATUS_NEW
        ).count() == 0, "السلة بقات فقائمة المتابعة رغم إتمام الطلبية"
        assert AbandonedCart.query.first().status == AbandonedCart.STATUS_RECOVERED


def test_cart_is_cleared_even_if_phone_format_differs(client, app, seed):
    """
    🔴 السبب الثاني اللي كان يخلّي السلات عالقة: المقارنة النصية.
    الزبون يكتب «0666 25 52 00» وقت التصفّح و«0666255200» فالطلبية —
    نفس الشخص، ونصّان مختلفان.
    """
    _fill_cart(client, seed)
    _save_progress(client, "0666 25 52 00")

    client.post("/checkout", data={
        "name": "علي بن علي", "phone": "+213666255200",
        "wilaya_code": "39", "commune": "الوادي",
        "carrier_code": "zrexpress", "delivery_type": "home",
    })

    with app.app_context():
        assert AbandonedCart.query.first().status == AbandonedCart.STATUS_RECOVERED


def test_landing_order_also_clears_the_abandoned_cart(client, app, seed):
    """
    ⚠️ صفحات الهبوط ما كانتش تنحّي السلات إطلاقًا: زبون يبدا فالمتجر
    (تتسجّل سلة)، يشوف إعلانًا، ويكمّل من صفحة الهبوط — والسلة تبقى.
    """
    _fill_cart(client, seed)
    _save_progress(client, "0666255200")

    client.post(f"/lp/{seed['page_slug']}", data={
        "name": "علي بن علي", "phone": "0666255200", "quantity": "1",
        "wilaya_code": "39", "commune": "الوادي",
        "carrier_code": "zrexpress", "delivery_type": "home",
    })

    with app.app_context():
        assert Order.query.count() == 1
        assert AbandonedCart.query.first().status == AbandonedCart.STATUS_RECOVERED


def test_other_customers_carts_are_untouched(client, app, seed):
    """⚠️ ما ننحّيوش سلات الناس الآخرين — كل واحد ورقمو."""
    from services.marketing import mark_cart_recovered

    _fill_cart(client, seed)
    _save_progress(client, "0666255200", name="علي")

    with app.app_context():
        db.session.add(AbandonedCart(
            customer_name="فاطمة", customer_phone="0777111222",
            cart_json="[]", cart_total=500, item_count=1,
        ))
        db.session.commit()

        mark_cart_recovered("0666255200", "BD-11112222")

        fatima = AbandonedCart.query.filter_by(customer_phone="0777111222").first()
        assert fatima.status == AbandonedCart.STATUS_NEW


@pytest.mark.parametrize("stored,completed", [
    ("0666255200", "0666255200"),
    ("0666255200", "0666 25 52 00"),
    ("0666255200", "+213666255200"),
    ("0666255200", "00213666255200"),
    ("0666 25 52 00", "0666255200"),
])
def test_mark_cart_recovered_matches_across_phone_formats(app, stored, completed):
    """
    ⚠️ اختبار عقد مباشر على الدالة، ماشي عبر الصفحات.

    علاش؟ اختبار طفرة كشف أن اختبار «الصيغ المختلفة» عبر الـcheckout
    كان يمرّ حتى لو رجّعنا المقارنة النصية — لأن الـcheckout يوحّد
    الرقم قبلها، فالصيغة ما توصلش أصلاً للدالة.

    هنا نناديها بصيغة خام مباشرة. لو رجعت المقارنة النصية، هاد
    الاختبار يصرخ.
    """
    from services.marketing import mark_cart_recovered

    with app.app_context():
        db.session.add(AbandonedCart(
            customer_name="علي", customer_phone=stored,
            cart_json="[]", cart_total=1200, item_count=1,
        ))
        db.session.commit()

        assert mark_cart_recovered(completed, "BD-11112222") == 1, (
            f"«{completed}» ما طابقش السلة المحفوظة بـ«{stored}»"
        )
        assert AbandonedCart.query.first().status == AbandonedCart.STATUS_RECOVERED


def test_mark_cart_recovered_ignores_blank_phone(app):
    from services.marketing import mark_cart_recovered

    with app.app_context():
        assert mark_cart_recovered("") == 0
        assert mark_cart_recovered(None) == 0
        assert mark_cart_recovered("abc") == 0
