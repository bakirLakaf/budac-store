/* Service Worker تاع لوحة تحكم Budac — v190
 *
 * ⚠️ قاعدة ذهبية: ما نخزّنوش صفحات اللوحة (فيها طلبيات وأرقام زبائن).
 * نخزّنو غير: صفحة «ما كاينش اتصال» + الأيقونات.
 * التنقّل: دايمًا من الشبكة؛ إذا طاح الاتصال نوريو صفحة الـoffline.
 */
var CACHE = 'budac-admin-v190';
var OFFLINE_URL = '/admin/offline';
var PRECACHE = [
  OFFLINE_URL,
  '/static/img/brand/icon-192.png',
  '/static/img/brand/icon-512.png'
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE)
      .then(function (c) { return c.addAll(PRECACHE); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; })
        .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;           // POST (فورمات) تمشي طبيعي

  // تنقّل بين الصفحات: شبكة فقط + بديل offline
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).catch(function () { return caches.match(OFFLINE_URL); })
    );
    return;
  }

  // الأيقونات فقط من الكاش
  var url = new URL(req.url);
  if (url.pathname.indexOf('/static/img/brand/') === 0) {
    e.respondWith(
      caches.match(req, { ignoreSearch: true }).then(function (hit) { return hit || fetch(req); })
    );
  }
});
