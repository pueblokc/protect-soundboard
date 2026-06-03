// Minimal service worker — enables install-to-home-screen.
// Network-first; we never want stale control actions cached.
self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => self.clients.claim());
self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;            // never cache POST control calls
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
