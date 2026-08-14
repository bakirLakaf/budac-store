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

    var countdownEl = document.getElementById('countdown');
    if (countdownEl) {
        var box = countdownEl.closest('.lp-countdown');
        var hours = parseInt(box.getAttribute('data-hours'), 10) || 24;

        // ⚠️ نخزّنو وقت البداية فـsessionStorage باش العدّاد ما يتصفّرش
        // فكل تحديث للصفحة — وإلا الزائر يشوفو يعاود من الأول ويفقد المصداقية.
        var key = 'lp-deadline-' + location.pathname;
        var deadline = sessionStorage.getItem(key);

        if (!deadline) {
            deadline = Date.now() + hours * 3600 * 1000;
            try { sessionStorage.setItem(key, deadline); } catch (e) { /* وضع خاص */ }
        }
        deadline = parseInt(deadline, 10);

        setInterval(function tick() {
            var left = deadline - Date.now();
            if (left <= 0) {
                countdownEl.textContent = '00:00:00';
                return;
            }
            var h = Math.floor(left / 3600000);
            var m = Math.floor(left % 3600000 / 60000);
            var s = Math.floor(left % 60000 / 1000);
            countdownEl.textContent =
                String(h).padStart(2, '0') + ':' +
                String(m).padStart(2, '0') + ':' +
                String(s).padStart(2, '0');
        }, 1000);
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
})();
