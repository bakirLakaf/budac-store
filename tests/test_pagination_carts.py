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
# الترقيم — فالمتصفح
# ══════════════════════════════════════════════════════════════
#
# ⚠️ الطراز تبدّل: الترقيم انتقل من السيرفر للمتصفح باش تبديل
# الصفحات يولّي فوريًا (بلا إعادة تحميل، بلا قفزة تمرير).
#
# يعني الاختبارات القديمة اللي كانت تفحص «9 بطاقات فالـHTML»
# ولّات غالطة: السيرفر درك يبعث **كل** المنتجات وJavaScript يخبّي
# اللي ماشي فالصفحة الحالية.
#
# ما نقدروش نشغّلو JavaScript هنا، فنختبرو:
#   • السيرفر يبعث كل المنتجات (المادة الخام)
#   • الإعدادات اللي يقرا منها السكريبت موجودة وصحيحة
#   • الحارس تاع الكتالوج الضخم يخدم
#   • الاحتياط بلا JavaScript سليم


def _grid_cards(html):
    """عدد بطاقات المنتجات داخل شبكة المتجر برك."""
    start_index = html.find('id="shopGrid"')
    if start_index == -1:
        return 0

    for marker in ('id="shopPager"', '<nav class="pager"', 'class="pager-info"', '</section>'):
        end_index = html.find(marker, start_index)
        if end_index != -1:
            break
    else:
        end_index = len(html)

    return html[start_index:end_index].count('class="product-card"')


def test_server_sends_the_whole_catalog_for_client_pagination(client, catalog):
    """
    ⚠️ هذا هو التبديل الأساسي: الصفحة تحمل كل المنتجات مرة وحدة،
    وJavaScript يقسّمهم. بلا هذا، تبديل الصفحة يرجع لإعادة تحميل.
    """
    body = client.get("/").get_data(as_text=True)
    assert _grid_cards(body) == 23, (
        "السيرفر ما بعثش الكتالوج كامل — التبديل الفوري ما يخدمش"
    )


def test_grid_carries_the_settings_the_script_reads(client, catalog):
    """
    السكريبت يقرا `data-per-page` و`data-client-paginated` من الشبكة.
    لو تنحاو، الترقيم ما يبانش إطلاقًا والزائر يشوف 23 منتج دفعة.
    """
    from routes.shop import PRODUCTS_PER_PAGE

    body = client.get("/").get_data(as_text=True)
    assert f'data-per-page="{PRODUCTS_PER_PAGE}"' in body
    assert 'data-client-paginated="1"' in body
    assert 'id="shopPager"' in body
    assert 'id="shopResultCount"' in body


def test_every_card_carries_its_category_for_instant_filtering(client, catalog):
    """
    الفلترة الفورية تعتمد على data-cat فكل بطاقة. بلاها، النقر على
    تصنيف يخبّي كلشي.
    """
    body = client.get("/").get_data(as_text=True)
    assert body.count('data-cat="decor"') >= 12


def test_huge_catalog_falls_back_to_server_pagination(client, app, seed):
    """
    ⚠️ الحارس: فوق MAX_CLIENT_PAGINATION، بعث الكتالوج كامل يولّي
    مكلفًا على اتصال الجوال. السيرفر يرجع للترقيم تاعو تلقائيًا —
    الموقع يبطّئ شوية فالتبديل، لكنو ما ينكسرش.
    """
    from routes.shop import MAX_CLIENT_PAGINATION, PRODUCTS_PER_PAGE

    with app.app_context():
        for i in range(MAX_CLIENT_PAGINATION + 10):
            db.session.add(Product(
                name=f"ضخم {i}", slug=f"huge-{i}", price=100, is_active=True,
            ))
        db.session.commit()

    body = client.get("/").get_data(as_text=True)

    assert 'data-client-paginated="0"' in body, "الحارس ما اشتغلش"
    assert _grid_cards(body) == PRODUCTS_PER_PAGE
    # وروابط السيرفر ترجع مع مرساتها
    assert "#products" in body


def test_server_pagination_fallback_still_paginates(client, app, seed):
    """الاحتياط لازم يخدم فعلاً، ماشي غير يبان."""
    from routes.shop import MAX_CLIENT_PAGINATION

    with app.app_context():
        for i in range(MAX_CLIENT_PAGINATION + 10):
            db.session.add(Product(
                name=f"ضخم {i}", slug=f"huge-{i}", price=100, is_active=True,
            ))
        db.session.commit()

    body = client.get("/?page=2").get_data(as_text=True)
    assert "صفحة 2 من" in body


@pytest.mark.parametrize("query", [
    "?page=999", "?page=abc", "?page=-5", "?page=0", "?page=", "?page=1.5",
])
def test_invalid_page_numbers_do_not_crash(client, catalog, query):
    """رقم صفحة غالط = صفحة عادية، ماشي 500."""
    assert client.get("/" + query).status_code == 200


def test_direct_category_link_still_filters_server_side(client, catalog):
    """
    ⚠️ الزواحف (جوجل) والروابط المشاركة ماكانش عندهم JavaScript —
    ?category= لازم يبقى يخدم من السيرفر.
    """
    body = client.get("/?category=decor").get_data(as_text=True)
    assert _grid_cards(body) == 12, "الفلترة من السيرفر ما خدماتش"


def test_unknown_category_shows_helpful_empty_state(client, catalog):
    body = client.get("/?category=does-not-exist").get_data(as_text=True)
    assert "ماكاين حتى منتج فهاد التصنيف" in body
    assert "شوف كل المنتجات" in body


def test_sidebar_counts_are_real_totals(client, catalog):
    """
    العدّاد يعرض مجموع التصنيف، ماشي عدد اللي بان فالصفحة.
    """
    counts = re.findall(
        r'side-cat-count">(\d+)<', client.get("/").get_data(as_text=True)
    )
    assert counts[0] == "23", f"مجموع المنتجات غالط: {counts}"
    assert "12" in counts, f"عدد التصنيف غالط: {counts}"


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


# ---------- سلوك السكريبت (فحص بنيوي) ----------

def _site_js():
    import os
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "js", "site.js",
    )
    return open(path, encoding="utf-8").read()


def test_script_respects_the_server_fallback_flag():
    """
    ⚠️ لو السكريبت تجاهل data-client-paginated، على كتالوج ضخم
    راح يرقّم الـ9 اللي بعثهم السيرفر ويعرض «صفحة 1 من 1» —
    والزائر ما يشوف أبدًا باقي المنتجات.
    """
    source = _site_js()
    assert "data-client-paginated" in source
    assert "!== '1') return" in source


def test_script_updates_the_url_without_reloading():
    """
    زر الرجوع تاع المتصفح ونسخ الرابط لازم يبقاو يخدمو رغم أن
    ماكانش إعادة تحميل.
    """
    source = _site_js()
    assert "history.pushState" in source
    assert "history.replaceState" in source
    assert "popstate" in source


def test_script_does_not_hijack_ctrl_click():
    """
    ⚠️ preventDefault على كل نقرة يكسر «فتح فتبويب جديد» —
    عادة شائعة عند الزوار، وكسرها يبان كأن الموقع معطّل.
    """
    source = _site_js()
    assert "event.metaKey || event.ctrlKey" in source


def test_pager_is_hidden_when_script_does_not_run(client, catalog):
    """
    ⚠️ حاوية الترقيم تبدا hidden ويعمّرها JavaScript. لو السكريبت
    ما حمّلش، الزائر يشوف كل المنتجات — طويل، لكن سليم. المهم
    ما يشوفش ترقيم فارغ ولا مكسور.
    """
    body = client.get("/").get_data(as_text=True)
    assert re.search(r'<nav class="pager" id="shopPager"[^>]*hidden', body)


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


# ══════════════════════════════════════════════════════════════
# «شفتها مؤخرًا» — إشارة الاستقرار
# ══════════════════════════════════════════════════════════════
#
# ⚠️ هاد القسم جالس فوق شبكة المنتجات ويتعمّر بعد fetch، فيدفع
# كلشي لتحت. مع الترقيم فالمتصفح ماكانش إعادة تحميل، فالمشكل
# خفّ بزاف — لكن الإشارة تبقى مفيدة لأي كود يحتاج يعرف كي
# يستقرّ تخطيط الصفحة.

def test_recently_viewed_signals_on_every_exit_path():
    """
    `settled()` لازم تتنادى فـ**كل** طريق خروج — نجاح، فشل شبكة،
    نتيجة فارغة، ماكانش القسم، localStorage معطوب.
    """
    source = _site_js()
    start = source.index("function initRecentlyViewed")
    body = source[start:source.index("\n    }", start)]

    lines = body.split("\n")
    unguarded = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        if "return;" not in stripped:
            continue
        window = stripped + " " + (lines[index - 1].strip() if index else "")
        if "settled()" not in window:
            unguarded.append(stripped)

    assert not unguarded, f"طرق خروج بلا إشارة استقرار: {unguarded}"
    assert ".catch(" in body and body.count("settled();") >= 5
    assert "budac:recent-settled" in body


def test_products_anchor_has_css_scroll_margin():
    """شبكة أمان بلا JavaScript للروابط اللي فيها #products."""
    import os
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "css", "style.css",
    )
    css = open(path, encoding="utf-8").read()
    assert "#products {" in css
    assert "scroll-margin-top" in css


def test_hidden_cards_are_actually_hidden_in_css():
    """
    ⚠️ الشبكة display:grid تغلب display:none تاع [hidden] فبعض
    المتصفحات — فالبطاقات «المخبّية» تبقى بانة والترقيم يبان مكسور.
    """
    import os
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "css", "style.css",
    )
    css = open(path, encoding="utf-8").read()
    assert ".product-card[hidden]" in css
