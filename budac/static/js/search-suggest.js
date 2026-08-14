/**
 * البحث الفوري فالهيدر — قائمة اقتراحات تظهر وقت الكتابة.
 *
 * كيفاش يخدم:
 *   1. الزبون يكتب → نستنّاو 250ms (debounce) باش ما نقصفوش السيرفر
 *   2. نطلبو /api/search-suggest
 *   3. نعرضو 8 نتائج بالصورة والسعر
 *
 * التنقّل: الأسهم ↑↓، Enter يفتح المختار، Escape يسكّر.
 */
(function () {
    'use strict';

    var form = document.getElementById('headerSearch');
    var input = document.getElementById('searchInput');
    var box = document.getElementById('searchSuggest');

    if (!form || !input || !box) return;

    var API = form.getAttribute('data-suggest-url') || '/api/search-suggest';

    var timer = null;          // مؤقّت الـdebounce
    var controller = null;     // باش نلغيو الطلب القديم
    var items = [];            // النتائج الحالية
    var active = -1;           // العنصر المختار بالأسهم
    var lastQuery = '';

    /** يخبّي القائمة ويصفّي الحالة */
    function close() {
        box.hidden = true;
        box.innerHTML = '';
        input.setAttribute('aria-expanded', 'false');
        items = [];
        active = -1;
    }

    /** يهرّب النص باش ما يتفسّرش كـHTML (حماية من XSS) */
    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text == null ? '' : text;
        return div.innerHTML;
    }

    /** يلوّن الجزء المطابق من الاسم */
    function highlight(name, query) {
        var safeName = escapeHtml(name);
        if (!query) return safeName;

        var lower = name.toLowerCase();
        var pos = lower.indexOf(query.toLowerCase());
        if (pos === -1) return safeName;

        return escapeHtml(name.slice(0, pos))
            + '<mark>' + escapeHtml(name.slice(pos, pos + query.length)) + '</mark>'
            + escapeHtml(name.slice(pos + query.length));
    }

    /** يرسم النتائج فالقائمة */
    function render(results, query) {
        items = results || [];

        if (!items.length) {
            box.innerHTML = '<div class="search-suggest-empty">'
                + 'ما لقينا حتى منتج بـ«' + escapeHtml(query) + '»'
                + '</div>';
            box.hidden = false;
            input.setAttribute('aria-expanded', 'true');
            return;
        }

        var html = items.map(function (item, i) {
            var img = item.image
                ? '<img src="' + escapeHtml(item.image) + '" alt="" loading="lazy">'
                : '<span class="search-suggest-noimg">📦</span>';

            var stock = item.in_stock
                ? ''
                : '<span class="search-suggest-out">نفد</span>';

            return '<a class="search-suggest-item" role="option" id="sg-' + i + '"'
                + ' href="' + escapeHtml(item.url) + '" data-index="' + i + '">'
                + '<span class="search-suggest-img">' + img + '</span>'
                + '<span class="search-suggest-info">'
                + '<span class="search-suggest-name">' + highlight(item.name, query) + '</span>'
                + '<span class="search-suggest-meta">'
                + escapeHtml(item.price || '') + stock
                + '</span>'
                + '</span>'
                + '</a>';
        }).join('');

        // سطر أخير يودّي لصفحة النتائج الكاملة
        html += '<button type="submit" class="search-suggest-all">'
            + 'شوف كل النتائج ←'
            + '</button>';

        box.innerHTML = html;
        box.hidden = false;
        input.setAttribute('aria-expanded', 'true');
        active = -1;
    }

    /** يعلّم العنصر المختار بالأسهم */
    function setActive(index) {
        var nodes = box.querySelectorAll('.search-suggest-item');
        if (!nodes.length) return;

        // دوران: من الآخر للأول والعكس
        if (index < 0) index = nodes.length - 1;
        if (index >= nodes.length) index = 0;

        nodes.forEach(function (n) { n.classList.remove('is-active'); });
        nodes[index].classList.add('is-active');
        nodes[index].scrollIntoView({ block: 'nearest' });

        active = index;
        input.setAttribute('aria-activedescendant', 'sg-' + index);
    }

    /** يجيب الاقتراحات من السيرفر */
    function fetchSuggestions(query) {
        // نلغيو الطلب القديم — النتيجة القديمة ما بقاتش تهم
        if (controller) controller.abort();
        controller = new AbortController();

        fetch(API + '?q=' + encodeURIComponent(query), { signal: controller.signal })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                // نتأكد أن الرد ديال آخر كتابة، ماشي واحد قديم وصل متأخر
                if (data.query && data.query !== input.value.trim()) return;
                render(data.results, query);
            })
            .catch(function (err) {
                if (err.name !== 'AbortError') close();
            });
    }

    // ---------- الأحداث ----------

    input.addEventListener('input', function () {
        var query = input.value.trim();

        if (query === lastQuery) return;
        lastQuery = query;

        clearTimeout(timer);

        if (query.length < 2) {
            close();
            return;
        }

        // debounce: ما نطلبوش فكل حرف
        timer = setTimeout(function () { fetchSuggestions(query); }, 250);
    });

    input.addEventListener('keydown', function (e) {
        if (box.hidden) {
            // سهم تحت يعاود يفتح القائمة إذا كانت مسكّرة
            if (e.key === 'ArrowDown' && input.value.trim().length >= 2) {
                fetchSuggestions(input.value.trim());
                e.preventDefault();
            }
            return;
        }

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActive(active + 1);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActive(active - 1);
        } else if (e.key === 'Enter') {
            var nodes = box.querySelectorAll('.search-suggest-item');
            if (active >= 0 && nodes[active]) {
                e.preventDefault();
                window.location.href = nodes[active].getAttribute('href');
            }
            // ماكانش مختار → الفورم يتبعث عادي لصفحة النتائج
        } else if (e.key === 'Escape') {
            close();
            input.blur();
        }
    });

    // ضغطة برّا القائمة تسكّرها
    document.addEventListener('click', function (e) {
        if (!form.contains(e.target)) close();
    });

    // رجوع للحقل يعاود يعرض النتائج
    input.addEventListener('focus', function () {
        var query = input.value.trim();
        if (query.length >= 2 && !items.length) fetchSuggestions(query);
    });
})();
