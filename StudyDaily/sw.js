/*
 * StudyDaily - Service Worker
 * 只缓存应用外壳；视频文件（可能几百 GB 量级）绝不进缓存。
 * 网络优先，离线时回退到缓存；本地 HTTP 服务仍在时一切照常。
 */
var CACHE = 'studydaily-shell-v7';
var SHELL = [
  './', './index.html', './styles.css', './planner.js', './app.js',
  './manifest.webmanifest', './quiz.json', './workbooks.json',
  './icons/icon-192.png', './icons/icon-512.png',
  './assets/img/bear.png', './assets/img/trophy.png'
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (cache) {
      // 允许个别资源缺失，不影响整体安装
      return Promise.allSettled(SHELL.map(function (u) { return cache.add(u); }));
    }).then(function () { return self.skipWaiting(); })
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
  if (req.method !== 'GET') return;
  var url;
  try { url = new URL(req.url); } catch (err) { return; }
  if (url.origin !== self.location.origin) return;

  // 视频（需 Range / 206）、语音、PDF 页图、练习册页图：直连网络，不进缓存
  if (/\.(mp4|m4v|ts|mkv|webm|mp3|m4a|ogg|wav)$/i.test(url.pathname)
      || url.pathname.indexOf('/pages/') >= 0
      || url.pathname.indexOf('/voice/') >= 0
      || url.pathname.indexOf('/workbooks/') >= 0) return;

  e.respondWith(
    fetch(req).then(function (res) {
      if (res && res.ok && res.type === 'basic') {
        var copy = res.clone();
        caches.open(CACHE).then(function (c) { c.put(req, copy); }).catch(function () {});
      }
      return res;
    }).catch(function () {
      return caches.match(req).then(function (r) {
        return r || new Response('离线且未缓存', { status: 503 });
      });
    })
  );
});
