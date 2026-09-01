// Service worker: cachea el shell y sirve los datos con "network first,
// cae a cache" para que la app abra offline con lo ultimo descargado.
// Sube estos numeros para forzar refresco en todos los dispositivos.
const SHELL = 'shell-v2';
const DATOS = 'datos-v2';
const ARCHIVOS = [
  './', './index.html', './app.js', './styles.css',
  './manifest.webmanifest', './icon-192.png', './icon-512.png', './icon-180.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ARCHIVOS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((ks) => Promise.all(
      ks.filter((k) => k !== SHELL && k !== DATOS).map((k) => caches.delete(k)),
    )).then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;

  if (url.pathname.includes('/data/')) {
    // datos: siempre de la red; solo si falla (offline) se usa la copia.
    e.respondWith(
      fetch(e.request, { cache: 'no-store' }).then((r) => {
        const copia = r.clone();
        caches.open(DATOS).then((c) => c.put(e.request, copia));
        return r;
      }).catch(() => caches.match(e.request)),
    );
    return;
  }
  // shell: cache primero, pero refresca en segundo plano.
  e.respondWith(
    caches.match(e.request).then((r) => {
      const red = fetch(e.request).then((resp) => {
        caches.open(SHELL).then((c) => c.put(e.request, resp.clone()));
        return resp;
      }).catch(() => r);
      return r || red;
    }),
  );
});
