// Rasad service worker: the app opens offline; incidents are always fetched fresh first.
const CACHE = "firms-v28";
// what anyone may have: the page itself and what a browser needs to install the app
const SHELL = ["./", "index.html", "manifest.webmanifest", "brand-mark.svg", "icons/icon-192.png", "icons/icon-512.png"];
// Files that may not answer when the worker is installed: access.js exists only on the private app,
// and there the «قبل وبعد» scripts answer 401 until somebody is signed in. Each is tried on its own,
// so one that is missing never stops the install, and each is kept the first time it does answer.
// The scripts carry the same ?v= as in index.html, so a new version is never mixed with an old page.
const LATER = ["access.js?v=28", "imagery.js?v=28", "beforeafter.js?v=28"];
const KEEP = new Set([...SHELL, ...LATER].map(file => new URL(file, self.location).href));

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE)
    .then(c => c.addAll(SHELL).then(() => Promise.all(LATER.map(file => c.add(file).catch(() => null)))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function networkFirst(request) {
  const url = new URL(request.url);
  const key = url.origin + url.pathname;
  return fetch(request).then(res => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(key, copy)); }
    return res;
  }).catch(() => caches.match(key));
}
// the app's own files: from the store when they are there, otherwise from the network, kept when the answer is good
function cacheFirst(request) {
  return caches.match(request).then(hit => hit || fetch(request).then(res => {
    if (res.ok && KEEP.has(request.url)) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(request, copy)); }
    return res;
  }));
}

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.includes("/api/") || url.pathname.includes("/clips/")) return;   // private: always from the network, never kept
  if (e.request.mode === "navigate" || url.pathname.includes("/data/")) {
    e.respondWith(networkFirst(e.request));
    return;
  }
  e.respondWith(cacheFirst(e.request));
});
