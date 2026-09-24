// Sleepy-Chat service worker
const CACHE = 'sleepy-chat-v1';
const SHELL = [
  './',
  './index.html',
  './manifest.json',
  './icon.svg'
];

self.addEventListener('install', (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => {})));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // Only handle our own shell. Pass through Supabase, fonts, and CDN.
  if (url.origin !== self.location.origin) return;
  if (e.request.method !== 'GET') return;
  e.respondWith(
    caches.match(e.request).then((cached) => {
      if (cached) {
        // Stale-while-revalidate: return cache, refresh in background
        fetch(e.request).then((res) => {
          if (res && res.status === 200) caches.open(CACHE).then((c) => c.put(e.request, res.clone()));
        }).catch(() => {});
        return cached;
      }
      return fetch(e.request).catch(() => caches.match('./index.html'));
    })
  );
});
