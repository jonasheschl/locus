const CACHE_NAME = 'locus-shell-v2';
const SHELL_FILES = [
  '/manifest.webmanifest',
  '/icons/locus-32.png',
  '/icons/locus-48.png',
  '/icons/locus-128.png',
  '/icons/locus-192.png',
  '/icons/locus-256.png',
  '/icons/locus-512.png',
  '/icons/locus-maskable-192.png',
  '/icons/locus-maskable-512.png',
  '/icons/locus.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    const shellResponse = await fetch('/');
    const shellMarkup = await shellResponse.clone().text();
    const builtAssets = [...shellMarkup.matchAll(/(?:src|href)="(\/assets\/[^\"]+)"/g)]
      .map((match) => match[1]);

    await cache.put('/', shellResponse);
    await cache.addAll([...SHELL_FILES, ...builtAssets]);
  })());
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key.startsWith('locus-') && key !== CACHE_NAME)
          .map((key) => caches.delete(key)),
      ))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  if (
    request.method !== 'GET'
    || url.origin !== self.location.origin
    || url.pathname.startsWith('/api/')
  ) return;

  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        const response = await fetch(request);
        if (response.ok) {
          const cache = await caches.open(CACHE_NAME);
          await cache.put('/', response.clone());
        }
        return response;
      } catch {
        return caches.match('/');
      }
    })());
    return;
  }

  if (['script', 'style', 'font', 'image'].includes(request.destination)) {
    event.respondWith((async () => {
      const cached = await caches.match(request);
      if (cached) return cached;

      const response = await fetch(request);
      if (response.ok) {
        const cache = await caches.open(CACHE_NAME);
        await cache.put(request, response.clone());
      }
      return response;
    })());
  }
});
