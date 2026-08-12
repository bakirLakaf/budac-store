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
