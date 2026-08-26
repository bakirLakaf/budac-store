/*
 * سكريبت الموقع — خفيف وبلا أي مكتبة خارجية.
 * سبب هذا القرار: jQuery ولا سلايدر جاهز يزيدو 80-100 كيلوبايت للتحميل،
 * وكلشي هنا يقدر يتعمل بـ40 سطر جافاسكريبت عادي.
 */

(function () {
    "use strict";

    // واش المستخدم طالب تقليل الحركة (إعداد فالنظام)؟ نحترموه.
    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    /* ═══════════ 1. سلايدر العروض ═══════════ */
    function initHeroSlider() {
        var slider = document.getElementById("heroSlider");
        var track = document.getElementById("heroTrack");
        if (!slider || !track) return;

        var slides = track.querySelectorAll(".hero-slide");
        if (slides.length < 2) return;

        var dots = slider.querySelectorAll(".hero-dot");
        var arrows = slider.querySelectorAll(".hero-arrow");
        var current = 0;
        var timer = null;
        var AUTOPLAY_MS = 6000;

        function goTo(index) {
            // نلف بشكل دائري: بعد الأخيرة نرجعو للأولى
            current = (index + slides.length) % slides.length;

            // فـRTL الاتجاه معكوس — نستعملو موجب باش ينزلق صح
            track.style.transform = "translateX(" + (current * 100) + "%)";

            dots.forEach(function (dot, i) {
                dot.classList.toggle("is-active", i === current);
            });
        }

        function next() { goTo(current + 1); }

        function startAutoplay() {
            if (reduceMotion) return;
            stopAutoplay();
            timer = setInterval(next, AUTOPLAY_MS);
        }
        function stopAutoplay() {
            if (timer) { clearInterval(timer); timer = null; }
        }

        arrows.forEach(function (arrow) {
            arrow.addEventListener("click", function () {
                goTo(current + parseInt(arrow.dataset.dir, 10));
                startAutoplay();  // نعاودو العد من الصفر بعد تدخل المستخدم
            });
        });

        dots.forEach(function (dot) {
            dot.addEventListener("click", function () {
                goTo(parseInt(dot.dataset.index, 10));
                startAutoplay();
            });
        });

        // نوقفو التشغيل التلقائي كي الماوس فوق السلايدر — باش الزبون يقرا براحتو
        slider.addEventListener("mouseenter", stopAutoplay);
        slider.addEventListener("mouseleave", startAutoplay);

        // نوقفوه كي التبويب مخفي — ما فايدة نستهلكو موارد على صفحة ما يشوفهاش حد
        document.addEventListener("visibilitychange", function () {
            document.hidden ? stopAutoplay() : startAutoplay();
        });

        // السحب بالإصبع فالموبايل
        var startX = null;
        track.addEventListener("touchstart", function (e) {
            startX = e.touches[0].clientX;
            stopAutoplay();
        }, { passive: true });

        track.addEventListener("touchend", function (e) {
            if (startX === null) return;
            var diff = e.changedTouches[0].clientX - startX;
            if (Math.abs(diff) > 50) {
                // فـRTL: السحب لليسار = التالي
                goTo(current + (diff < 0 ? 1 : -1));
            }
            startX = null;
            startAutoplay();
        }, { passive: true });

        goTo(0);
        startAutoplay();
    }

    /* ═══════════ 2. ظهور الأقسام عند التمرير ═══════════ */
    function initScrollReveal() {
        var items = document.querySelectorAll(".reveal");
        if (!items.length) return;

        // IntersectionObserver أخف بكثير من الاستماع لحدث scroll
        if (!("IntersectionObserver" in window) || reduceMotion) {
            items.forEach(function (el) { el.classList.add("is-visible"); });
            return;
        }

        var observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add("is-visible");
                    observer.unobserve(entry.target);  // مرة وحدة تكفي
                }
            });
        }, { threshold: 0.08, rootMargin: "0px 0px -40px 0px" });

        items.forEach(function (el) { observer.observe(el); });
    }

    /* ═══════════ 3. منتجات شفتها مؤخرًا ═══════════ */
    function initRecentlyViewed() {
        var section = document.getElementById("recentlyViewed");
        var grid = document.getElementById("recentlyViewedGrid");
        if (!section || !grid) return;

        var KEY = "budac_recent";
        var slugs;

        try {
            slugs = JSON.parse(localStorage.getItem(KEY) || "[]");
        } catch (e) {
            return;
        }

        if (!slugs.length) return;

        fetch("/api/products-by-slug?slugs=" + encodeURIComponent(slugs.join(",")))
            .then(function (r) { return r.json(); })
            .then(function (products) {
                if (!products.length) return;

                grid.innerHTML = products.map(function (p) {
                    var media = p.image
                        ? '<img src="' + p.image + '" alt="" loading="lazy">'
                        : '<span class="ph-monogram">' + p.letter + "</span>";

                    return '<a href="' + p.url + '" class="product-card">' +
                        '<div class="product-card-image">' + media + "</div>" +
                        '<div class="product-card-body">' +
                        '<h3 class="product-card-name">' + p.name + "</h3>" +
                        '<p class="product-card-price">' + p.price + "</p>" +
                        "</div></a>";
                }).join("");

                section.hidden = false;
            })
            .catch(function () { /* فشل الشبكة — نخليو القسم مخفي */ });

        var clearBtn = document.getElementById("clearRecent");
        if (clearBtn) {
            clearBtn.addEventListener("click", function () {
                localStorage.removeItem(KEY);
                section.hidden = true;
            });
        }
    }

    /* ═══════════ 4. إخفاء رسائل flash تلقائيًا ═══════════ */
    function initFlashAutoHide() {
        document.querySelectorAll(".flash").forEach(function (flash) {
            setTimeout(function () {
                flash.style.transition = "opacity .4s ease, transform .4s ease";
                flash.style.opacity = "0";
                flash.style.transform = "translateY(-10px)";
                setTimeout(function () { flash.remove(); }, 400);
            }, 5000);
        });
    }

    /* ═══════════ التشغيل ═══════════ */
    function init() {
        initHeroSlider();
        initScrollReveal();
        initRecentlyViewed();
        initFlashAutoHide();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();

/* ════════════════════════════════════════════════════════════
   لوحة السلة الجانبية (v15)
   تتفتح فنفس الصفحة بلا انتقال. كل الأسعار تجي من السيرفر —
   ما نحسبو والو فالمتصفح باش الزبون ما يقدرش يبدّلهم.
   ════════════════════════════════════════════════════════════ */
(function () {
    var btn      = document.getElementById('cartFloatBtn');
    var drawer   = document.getElementById('cartDrawer');
    var overlay  = document.getElementById('cartOverlay');
    var closeBtn = document.getElementById('cartDrawerClose');
    var body     = document.getElementById('cartDrawerBody');
    var foot     = document.getElementById('cartDrawerFoot');
    var subtotal = document.getElementById('cartDrawerSubtotal');
    var floatCount = document.getElementById('cartFloatCount');

    if (!btn || !drawer) return;

    var tokenMeta = document.querySelector('meta[name="csrf-token"]');
    var csrfToken = tokenMeta ? tokenMeta.getAttribute('content') : '';
    var isOpen = false;

    function openDrawer() {
        isOpen = true;
        overlay.hidden = false;
        drawer.hidden = false;
        // نأخّر إضافة الكلاس إطار واحد باش الانتقال (transition) يبان
        requestAnimationFrame(function () {
            overlay.classList.add('is-open');
            drawer.classList.add('is-open');
        });
        document.body.classList.add('drawer-open');
        loadCart();
    }

    function closeDrawer() {
        isOpen = false;
        overlay.classList.remove('is-open');
        drawer.classList.remove('is-open');
        document.body.classList.remove('drawer-open');
        setTimeout(function () {
            if (!isOpen) { overlay.hidden = true; drawer.hidden = true; }
        }, 250);
    }

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str == null ? '' : str;
        return div.innerHTML;
    }

    function render(data) {
        if (!data.lines || data.lines.length === 0) {
            body.innerHTML = '<p class="cart-drawer-empty">سلتك خاوية.<br>تصفّح المنتجات وزيد اللي عجبك.</p>';
            foot.hidden = true;
        } else {
            var html = '';
            data.lines.forEach(function (line) {
                var img = line.image
                    ? '<img class="cd-line-img" src="/static/img/products/' + escapeHtml(line.image) + '" alt="" loading="lazy">'
                    : '<div class="cd-line-img"></div>';
                html +=
                    '<div class="cd-line" data-key="' + escapeHtml(line.key) + '">' +
                        img +
                        '<div class="cd-line-info">' +
                            '<p class="cd-line-name">' + escapeHtml(line.name) + '</p>' +
                            (line.variant_label ? '<p class="cd-line-variant">' + escapeHtml(line.variant_label) + '</p>' : '') +
                            '<p class="cd-line-price">' + escapeHtml(line.line_total_display) + '</p>' +
                            '<div class="cd-line-actions">' +
                                '<button type="button" class="cd-qty-btn" data-act="dec">−</button>' +
                                '<span class="cd-qty-val">' + line.quantity + '</span>' +
                                '<button type="button" class="cd-qty-btn" data-act="inc">+</button>' +
                                '<button type="button" class="cd-remove" data-act="del">حذف</button>' +
                            '</div>' +
                        '</div>' +
                    '</div>';
            });
            body.innerHTML = html;
            subtotal.textContent = data.subtotal_display;
            foot.hidden = false;
        }
        updateCounts(data.count);
    }

    function updateCounts(count) {
        if (floatCount) {
            floatCount.textContent = count;
            floatCount.classList.toggle('has-items', count > 0);
        }
        // نحدّثو حتى عدّاد الهيدر باش الرقمين ما يختلفوش
        var headerCount = document.querySelector('.cart-link .cart-count');
        if (headerCount) {
            headerCount.textContent = count;
            headerCount.classList.toggle('has-items', count > 0);
        }
    }

    function loadCart() {
        body.innerHTML = '<p class="cart-drawer-loading">جاري التحميل…</p>';
        fetch('/api/cart', { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.json(); })
            .then(render)
            .catch(function () {
                body.innerHTML = '<p class="cart-drawer-empty">تعذّر تحميل السلة. حاول من جديد.</p>';
            });
    }

    function changeQty(key, quantity) {
        fetch('/api/cart/update', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ key: key, quantity: quantity })
        })
            .then(function (r) { return r.json(); })
            .then(render)
            .catch(function () { loadCart(); });
    }

    // تفويض الأحداث: زر واحد يخدم على كل الأسطر حتى بعد إعادة الرسم
    body.addEventListener('click', function (e) {
        var target = e.target.closest('[data-act]');
        if (!target) return;

        var lineEl = target.closest('.cd-line');
        if (!lineEl) return;

        var key = lineEl.getAttribute('data-key');
        var current = parseInt(lineEl.querySelector('.cd-qty-val').textContent, 10) || 1;
        var act = target.getAttribute('data-act');

        if (act === 'inc')      changeQty(key, current + 1);
        else if (act === 'dec') changeQty(key, current - 1);
        else if (act === 'del') changeQty(key, 0);
    });

    btn.addEventListener('click', openDrawer);
    closeBtn.addEventListener('click', closeDrawer);
    overlay.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && isOpen) closeDrawer();
    });
})();

/* ════════════════════════════════════════════════════════════
   الفلترة الفورية حسب التصنيف (v17)
   كل المنتجات مطبوعة فالصفحة، والفلترة تصرا فالمتصفح — بلا إعادة تحميل.
   الرابط يتحدّث بـhistory.pushState باش الزبون يقدر يشاركو ويرجع للخلف.
   ════════════════════════════════════════════════════════════ */
(function () {
    var grid = document.getElementById('shopGrid');
    if (!grid) return;

    var cards      = Array.prototype.slice.call(grid.querySelectorAll('.product-card'));
    var countEl    = document.getElementById('shopResultCount');
    var emptyEl    = document.getElementById('shopEmptyFilter');
    var triggers   = document.querySelectorAll('.side-cat[data-cat], .category-card[data-cat]');
    var section    = document.getElementById('products');

    function applyFilter(slug, pushUrl) {
        var visible = 0;

        cards.forEach(function (card) {
            var match = (slug === 'all') || (card.getAttribute('data-cat') === slug);
            card.classList.toggle('is-filtered-out', !match);
            if (match) visible++;
        });

        // نحدّثو حالة الأزرار فالجهتين (الشريط الجانبي وأزرار الهاتف)
        triggers.forEach(function (el) {
            el.classList.toggle('is-active', el.getAttribute('data-cat') === slug);
        });

        if (countEl) {
            if (visible === 0)      countEl.textContent = '';
            else if (visible === 1)  countEl.textContent = 'منتج واحد';
            else if (visible === 2)  countEl.textContent = 'منتجان';
            else if (visible <= 10)  countEl.textContent = visible + ' منتجات';
            else                     countEl.textContent = visible + ' منتج';
        }
        if (emptyEl) emptyEl.hidden = visible !== 0;

        if (pushUrl) {
            var url = (slug === 'all')
                ? window.location.pathname
                : window.location.pathname + '?category=' + encodeURIComponent(slug);
            history.pushState({ cat: slug }, '', url);
        }
    }

    triggers.forEach(function (el) {
        el.addEventListener('click', function (e) {
            e.preventDefault();
            var slug = el.getAttribute('data-cat');
            applyFilter(slug, true);

            // نمرّرو لقسم المنتجات باش الزبون يشوف النتيجة فورًا
            if (section) {
                var top = section.getBoundingClientRect().top + window.scrollY - 20;
                window.scrollTo({ top: top, behavior: 'smooth' });
            }
        });
    });

    // زر "رجوع" فالمتصفح يرجّع الفلتر السابق
    window.addEventListener('popstate', function () {
        var params = new URLSearchParams(window.location.search);
        applyFilter(params.get('category') || 'all', false);
    });

    // الحالة الأولى: من الرابط
    var initial = new URLSearchParams(window.location.search).get('category') || 'all';
    applyFilter(initial, false);
})();


/**
 * سلايدر تقييمات الزبائن — يمشي وحدو.
 *
 * ⚠️ قرارات:
 *   • بلا مكتبة خارجية — الصفحة الرئيسية لازم تحمّل بسرعة
 *   • يوقف عند المرور بالفأرة ولا التركيز — الزائر يقرا براحتو
 *   • يوقف كي التبويب مخفي — ما نضيّعوش بطارية الموبايل
 *   • السحب بالإصبع يخدم طبيعي (scroll-snap تاع CSS)
 *   • يحترم prefers-reduced-motion
 */
(function () {
    'use strict';

    var viewport = document.getElementById('testiViewport');
    var track = document.getElementById('testiTrack');
    var dotsBox = document.getElementById('testiDots');

    if (!viewport || !track) return;

    var cards = track.querySelectorAll('.testi-card');
    if (cards.length < 2) return;   // بطاقة وحدة ما تحتاجش سلايدر

    var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var timer = null;
    var paused = false;

    /** كم بطاقة تبان فالشاشة درك */
    function perView() {
        var cardWidth = cards[0].getBoundingClientRect().width;
        if (!cardWidth) return 1;
        return Math.max(1, Math.round(viewport.clientWidth / cardWidth));
    }

    function pageCount() {
        return Math.max(1, Math.ceil(cards.length / perView()));
    }

    function currentPage() {
        var pageWidth = viewport.clientWidth || 1;
        // ⚠️ RTL: scrollLeft يجي سالب فمعظم المتصفحات
        return Math.round(Math.abs(viewport.scrollLeft) / pageWidth);
    }

    function goTo(page) {
        var total = pageCount();
        var target = ((page % total) + total) % total;   // دوران
        var offset = target * viewport.clientWidth;

        viewport.scrollTo({
            left: isRTL() ? -offset : offset,
            behavior: reduced ? 'auto' : 'smooth',
        });
    }

    function isRTL() {
        return getComputedStyle(document.documentElement).direction === 'rtl';
    }

    // ---------- النقاط ----------

    function buildDots() {
        var total = pageCount();
        dotsBox.innerHTML = '';

        if (total < 2) return;

        for (var i = 0; i < total; i++) {
            var dot = document.createElement('button');
            dot.type = 'button';
            dot.className = 'testi-dot';
            dot.setAttribute('aria-label', 'التقييم ' + (i + 1));
            dot.dataset.page = i;
            dotsBox.appendChild(dot);
        }
        markActive();
    }

    function markActive() {
        var active = currentPage();
        dotsBox.querySelectorAll('.testi-dot').forEach(function (d, i) {
            d.classList.toggle('is-on', i === active);
        });
    }

    // ---------- التشغيل التلقائي ----------

    function start() {
        if (reduced || timer) return;
        timer = setInterval(function () {
            if (!paused && !document.hidden) goTo(currentPage() + 1);
        }, 5000);
    }

    function stop() {
        clearInterval(timer);
        timer = null;
    }

    // ---------- الأحداث ----------

    ['mouseenter', 'focusin', 'touchstart'].forEach(function (ev) {
        viewport.addEventListener(ev, function () { paused = true; }, { passive: true });
    });
    ['mouseleave', 'focusout', 'touchend'].forEach(function (ev) {
        viewport.addEventListener(ev, function () { paused = false; }, { passive: true });
    });

    viewport.addEventListener('scroll', function () {
        clearTimeout(viewport._t);
        viewport._t = setTimeout(markActive, 90);
    }, { passive: true });

    dotsBox.addEventListener('click', function (e) {
        var dot = e.target.closest('.testi-dot');
        if (dot) goTo(parseInt(dot.dataset.page, 10));
    });

    window.addEventListener('resize', function () {
        clearTimeout(window._testiR);
        window._testiR = setTimeout(buildDots, 200);
    });

    buildDots();
    start();
})();
