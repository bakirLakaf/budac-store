/* حفظ معلومات الزبون للمرة الجاية (checkout + صفحات الهبوط)
 *
 * ⚠️ كل شي فجهاز الزبون (localStorage) — ما نبعثو والو للسيرفر.
 * ⚠️ مفعّل تلقائيًا أول مرة. إذا الزبون طفّاه نتذكّرو اختيارو (PREF).
 * ⚠️ ما نرجّعوش «التوصيل للمكتب»: يحتاج نقطة استلام تتعمّر من الشبكة، وإذا
 *    رجعناه ناقص الزبون يلقى فورم مكسور. «المنزل» برك.
 * ⚠️ ما نكتبوش فوق حقل فيه قيمة (مثلاً الفورم رجع بعد خطأ تحقق).
 */
(function () {
    'use strict';

    var KEY = 'budac_saved_info_v1';
    var PREF = 'budac_save_info_pref';
    var box = document.querySelector('[data-save-info]');
    if (!box) return;
    var form = box.closest('form');
    if (!form) return;

    var check = box.querySelector('#saveInfoCheck');
    var clearBtn = box.querySelector('.save-info-clear');

    function read(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
    function write(key, v) { try { localStorage.setItem(key, v); } catch (e) { /* وضع خاص */ } }
    function drop(key) { try { localStorage.removeItem(key); } catch (e) { /* وضع خاص */ } }
    function field(name) { return form.querySelector('[name="' + name + '"]'); }
    function fire(el, type) { el.dispatchEvent(new Event(type, { bubbles: true })); }

    // الاختيار المحفوظ: '0' = طفّاه. غير ذلك (أول مرة) = مفعّل
    check.checked = read(PREF) !== '0';
    check.addEventListener('change', function () {
        write(PREF, check.checked ? '1' : '0');
        if (!check.checked) { drop(KEY); clearBtn.hidden = true; }
    });

    var saved = null;
    try { saved = JSON.parse(read(KEY) || 'null'); } catch (e) { saved = null; }

    function setIfEmpty(name, value, evt) {
        var el = field(name);
        if (!el || !value || el.value) return el;
        el.value = value;
        if (el.value !== value) { el.value = ''; return el; }   // خيار ما بقاش موجود (select)
        fire(el, evt || 'input');
        return el;
    }

    function restore() {
        clearBtn.hidden = false;
        setIfEmpty('name', saved.name);
        setIfEmpty('phone', saved.phone);
        setIfEmpty('carrier_code', saved.carrier, 'change');
        setIfEmpty('wilaya_code', saved.wilaya, 'change');
        if (saved.delivery === 'home') setIfEmpty('delivery_type', 'home', 'change');
        setIfEmpty('address_note', saved.address);

        // البلدية تتحمّل من السيرفر بعد اختيار الولاية/الشركة: نستنّاو توصل (حتى 8 ثواني)
        if (!saved.commune) return;
        var tries = 0;
        var timer = setInterval(function () {
            var sel = field('commune');
            tries++;
            if (!sel || tries > 27) { clearInterval(timer); return; }
            if (sel.value) { clearInterval(timer); return; }     // الزبون اختار بيدو
            for (var i = 0; i < sel.options.length; i++) {
                if (sel.options[i].value === saved.commune) {
                    sel.value = saved.commune;
                    fire(sel, 'change');
                    clearInterval(timer);
                    return;
                }
            }
        }, 300);
    }

    if (saved && check.checked) restore();

    clearBtn.addEventListener('click', function () {
        drop(KEY);
        clearBtn.hidden = true;
    });

    // الحفظ عند الإرسال (مرة وحدة — بعد ما الزبون كمّل المعلومات)
    form.addEventListener('submit', function () {
        if (!check.checked) { drop(KEY); return; }
        function val(name) { var el = field(name); return el ? (el.value || '').trim() : ''; }
        if (!val('name') || !val('phone')) return;
        write(KEY, JSON.stringify({
            name: val('name'), phone: val('phone'),
            carrier: val('carrier_code'), wilaya: val('wilaya_code'),
            delivery: val('delivery_type'), commune: val('commune'),
            address: val('address_note')
        }));
    });
})();
