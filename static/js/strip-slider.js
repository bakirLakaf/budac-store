/* سلايدر شريط المنتجات (الأكثر طلبًا / وصل حديثًا)
 *
 * كيفاش يخدم:
 *   • 3 منتجات تبان (2 فالشاشات الصغيرة)، وكل مرة يمشي لـ«صفحة» الجاية.
 *   • تلقائي ويروح ويجي (ping-pong): 0 → 1 → 0 → 1... ماشي يقفز للبداية.
 *   • يوقف كي الزائر يحط الماوس/يلمس/يركّز، وكي التبويب يتخبّى،
 *     ومع «تقليل الحركة» (prefers-reduced-motion) ما يتحرّكش وحدو.
 *   • سهمين + نقاط + سحب بالإصبع.
 *
 * ⚠️ الصفحة RTL: المحتوى يبدا من اليمين، فالصفحة الجاية تتجاب بـtranslateX موجب.
 *    نحسبو الاتجاه من document.dir ماشي نفترضو.
 * ⚠️ الصفحة الأخيرة ما تخليش فراغ: 5 منتجات بـ3 = الصفحة 2 تبان آخر 3 (تتداخل).
 */
(function () {
    'use strict';

    var AUTOPLAY_MS = 4500;
    var reduceMotion = window.matchMedia &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var rtl = (document.documentElement.getAttribute('dir') || '').toLowerCase() === 'rtl';

    function init(root) {
        var viewport = root.querySelector('.ss-viewport');
        var track = root.querySelector('.ss-track');
        var items = Array.prototype.slice.call(track.children);
        var prev = root.querySelector('.ss-prev');
        var next = root.querySelector('.ss-next');
        var dotsBox = root.querySelector('.ss-dots');
        var wantAuto = root.getAttribute('data-autoplay') === '1' && !reduceMotion;

        var page = 0, dir = 1, timer = null, paused = false;
        var per = 3, pages = 1;

        function measure() {
            per = parseInt(getComputedStyle(root).getPropertyValue('--ss-per'), 10) || 3;
            pages = Math.max(1, Math.ceil(items.length / per));
            if (page > pages - 1) page = pages - 1;
            root.classList.toggle('ss-static', pages <= 1);
            buildDots();
            go(page, true);
        }

        function buildDots() {
            dotsBox.innerHTML = '';
            if (pages <= 1) return;
            for (var i = 0; i < pages; i++) {
                var b = document.createElement('button');
                b.type = 'button';
                b.className = 'ss-dot';
                b.setAttribute('aria-label', 'الصفحة ' + (i + 1));
                (function (n) { b.addEventListener('click', function () { go(n); restart(); }); })(i);
                dotsBox.appendChild(b);
            }
            dotsBox.removeAttribute('aria-hidden');
        }

        function go(n, instant) {
            page = Math.max(0, Math.min(pages - 1, n));
            var first = Math.min(page * per, Math.max(0, items.length - per));  // بلا فراغ فالآخر
            var step = items[0].getBoundingClientRect().width +
                (parseFloat(getComputedStyle(track).columnGap) || 0);
            var x = first * step * (rtl ? 1 : -1);
            track.style.transition = instant ? 'none' : '';
            track.style.transform = 'translateX(' + x + 'px)';
            if (instant) { void track.offsetWidth; track.style.transition = ''; }
            var dots = dotsBox.children;
            for (var i = 0; i < dots.length; i++) dots[i].classList.toggle('is-on', i === page);
            if (prev) prev.disabled = page === 0;
            if (next) next.disabled = page === pages - 1;
        }

        function step(delta) { go(page + delta); }

        // ping-pong: يوصل للآخر ← يرجع، يوصل للبداية ← يمشي
        function tick() {
            if (paused || pages <= 1) return;
            if (page + dir > pages - 1 || page + dir < 0) dir = -dir;
            go(page + dir);
        }
        function start() {
            if (wantAuto && !timer && pages > 1) timer = setInterval(tick, AUTOPLAY_MS);
        }
        function stop() { clearInterval(timer); timer = null; }
        function restart() { stop(); start(); }

        if (next) next.addEventListener('click', function () { dir = 1; step(1); restart(); });
        if (prev) prev.addEventListener('click', function () { dir = -1; step(-1); restart(); });

        root.addEventListener('mouseenter', function () { paused = true; });
        root.addEventListener('mouseleave', function () { paused = false; });
        root.addEventListener('focusin', function () { paused = true; });
        root.addEventListener('focusout', function () { paused = false; });
        document.addEventListener('visibilitychange', function () {
            if (document.hidden) stop(); else start();
        });

        // سحب بالإصبع/الماوس
        var sx = null;
        viewport.addEventListener('pointerdown', function (e) { sx = e.clientX; paused = true; });
        viewport.addEventListener('pointerup', function (e) {
            if (sx !== null && Math.abs(e.clientX - sx) > 50) {
                // RTL: السحب لليمين = الصفحة الجاية
                var forward = rtl ? (e.clientX - sx) > 0 : (e.clientX - sx) < 0;
                step(forward ? 1 : -1);
                dir = forward ? 1 : -1;
            }
            sx = null; paused = false; restart();
        });
        viewport.addEventListener('pointercancel', function () { sx = null; paused = false; });

        var resizeT;
        window.addEventListener('resize', function () {
            clearTimeout(resizeT);
            resizeT = setTimeout(measure, 150);
        });

        measure();
        start();
    }

    document.querySelectorAll('[data-ss]').forEach(init);
})();
