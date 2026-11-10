// Rasad service worker: the app opens offline; incidents are always fetched fresh first.
const CACHE = "firms-v12";
// the two scripts carry the same ?v= as in index.html, so a new version is never mixed with an old page
const SHELL = ["./", "index.html", "imagery.js?v=12", "beforeafter.js?v=12", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
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

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.includes("/api/") || url.pathname.includes("/clips/")) return;   // private: always from the network
  if (e.request.mode === "navigate" || url.pathname.includes("/data/")) {
    e.respondWith(networkFirst(e.request));
    return;
  }
  e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request)));
});
