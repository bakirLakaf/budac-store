/**
 * محرّر الصور — قص وتدوير قبل الرفع (لوحة التحكم فقط).
 *
 * يستعمل Cropper.js 1.6.2 (MIT) من static/vendor/cropperjs — نسخة محلية بلا CDN،
 * و`script-src 'self'` يسمح بيها. المكتبة تتحمّل غير كي تحتاجها (أول مرة تختار صورة).
 *
 * الاستعمال:
 *   BudacImageEditor.edit(files, {aspect: 1}).then(function (files) { ... })
 *     • aspect: نسبة العرض/الطول (1 = مربّع، NaN = حر)
 *     • يرجع Promise بقائمة الملفات النهائية (المعدّلة + اللي خلّيناها كيما هي)
 *       — القائمة الفارغة = المستخدم ألغى.
 *   ولحقول <input type="file" data-image-editor="1"> العادية: يخدم وحدو
 *   (قيمة data-image-editor: «1» مربّع، «free» حر، «16/9» أي نسبة).
 *
 * ⚠️ كيفاش نتعاملو مع صور الهاتف؟
 *   createImageBitmap(file, {imageOrientation: 'from-image'}) يطبّق اتجاه EXIF
 *   (صورة الهاتف العمودية ما تبانش مقلوبة)، ونصغّروها لـ2400 بكسل قبل العرض.
 *   وبهاد الطريقة Cropper ما يحتاجش XHR على رابط blob (يمنعو connect-src).
 *   يحتاج `blob:` فـimg-src (مضاف فـapp.py).
 */
(function () {
    "use strict";

    var script = document.currentScript;
    var VENDOR = script && script.src
        ? new URL("../vendor/cropperjs/", script.src).href
        : "/static/vendor/cropperjs/";

    var MAX_SIDE = 2400;       // أكبر ضلع للصورة فالمحرّر ولما تخرج
    var JPEG_QUALITY = 0.92;
    var loading = null;

    // ───────────────────────── تحميل Cropper عند الحاجة ─────────────────────────
    function loadCropper() {
        if (window.Cropper) { return Promise.resolve(); }
        if (loading) { return loading; }
        loading = new Promise(function (resolve, reject) {
            var css = document.createElement("link");
            css.rel = "stylesheet";
            css.href = VENDOR + "cropper.min.css";
            document.head.appendChild(css);

            var js = document.createElement("script");
            js.src = VENDOR + "cropper.min.js";
            js.onload = function () { resolve(); };
            js.onerror = function () {
                loading = null;
                reject(new Error("تعذّر تحميل أداة القص"));
            };
            document.head.appendChild(js);
        });
        return loading;
    }

    // ───────────────────────── مساعدات ─────────────────────────
    function baseName(name) {
        return String(name || "image").replace(/\.[^.]+$/, "") || "image";
    }

    function parseAspect(value) {
        if (!value || value === "1") { return 1; }
        if (value === "free") { return NaN; }
        var m = /^(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)$/.exec(value);
        if (m && Number(m[2]) > 0) { return Number(m[1]) / Number(m[2]); }
        var n = Number(value);
        return n > 0 ? n : 1;
    }

    /** يفتح الصورة باتجاهها الصحيح ويصغّرها → رابط blob لصورة JPEG */
    function prepare(file) {
        return new Promise(function (resolve, reject) {
            if (!window.createImageBitmap) { reject(new Error("المتصفح قديم")); return; }

            var opening;
            try {
                opening = window.createImageBitmap(file, { imageOrientation: "from-image" });
            } catch (e) {
                opening = window.createImageBitmap(file);
            }

            opening.then(function (bitmap) {
                var w = bitmap.width, h = bitmap.height;
                var k = Math.min(1, MAX_SIDE / Math.max(w, h));
                var canvas = document.createElement("canvas");
                canvas.width = Math.max(1, Math.round(w * k));
                canvas.height = Math.max(1, Math.round(h * k));
                var ctx = canvas.getContext("2d");
                ctx.fillStyle = "#fff";                  // PNG شفاف ← خلفية بيضاء
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
                if (bitmap.close) { bitmap.close(); }

                canvas.toBlob(function (blob) {
                    if (!blob) { reject(new Error("فشل تجهيز الصورة")); return; }
                    resolve(URL.createObjectURL(blob));
                }, "image/jpeg", 0.95);
            }).catch(reject);
        });
    }

    // ───────────────────────── النافذة (تتبني مرة وحدة) ─────────────────────────
    var ui = null;

    function buildUI() {
        if (ui) { return ui; }

        var overlay = document.createElement("div");
        overlay.className = "ie-overlay";
        overlay.hidden = true;
        overlay.setAttribute("role", "dialog");
        overlay.setAttribute("aria-modal", "true");
        overlay.innerHTML =
            '<div class="ie-box" dir="rtl">' +
              '<div class="ie-head"><strong class="ie-title"></strong> <span class="ie-name"></span></div>' +
              '<div class="ie-stage"><img class="ie-img" alt=""></div>' +
              '<div class="ie-tools">' +
                '<div class="ie-group">' +
                  '<button type="button" data-act="rot-left">↺ دوّر لليسار</button>' +
                  '<button type="button" data-act="rot-right">↻ دوّر لليمين</button>' +
                  '<button type="button" data-act="flip-h">⇋ اقلبها أفقيًا</button>' +
                  '<button type="button" data-act="flip-v">⇅ اقلبها عموديًا</button>' +
                  '<button type="button" data-act="reset">رجّع كيما كانت</button>' +
                '</div>' +
                '<div class="ie-group ie-ratios">' +
                  '<span class="ie-label">الشكل:</span>' +
                  '<button type="button" data-ratio="1">1:1</button>' +
                  '<button type="button" data-ratio="1.3333333">4:3</button>' +
                  '<button type="button" data-ratio="0.75">3:4</button>' +
                  '<button type="button" data-ratio="1.7777778">16:9</button>' +
                  '<button type="button" data-ratio="free">حر</button>' +
                '</div>' +
              '</div>' +
              '<div class="ie-foot">' +
                '<button type="button" class="ie-ok" data-act="ok">✓ اقصّ وكمّل</button>' +
                '<button type="button" data-act="asis">خلّيها كيما هي</button>' +
                '<button type="button" data-act="asis-all" hidden>خلّي الباقي كيما هو</button>' +
                '<button type="button" data-act="skip">ما نرفعهاش</button>' +
                '<button type="button" class="ie-cancel" data-act="cancel">إلغاء الكل</button>' +
              '</div>' +
            '</div>';
        document.body.appendChild(overlay);

        ui = {
            overlay: overlay,
            title: overlay.querySelector(".ie-title"),
            name: overlay.querySelector(".ie-name"),
            img: overlay.querySelector(".ie-img"),
            asisAll: overlay.querySelector('[data-act="asis-all"]'),
            ratios: overlay.querySelectorAll("[data-ratio]")
        };
        return ui;
    }

    // ───────────────────────── تعديل صورة وحدة ─────────────────────────
    /** يرجع {action: ok|asis|asis-all|skip|cancel, file?} */
    function editOne(file, index, total, aspect) {
        var u = buildUI();

        return prepare(file).then(function (url) {
            return new Promise(function (resolve) {
                var cropper = null;
                var finished = false;

                u.title.textContent = "قصّ وتدوير — صورة " + (index + 1) + " من " + total;
                u.name.textContent = file.name || "";
                u.asisAll.hidden = !(total > 1 && index < total - 1);
                u.overlay.hidden = false;
                document.body.classList.add("ie-open");

                function setActive(value) {
                    Array.prototype.forEach.call(u.ratios, function (b) {
                        b.classList.toggle("is-active", b.dataset.ratio === value);
                    });
                }

                function activeKey(a) {
                    if (isNaN(a)) { return "free"; }
                    var found = null;
                    Array.prototype.forEach.call(u.ratios, function (b) {
                        if (b.dataset.ratio !== "free" && Math.abs(Number(b.dataset.ratio) - a) < 0.001) {
                            found = b.dataset.ratio;
                        }
                    });
                    return found || "free";
                }

                function finish(result) {
                    if (finished) { return; }
                    finished = true;
                    document.removeEventListener("keydown", onKey);
                    u.overlay.removeEventListener("click", onClick);
                    if (cropper) { cropper.destroy(); cropper = null; }
                    u.img.removeAttribute("src");
                    URL.revokeObjectURL(url);
                    u.overlay.hidden = true;
                    document.body.classList.remove("ie-open");
                    resolve(result);
                }

                function exportEdited() {
                    var canvas = cropper && cropper.getCroppedCanvas({
                        maxWidth: MAX_SIDE, maxHeight: MAX_SIDE,
                        fillColor: "#fff", imageSmoothingQuality: "high"
                    });
                    if (!canvas) { finish({ action: "asis", file: file }); return; }
                    canvas.toBlob(function (blob) {
                        if (!blob) { finish({ action: "asis", file: file }); return; }
                        var edited = new File([blob], baseName(file.name) + ".jpg", { type: "image/jpeg" });
                        finish({ action: "ok", file: edited });
                    }, "image/jpeg", JPEG_QUALITY);
                }

                function onClick(e) {
                    var btn = e.target.closest("button");
                    if (!btn || !u.overlay.contains(btn)) { return; }

                    if (btn.dataset.ratio) {
                        var a = btn.dataset.ratio === "free" ? NaN : Number(btn.dataset.ratio);
                        if (cropper) { cropper.setAspectRatio(a); }
                        setActive(btn.dataset.ratio);
                        return;
                    }
                    switch (btn.dataset.act) {
                        case "rot-left":  if (cropper) { cropper.rotate(-90); } break;
                        case "rot-right": if (cropper) { cropper.rotate(90); } break;
                        case "flip-h":    if (cropper) { cropper.scaleX(cropper.getData().scaleX === -1 ? 1 : -1); } break;
                        case "flip-v":    if (cropper) { cropper.scaleY(cropper.getData().scaleY === -1 ? 1 : -1); } break;
                        case "reset":     if (cropper) { cropper.reset(); } break;
                        case "ok":        exportEdited(); break;
                        case "asis":      finish({ action: "asis", file: file }); break;
                        case "asis-all":  finish({ action: "asis-all", file: file }); break;
                        case "skip":      finish({ action: "skip" }); break;
                        case "cancel":    finish({ action: "cancel" }); break;
                    }
                }

                function onKey(e) {
                    if (e.key === "Escape") { finish({ action: "cancel" }); }
                }

                u.overlay.addEventListener("click", onClick);
                document.addEventListener("keydown", onKey);

                u.img.onload = function () {
                    if (finished) { return; }
                    cropper = new window.Cropper(u.img, {
                        viewMode: 1,
                        aspectRatio: aspect,
                        autoCropArea: 1,
                        background: false,
                        checkOrientation: false,   // اتجاه EXIF مطبّق من قبل (prepare)
                        responsive: true,
                        guides: true
                    });
                    setActive(activeKey(aspect));
                };
                u.img.src = url;
            });
        });
    }

    // ───────────────────────── الواجهة العامة ─────────────────────────
    /**
     * يمرّ على الملفات وحدة وحدة. أي خطأ فتحضير صورة (مثلاً GIF، أو متصفح قديم)
     * ما يوقّف الرفع: الصورة تتخطّى المحرّر وتتبعث كيما هي.
     */
    function edit(files, options) {
        options = options || {};
        var aspect = options.aspect === undefined ? 1 : options.aspect;
        var list = Array.prototype.slice.call(files || []);
        if (!list.length) { return Promise.resolve([]); }

        return loadCropper().then(function () {
            var out = [];
            var passthrough = false;

            function step(i) {
                if (i >= list.length) { return Promise.resolve(out); }
                var file = list[i];

                // GIF أو ما هيش صورة، ولا المستخدم قال «خلّي الباقي كيما هو»
                if (passthrough || !/^image\/(jpe?g|png|webp|bmp)$/i.test(file.type || "")) {
                    out.push(file);
                    return step(i + 1);
                }

                return editOne(file, i, list.length, aspect).then(function (res) {
                    if (res.action === "cancel") { return []; }
                    if (res.action === "asis-all") { passthrough = true; }
                    if (res.file) { out.push(res.file); }
                    return step(i + 1);
                }, function () {
                    out.push(file);               // فشل التحضير ← كيما هي
                    return step(i + 1);
                });
            }
            return step(0);
        }, function () {
            return list;                           // Cropper ما تحمّلش ← نكمّلو بلا قص
        });
    }

    // ───────────────────────── تعديل صورة موجودة (من رابط) ─────────────────────────
    /**
     * يجيب صورة مرفوعة من قبل (نفس الموقع) ويفتحها فالمحرّر.
     * يرجع Promise بـFile جديد إذا الأدمن عدّل وأكّد، وإلا null
     * (ألغى، ولا ضغط «خلّيها كيما هي»، ولا فشل الجلب).
     * الشكل الافتراضي «حر» باش التدوير والقلب ما يقصّوش شي.
     */
    function editFromUrl(url, options) {
        options = options || {};
        return fetch(url, { credentials: "same-origin" }).then(function (res) {
            if (!res.ok) { throw new Error("HTTP " + res.status); }
            return res.blob();
        }).then(function (blob) {
            var name = decodeURIComponent(String(url).split("?")[0].split("/").pop() || "image.webp");
            var original = new File([blob], name, { type: blob.type || "image/webp" });
            return edit([original], {
                aspect: options.aspect === undefined ? NaN : options.aspect
            }).then(function (out) {
                return out.length && out[0] !== original ? out[0] : null;
            });
        }).catch(function () { return null; });
    }

    // ───────────────────────── حقول <input type=file> العادية ─────────────────────────
    function wire(input) {
        input.addEventListener("change", function (e) {
            if (input.dataset.ieDone === "1") { input.dataset.ieDone = ""; return; }
            var files = Array.prototype.slice.call(input.files || []);
            if (!files.length) { return; }

            e.stopImmediatePropagation();
            edit(files, { aspect: parseAspect(input.dataset.imageEditor) }).then(function (out) {
                if (!out.length) { input.value = ""; return; }
                setFiles(input, out);
            });
        }, true);

        // «✂️ عدّل الصورة الحالية»: نفتحو الصورة المحفوظة فالمحرّر، والنتيجة تتحط
        // فنفس الحقل — الحفظ العادي للنموذج يرفعها ويعوّض القديمة (بلا سيرفر زايد).
        var current = input.dataset.currentImage;
        if (current) {
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "btn-admin btn-sm btn-ghost ie-current";
            btn.textContent = "✂️ عدّل الصورة الحالية (قص / تدوير / قلب)";
            btn.addEventListener("click", function () {
                btn.disabled = true;
                editFromUrl(current, { aspect: parseAspect(input.dataset.imageEditor) }).then(function (file) {
                    btn.disabled = false;
                    if (file) {
                        setFiles(input, [file]);
                        btn.textContent = "✓ تعدّلت — احفظ باش تتطبّق";
                    }
                });
            });
            input.insertAdjacentElement("afterend", btn);
        }
    }

    function setFiles(input, files) {
        var dt = new DataTransfer();
        files.forEach(function (f) { dt.items.add(f); });
        input.files = dt.files;
    }

    window.BudacImageEditor = {
        edit: edit, editFromUrl: editFromUrl, parseAspect: parseAspect, baseName: baseName
    };

    document.addEventListener("DOMContentLoaded", function () {
        Array.prototype.forEach.call(
            document.querySelectorAll('input[type="file"][data-image-editor]'), wire);
    });
})();
