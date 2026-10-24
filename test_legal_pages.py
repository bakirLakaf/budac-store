"""صفحتا الخصوصية والشروط (تيك توك وMeta يطلبوهم لمراجعة التطبيق)."""


def test_privacy_and_terms_pages(client):
    r = client.get("/page/privacy")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "سياسة الخصوصية" in html and "عنوان IP" in html and "شركات التوصيل" in html
    assert "وما تجمعش" in html  # أدوات السوشيال ما تجمعش بيانات الزبائن

    r = client.get("/page/terms")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "شروط الاستخدام" in html and "/page/returns" in html and "/page/privacy" in html


def test_footer_links_to_legal_pages(client):
    html = client.get("/").get_data(as_text=True)
    assert "/page/privacy" in html and "/page/terms" in html


def test_unknown_static_page_still_404(client):
    assert client.get("/page/nope").status_code == 404
