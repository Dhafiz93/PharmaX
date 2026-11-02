/* Service worker PharmaX.
   Stratégie prudente : le réseau reste prioritaire pour les pages et l'API,
   afin de ne jamais servir une liste de gardes périmée. Seules les ressources
   statiques (logos, feuilles de style, scripts) sont mises en cache. */

const VERSION = 'pharmax-v1';
const CACHE_STATIQUE = `${VERSION}-statique`;

const RESSOURCES = [
  '/static/css/topbar.css',
  '/static/css/chat.css',
  '/static/css/app-ui.css',
  '/static/css/pwa.css',
  '/static/js/pwa.js',
  '/static/img/logo-icon.png',
  '/static/img/logo-full.png',
  '/static/img/icons/icon-192.png',
  '/static/img/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_STATIQUE)
      .then((cache) => cache.addAll(RESSOURCES).catch(() => null))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cles) => Promise.all(
        cles.filter((c) => !c.startsWith(VERSION)).map((c) => caches.delete(c))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const requete = event.request;
  if (requete.method !== 'GET') return;

  const url = new URL(requete.url);
  if (url.origin !== self.location.origin) return;

  // Les données de garde ne doivent jamais venir du cache
  if (url.pathname.startsWith('/api/')) return;

  // Ressources statiques : cache d'abord, réseau en secours
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(requete).then((reponse) => reponse || fetch(requete).then((reseau) => {
        const copie = reseau.clone();
        caches.open(CACHE_STATIQUE).then((cache) => cache.put(requete, copie));
        return reseau;
      }).catch(() => reponse))
    );
    return;
  }

  // Pages : réseau d'abord, cache en secours hors connexion
  if (requete.mode === 'navigate') {
    event.respondWith(
      fetch(requete)
        .then((reseau) => {
          const copie = reseau.clone();
          caches.open(CACHE_STATIQUE).then((cache) => cache.put(requete, copie));
          return reseau;
        })
        .catch(() => caches.match(requete).then((c) => c || caches.match('/assistant')))
    );
  }
});
