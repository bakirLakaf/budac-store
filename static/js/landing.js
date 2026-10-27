/**
 * صفحة الهبوط — منطق الفورم والحساب.
 *
 * مسؤولياتها:
 *   • تحميل البلديات / المكاتب حسب الولاية ونوع التوصيل
 *   • حساب سعر التوصيل والمجموع مباشرة
 *   • العدّاد التنازلي
 *   • أحداث البكسل (InitiateCheckout عند بداية التعمير)
 */
(function () {
    'use strict';

    var CFG = window.LP_CONFIG;
    if (!CFG) return;

    var form = document.getElementById('lpForm');
    var carrier = document.getElementById('carrierSelect');
    var wilaya = document.getElementById('wilayaSelect');
    var deliveryType = document.getElementById('deliveryTypeSelect');
    var communeField = document.getElementById('communeField');
    var communeSelect = document.getElementById('communeSelect');
    var communeIdInput = document.getElementById('communeIdInput');
    var pickupField = document.getElementById('pickupField');
    var pickupSelect = document.getElementById('pickupSelect');
    var pickupIdInput = document.getElementById('pickupIdInput');
    var qtyInput = document.getElementById('qtyInput');
    var submitBtn = document.getElementById('lpSubmit');

    var sumProduct = document.getElementById('sumProduct');
    var sumShipping = document.getElementById('sumShipping');
    var sumTotal = document.getElementById('sumTotal');

    var shippingPrice = null;   // null = مازال ما تحسبش

    function formatDZD(n) {
        return Number(n).toLocaleString('en-US') + ' دج';
    }

    function quantity() {
        var q = parseInt(qtyInput && qtyInput.value, 10);
        return (isNaN(q) || q < 1) ? 1 : q;
    }

    // ---------- الحساب ----------

    function updateTotals() {
        var productTotal = CFG.unitPrice * quantity();
        sumProduct.textContent = formatDZD(productTotal);

        if (shippingPrice === null) {
            sumShipping.textContent = 'اختر الولاية';
            sumTotal.textContent = '—';
            return;
        }

        sumShipping.textContent = shippingPrice === 0 ? 'مجاني' : formatDZD(shippingPrice);
        sumTotal.textContent = formatDZD(productTotal + shippingPrice);
    }

    function updateShipping() {
        // ⚠️ السعر يختلف بين المنزل والمكتب، فلازم الثلاثة يكونو مختارين
        if (!carrier.value || !wilaya.value || !deliveryType.value) {
            shippingPrice = null;
            updateTotals();
            return;
        }

        sumShipping.textContent = '...';

        var url = CFG.urls.shipping
            + '?carrier=' + encodeURIComponent(carrier.value)
            + '&wilaya=' + encodeURIComponent(wilaya.value)
            + '&type=' + encodeURIComponent(deliveryType.value);

        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.available === false || data.price === null || data.price === undefined) {
                    shippingPrice = null;
                    sumShipping.textContent = data.message || 'غير متوفر';
                    sumTotal.textContent = '—';
                    return;
                }
                shippingPrice = CFG.freeShipping ? 0 : data.price;
                updateTotals();
            })
            .catch(function () {
                shippingPrice = null;
                sumShipping.textContent = 'خطأ فالحساب';
            });
    }

    // ---------- البلديات والمكاتب ----------

    function fillSelect(select, items, placeholder, savedValue) {
        select.innerHTML = '';

        var first = document.createElement('option');
        first.value = '';
        first.textContent = placeholder;
        select.appendChild(first);

        items.forEach(function (item) {
            var opt = document.createElement('option');
            opt.value = item.name;
            opt.textContent = item.name;
            if (item.id) opt.setAttribute('data-id', item.id);
            if (item.available === false) {
                opt.disabled = true;
                opt.textContent += ' (ما يوصلش)';
            }
            if (savedValue && item.name === savedValue) opt.selected = true;
            select.appendChild(opt);
        });
    }

    function loadCommunes() {
        if (!carrier.value || !wilaya.value) return;

        communeSelect.innerHTML = '<option value="">...</option>';

        var url = CFG.urls.communes
            + '?carrier=' + encodeURIComponent(carrier.value)
            + '&wilaya=' + encodeURIComponent(wilaya.value)
            + '&type=home';

        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var list = (data.communes || []).map(function (c) {
                    return { name: c.name, id: c.id, available: c.available };
                });
                fillSelect(communeSelect, list, 'اختر البلدية', CFG.saved.commune);
                syncCommuneId();
            })
            .catch(function () {
                communeSelect.innerHTML = '<option value="">ما قدرناش نحمّلو البلديات</option>';
            });
    }

    function loadPickupPoints() {
        if (!carrier.value || !wilaya.value) return;

        pickupSelect.innerHTML = '<option value="">...</option>';

        var url = CFG.urls.pickupPoints
            + '?carrier=' + encodeURIComponent(carrier.value)
            + '&wilaya=' + encodeURIComponent(wilaya.value);

        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var list = (data.points || data.pickup_points || []).map(function (p) {
                    return { name: p.name, id: p.id };
                });
                if (!list.length) {
                    pickupSelect.innerHTML = '<option value="">ماكانش مكاتب فهاد الولاية</option>';
                    return;
                }
                fillSelect(pickupSelect, list, 'اختر المكتب', CFG.saved.pickup);
                syncPickupId();
            })
            .catch(function () {
                pickupSelect.innerHTML = '<option value="">ما قدرناش نحمّلو المكاتب</option>';
            });
    }

    function syncCommuneId() {
        var opt = communeSelect.options[communeSelect.selectedIndex];
        communeIdInput.value = opt ? (opt.getAttribute('data-id') || '') : '';
    }

    function syncPickupId() {
        var opt = pickupSelect.options[pickupSelect.selectedIndex];
        pickupIdInput.value = opt ? (opt.getAttribute('data-id') || '') : '';
    }

    /** يعرض حقل واحد فقط حسب نوع التوصيل، وينظّف الآخر */
    function syncFields() {
        var type = deliveryType.value;
        var isHome = type === 'home';
        var isOffice = type === 'office';

        communeField.hidden = !isHome;
        communeSelect.required = isHome;

        pickupField.hidden = !isOffice;
        pickupSelect.required = isOffice;

        // ⚠️ تنظيف: بلاه، الزبون اللي يبدّل يخلي وراه قيمة من النوع الآخر
        if (!isOffice) {
            pickupSelect.value = '';
            pickupIdInput.value = '';
        }
        if (!isHome) {
            communeSelect.value = '';
            communeIdInput.value = '';
        }

        if (isHome) loadCommunes();
        else if (isOffice) loadPickupPoints();
    }

    // ---------- الأحداث ----------

    [carrier, wilaya].forEach(function (el) {
        el.addEventListener('change', function () {
            syncFields();
            updateShipping();
        });
    });

    deliveryType.addEventListener('change', function () {
        syncFields();
        updateShipping();
    });

    communeSelect.addEventListener('change', syncCommuneId);
    pickupSelect.addEventListener('change', syncPickupId);

    if (qtyInput) qtyInput.addEventListener('input', updateTotals);

    // منع الإرسال المزدوج — النقر مرتين ينشئ طلبيتين
    var ctaLabel = submitBtn.textContent.trim();

    form.addEventListener('submit', function () {
        submitBtn.disabled = true;
        submitBtn.textContent = 'جاري الإرسال...';

        if (typeof fbq === 'function') {
            fbq('track', 'InitiateCheckout', {
                content_name: CFG.productName,
                content_ids: [CFG.productId],
                value: CFG.unitPrice * quantity(),
                currency: 'DZD'
            });
        }

        // نرجّعو الزر إذا السيرفر رد بخطأ ورجّع نفس الصفحة
        setTimeout(function () {
            submitBtn.disabled = false;
            submitBtn.textContent = ctaLabel;
        }, 8000);
    });

    // ---------- العدّاد التنازلي ----------
    //
    // وضعان:
    //   1) data-deadline موجود (الأدمن حدّد تاريخ نهاية العرض): عدّ حقيقي لنفس
    //      اللحظة عند كل الزوار — يوم / ساعة / دقيقة / ثانية.
    //   2) بلا تاريخ: مدة بالساعات تبدا من أول زيارة (sessionStorage).

    var cd = document.getElementById('lpCountdown');
    if (cd) {
        var unit = {};
        ['d', 'h', 'm', 's'].forEach(function (k) {
            unit[k] = cd.querySelector('[data-u="' + k + '"]');
        });
        var fixed = Date.parse(cd.getAttribute('data-deadline') || '');
        var deadline;

        if (!isNaN(fixed)) {
            deadline = fixed;
        } else {
            var hours = parseInt(cd.getAttribute('data-hours'), 10) || 24;
            // ⚠️ نخزّنو وقت البداية باش العدّاد ما يتصفّرش فكل تحديث للصفحة
            var key = 'lp-deadline-' + location.pathname;
            var saved = null;
            try { saved = sessionStorage.getItem(key); } catch (e) { /* وضع خاص */ }
            if (!saved) {
                saved = Date.now() + hours * 3600 * 1000;
                try { sessionStorage.setItem(key, saved); } catch (e) { /* وضع خاص */ }
            }
            deadline = parseInt(saved, 10);
        }

        var pad = function (n) { return String(n).padStart(2, '0'); };
        var setUnit = function (k, v) {
            if (unit[k]) unit[k].querySelector('b').textContent = v;
        };

        var tick = function () {
            var left = Math.max(0, deadline - Date.now());
            var d = Math.floor(left / 86400000);
            var h = Math.floor(left % 86400000 / 3600000);
            var m = Math.floor(left % 3600000 / 60000);
            var s = Math.floor(left % 60000 / 1000);
            if (unit.d) unit.d.hidden = (d === 0);   // نخبّيو «يوم» كي ما بقاش
            setUnit('d', d);
            setUnit('h', pad(h));
            setUnit('m', pad(m));
            setUnit('s', pad(s));
            if (left <= 0) {
                clearInterval(timer);
                // العرض خلص: نعاودو تحميل الصفحة باش يبان «انتهى العرض» من السيرفر
                // ⚠️ مرة وحدة برك: ساعة جهاز الزائر ممكن تكون مقدّمة، وبلا هاد الحماية
                // الصفحة تبقى تعاود التحميل بلا نهاية.
                var rk = 'lp-reloaded-' + location.pathname;
                var done = false;
                try { done = !!sessionStorage.getItem(rk); } catch (e) { done = true; }
                if (!isNaN(fixed) && !done) {
                    try { sessionStorage.setItem(rk, '1'); } catch (e) { /* وضع خاص */ }
                    setTimeout(function () { location.reload(); }, 1500);
                }
            }
        };
        var timer = setInterval(tick, 1000);
        tick();
    }

    // ---------- الزر الثابت ----------

    var sticky = document.getElementById('stickyCta');
    var orderSection = document.getElementById('order');

    if (sticky && orderSection && 'IntersectionObserver' in window) {
        // يختفي كي يوصل الزائر للفورم — ما بقاش عندو فايدة
        new IntersectionObserver(function (entries) {
            sticky.classList.toggle('is-hidden', entries[0].isIntersecting);
        }, { threshold: 0.15 }).observe(orderSection);
    }

    // ---------- البداية ----------

    if (CFG.saved.wilaya) wilaya.value = CFG.saved.wilaya;
    syncFields();
    updateTotals();
    updateShipping();

    // ---------- معرض الصور (سلايد) + مزامنة الموديل ----------
    //
    // ⚠️ نستعملو scrollIntoView بدل scrollLeft: فالصفحات RTL قيمة
    // scrollLeft تتبدل من متصفح لمتصفح (سالبة/موجبة)، وscrollIntoView
    // يخدم بنفس الطريقة فكلهم.
    var track = document.getElementById('lpTrack');
    var slides = track ? Array.prototype.slice.call(track.querySelectorAll('.lp-slide')) : [];
    var dots = Array.prototype.slice.call(document.querySelectorAll('.lp-dot'));
    var variantRadios = Array.prototype.slice.call(
        document.querySelectorAll('input[name="variant_id"][form="lpForm"]'));
    var bundleMainImg = document.getElementById('lpBundleMainImg');
    var syncing = false;   // يمنع الحلقة: نقر موديل ← سلايد ← موديل

    function goTo(index) {
        var slide = slides[index];
        if (!slide) return;
        slide.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
    }

    function setActive(index) {
        dots.forEach(function (d, i) { d.classList.toggle('is-active', i === index); });
    }

    function selectVariant(id) {
        variantRadios.forEach(function (r) {
            if (String(r.value) === String(id) && !r.checked) r.checked = true;
        });
        updateBundleImage();
    }

    function updateBundleImage() {
        if (!bundleMainImg) return;
        var picked = variantRadios.filter(function (r) { return r.checked; })[0];
        if (picked && picked.getAttribute('data-image')) {
            bundleMainImg.src = picked.getAttribute('data-image');
        }
    }

    dots.forEach(function (d) {
        d.addEventListener('click', function () { goTo(parseInt(d.getAttribute('data-go'), 10)); });
    });

    // نقر الموديل ← تتبدل الصورة
    variantRadios.forEach(function (r) {
        r.addEventListener('change', function () {
            updateBundleImage();
            var target = slides.filter(function (sl) {
                return sl.getAttribute('data-variant') === String(r.value);
            })[0];
            if (target) {
                syncing = true;
                goTo(parseInt(target.getAttribute('data-index'), 10));
                setTimeout(function () { syncing = false; }, 600);
            }
        });
    });

    // سحب الصورة ← يتبدل الموديل (ونقاط السلايد)
    if (slides.length && 'IntersectionObserver' in window) {
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (en) {
                if (!en.isIntersecting) return;
                var i = parseInt(en.target.getAttribute('data-index'), 10);
                setActive(i);
                var vid = en.target.getAttribute('data-variant');
                if (vid && !syncing) selectVariant(vid);
            });
        }, { root: track, threshold: 0.6 });
        slides.forEach(function (sl) { io.observe(sl); });
    }

    updateBundleImage();

})();
