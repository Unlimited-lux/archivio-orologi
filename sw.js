// Service worker di Archivio Orologi (generato dalla build): tiene in memoria tutti i file dell'app, così funziona
// offline. Non tocca mai i dati dell'archivio (che stanno in IndexedDB) e non contatta nessun server: risponde dalla cache
// e, solo per i file dell'app, prova la rete se un file manca (aggiornamenti).
const CACHE = 'archivio-orologi-20261020215237271';
const FILE = ["./","./apple-touch-icon.png","./assets/index-CUnWK4ZY.css","./assets/index-D4eTEhQ0.js","./assets/sqlite3-DBpDb1lf.wasm","./assets/sqlite3-opfs-async-proxy-C_otN2ZJ.js","./assets/sqlite3-worker1-bundler-friendly-Cp4kN0G-.js","./icona-192.png","./icona-512.png","./index.html","./manifest.webmanifest"];

self.addEventListener('install', (evento) => {
  evento.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILE)));
});

self.addEventListener('activate', (evento) => {
  evento.waitUntil(
    caches.keys().then((chiavi) => Promise.all(chiavi.filter((k) => k.startsWith('archivio-orologi-') && k !== CACHE).map((k) => caches.delete(k)))),
  );
});

self.addEventListener('message', (evento) => {
  if (evento.data === 'aggiorna-ora') self.skipWaiting();
});

self.addEventListener('fetch', (evento) => {
  const richiesta = evento.request;
  if (richiesta.method !== 'GET' || new URL(richiesta.url).origin !== self.location.origin) return;
  evento.respondWith(
    // ignoreVary: alcuni server (anche di hosting) rispondono con «Vary: Origin» e senza questa opzione la copia offline
    // dei file caricati come moduli non verrebbe riconosciuta.
    caches.match(richiesta, { ignoreSearch: true, ignoreVary: true }).then((trovato) => {
      if (trovato) return trovato;
      if (richiesta.mode === 'navigate') return caches.match('./', { ignoreVary: true });
      return fetch(richiesta);
    }),
  );
});
