// اختبار سلوك محرّر الصور (قص/تدوير). مستقل عن pytest — يحتاج Node:
//   cd tests/js && npm i jsdom@24 && node test_image_editor.js
// المسار تحت نسبي لمجلد المشروع.
const { JSDOM } = require("jsdom");
const fs = require("fs");
const src = fs.readFileSync("" + require("path").join(__dirname, "..", "..", "static", "js", "image-editor.js") + "", "utf8");

function makeWindow() {
  const { VirtualConsole } = require("jsdom"); const dom = new JSDOM('<!doctype html><body><input type="file" id="f" data-image-editor="16/9"></body>',
    { runScripts: "outside-only", url: "http://localhost/admin/", pretendToBeVisual: true, virtualConsole: new VirtualConsole().sendTo(console) });
  const w = dom.window;
  // ── تقليد المتصفح: createImageBitmap / canvas / blob URLs / Cropper ──
  w.createImageBitmap = async () => ({ width: 4000, height: 3000, close() {} });
  w.HTMLCanvasElement.prototype.getContext = () => ({ fillRect() {}, drawImage() {}, set fillStyle(v) {} });
  w.HTMLCanvasElement.prototype.toBlob = function (cb) { cb(new w.Blob(["x"], { type: "image/jpeg" })); };
  let n = 0; w.URL.createObjectURL = () => "blob:fake/" + (++n); w.URL.revokeObjectURL = () => {};
  w.__calls = [];
  w.Cropper = class {
    constructor(img, opts) { w.__calls.push(["init", opts.aspectRatio, opts.checkOrientation, opts.viewMode]); }
    constructor_state() {}
    rotate(d) { w.__calls.push(["rotate", d]); }
    scaleX(v) { this._sx = v; w.__calls.push(["scaleX", v]); }
    scaleY(v) { this._sy = v; w.__calls.push(["scaleY", v]); }
    getData() { return { scaleX: this._sx || 1, scaleY: this._sy || 1 }; }
    reset() { w.__calls.push(["reset"]); }
    setAspectRatio(a) { w.__calls.push(["ratio", a]); }
    getCroppedCanvas() { return w.document.createElement("canvas"); }
    destroy() { w.__calls.push(["destroy"]); }
  };
  // الصورة داخل jsdom ما تتحمّلش: نطلقو onload يدويًا كي تتبدّل src
  Object.defineProperty(w.HTMLImageElement.prototype, "src", {
    set(v) { this.setAttribute("src", v); setTimeout(() => this.onload && this.onload(), 0); },
    get() { return this.getAttribute("src") || ""; }
  });
  w.eval(src);
  return w;
}
const tick = () => new Promise(r => setTimeout(r, 80));
const click = (w, sel) => w.document.querySelector(sel).click();
const file = (w, name, type = "image/jpeg") => new w.File(["abc"], name, { type });
let failed = 0;
const ok = (c, m) => { if (!c) { failed++; console.log("FAIL:", m); } else console.log("ok  :", m); };

(async () => {
  // 1) تأكيد قص + تدوير: يرجع ملف .jpg جديد
  let w = makeWindow();
  let p = w.BudacImageEditor.edit([file(w, "IMG_001.JPG")], { aspect: 1 });
  await tick();
  ok(!w.document.querySelector(".ie-overlay").hidden, "النافذة تظهر");
  ok(w.__calls[0][0] === "init" && w.__calls[0][1] === 1 && w.__calls[0][2] === false, "Cropper يبدا بنسبة 1:1 وبلا checkOrientation");
  click(w, '[data-act="rot-right"]'); click(w, '[data-act="rot-left"]'); click(w, '[data-ratio="free"]');
  ok(JSON.stringify(w.__calls.slice(1, 4)) === JSON.stringify([["rotate", 90], ["rotate", -90], ["ratio", NaN]]), "تدوير يمين/يسار وتغيير النسبة");
  click(w, '[data-act="ok"]');
  let out = await p;
  ok(out.length === 1 && out[0].name === "IMG_001.jpg" && out[0].type === "image/jpeg", "الناتج File باسم .jpg");
  ok(w.document.querySelector(".ie-overlay").hidden, "النافذة تتقفل");

  // 2) «خلّيها كيما هي» ← الملف الأصلي نفسه
  w = makeWindow(); const f2 = file(w, "a.png", "image/png");
  p = w.BudacImageEditor.edit([f2]); await tick(); click(w, '[data-act="asis"]');
  out = await p; ok(out.length === 1 && out[0] === f2, "كيما هي ← نفس الملف");

  // 3) ثلاث صور: تخطّي الأولى، قص الثانية، «خلّي الباقي» للثالثة
  w = makeWindow(); const fs3 = [file(w, "1.jpg"), file(w, "2.jpg"), file(w, "3.jpg")];
  p = w.BudacImageEditor.edit(fs3); await tick();
  ok(!w.document.querySelector('[data-act="asis-all"]').hidden, "زر «خلّي الباقي» يبان فصور متعدّدة");
  click(w, '[data-act="skip"]'); await tick();
  click(w, '[data-act="ok"]'); await tick();
  ok(w.document.querySelector('[data-act="asis-all"]').hidden === true, "فآخر صورة ما يبانش «خلّي الباقي»");
  click(w, '[data-act="asis"]');
  out = await p; ok(out.length === 2 && out[0].name === "2.jpg" && out[1] === fs3[2], "تخطّي + قص + كيما هي = 2 ملفات");

  // 4) «خلّي الباقي كيما هو» يعدّي الصور الباقية بلا نافذة
  w = makeWindow(); const fs4 = [file(w, "1.jpg"), file(w, "2.jpg"), file(w, "3.jpg")];
  p = w.BudacImageEditor.edit(fs4); await tick(); click(w, '[data-act="asis-all"]');
  out = await p; ok(out.length === 3 && out.every((f, i) => f === fs4[i]), "الباقي كيما هو بلا نوافذ زايدة");

  // 5) إلغاء الكل (زر + Escape) ← قائمة فارغة
  w = makeWindow(); p = w.BudacImageEditor.edit([file(w, "1.jpg"), file(w, "2.jpg")]); await tick(); click(w, '[data-act="cancel"]');
  out = await p; ok(out.length === 0, "إلغاء الكل ← فارغ");
  w = makeWindow(); p = w.BudacImageEditor.edit([file(w, "1.jpg")]); await tick();
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" }));
  out = await p; ok(out.length === 0, "Escape ← إلغاء");

  // 6) GIF ما يتعدّلش (يتعدّى كيما هو)
  w = makeWindow(); const g = file(w, "a.gif", "image/gif");
  out = await w.BudacImageEditor.edit([g]); ok(out.length === 1 && out[0] === g && w.document.querySelector(".ie-overlay") === null, "GIF يتعدّى بلا محرّر");

  // 7) فشل تحضير الصورة ← نكمّلو بالأصل (ما نضيّعوهاش)
  w = makeWindow(); w.createImageBitmap = async () => { throw new Error("bad"); };
  const f7 = file(w, "x.jpg"); out = await w.BudacImageEditor.edit([f7]);
  ok(out.length === 1 && out[0] === f7, "فشل التحضير ← الأصل يتبعث");

  // 8) Cropper ما تحمّلش ← الأصل يتبعث
  w = makeWindow(); delete w.Cropper;
  w.document.head.appendChild = (el) => { setTimeout(() => el.onerror && el.onerror(), 0); return el; };
  const f8 = file(w, "y.jpg"); out = await w.BudacImageEditor.edit([f8]);
  ok(out.length === 1 && out[0] === f8, "المكتبة ما تحمّلتش ← نرفعو بلا قص");

  // 9) تحليل النسب
  w = makeWindow(); const pa = w.BudacImageEditor.parseAspect;
  ok(pa("1") === 1 && isNaN(pa("free")) && Math.abs(pa("16/9") - 16 / 9) < 1e-9 && pa(undefined) === 1 && pa("abc") === 1, "parseAspect");

  // 10) حقل data-image-editor: يبدّل input.files بالنتيجة (16:9)
  w = makeWindow();
  w.DataTransfer = class { constructor() { this.items = { add: (f) => (this._f = this._f || []).push(f) }; } get files() { return this._f; } };
  w.document.dispatchEvent(new w.Event("DOMContentLoaded"));
  const input = w.document.getElementById("f");
  const f10 = file(w, "banner.jpg");
  Object.defineProperty(input, "files", { configurable: true, writable: true, value: [f10] });
  input.dispatchEvent(new w.Event("change", { bubbles: true })); await tick();
  ok(w.__calls[0] && Math.abs(w.__calls[0][1] - 16 / 9) < 1e-9, "حقل البانر يفتح المحرّر بنسبة 16:9");
  click(w, '[data-act="ok"]'); await tick(); await tick();
  ok(input.files.length === 1 && input.files[0].name === "banner.jpg", "input.files تتبدّل بالصورة المقصوصة");


  // 11) القلب: أفقي وعمودي، ثاني ضغطة ترجّعو
  w = makeWindow(); p = w.BudacImageEditor.edit([file(w, "f.jpg")]); await tick();
  click(w, '[data-act="flip-h"]'); click(w, '[data-act="flip-h"]'); click(w, '[data-act="flip-v"]');
  const flips = w.__calls.filter(c => c[0] === "scaleX" || c[0] === "scaleY");
  ok(JSON.stringify(flips) === JSON.stringify([["scaleX", -1], ["scaleX", 1], ["scaleY", -1]]), "قلب أفقي (مرتين) وعمودي");
  click(w, '[data-act="cancel"]'); await p;

  // 12) editFromUrl: يجيب الصورة ← محرّر ← File جديد
  w = makeWindow();
  w.fetch = async (u) => ({ ok: true, blob: async () => new w.Blob(["img"], { type: "image/webp" }) });
  p = w.BudacImageEditor.editFromUrl("/static/img/products/p-abc123.webp?v=1"); await tick(); await tick();
  ok(Number.isNaN(w.__calls[0][1]), "الصورة الموجودة تتفتح بشكل «حر»");
  click(w, '[data-act="rot-right"]'); click(w, '[data-act="ok"]');
  out = await p; ok(out && out.name === "p-abc123.jpg" && out.type === "image/jpeg", "editFromUrl يرجّع File معدّل");

  // 13) «خلّيها كيما هي» / إلغاء / فشل الجلب ← null (ما نبدّلو والو)
  w = makeWindow(); w.fetch = async () => ({ ok: true, blob: async () => new w.Blob(["i"], { type: "image/png" }) });
  p = w.BudacImageEditor.editFromUrl("/a.png"); await tick(); await tick(); click(w, '[data-act="asis"]');
  ok((await p) === null, "كيما هي ← null");
  w = makeWindow(); w.fetch = async () => ({ ok: true, blob: async () => new w.Blob(["i"], { type: "image/png" }) });
  p = w.BudacImageEditor.editFromUrl("/a.png"); await tick(); await tick(); click(w, '[data-act="cancel"]');
  ok((await p) === null, "إلغاء ← null");
  w = makeWindow(); w.fetch = async () => ({ ok: false, status: 404 });
  ok((await w.BudacImageEditor.editFromUrl("/missing.png")) === null, "فشل الجلب ← null");

  // 14) data-current-image: زر «عدّل الصورة الحالية» يحط النتيجة فنفس الحقل
  w = makeWindow();
  w.DataTransfer = class { constructor() { this.items = { add: (f) => (this._f = this._f || []).push(f) }; } get files() { return this._f; } };
  w.fetch = async () => ({ ok: true, blob: async () => new w.Blob(["i"], { type: "image/webp" }) });
  const inp = w.document.getElementById("f"); inp.dataset.currentImage = "/static/img/offers/b.webp";
  Object.defineProperty(inp, "files", { configurable: true, writable: true, value: [] });   // jsdom: FileList حقيقي ما يقبلش المحاكاة
  w.document.dispatchEvent(new w.Event("DOMContentLoaded"));
  const cbtn = w.document.querySelector(".ie-current");
  ok(!!cbtn && cbtn.textContent.includes("عدّل الصورة الحالية"), "زر الصورة الحالية يتزاد جنب الحقل");
  cbtn.click(); await tick(); await tick(); click(w, '[data-act="ok"]'); await tick(); await tick();
  ok(inp.files && inp.files.length === 1 && inp.files[0].name === "b.jpg" && cbtn.textContent.includes("احفظ"), "النتيجة تتحط فالحقل + رسالة «احفظ»");

  console.log(failed ? `\n${failed} FAILED` : "\nALL PASSED"); process.exit(failed ? 1 : 0);
})();
