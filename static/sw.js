const CACHE_NAME = 'kinapp-v1';
const urlsToCache = [
    '/',
    '/static/index.html',
    '/static/manifest.json',
    '/static/icon-192.png',
    '/static/icon-512.png',
    'https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=Noto+Sans+JP:wght@300;400;500;700;900&display=swap',
    'https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200',
    'https://cdn.jsdelivr.net/npm/chart.js'
];

// Install event - cache resources
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => {
                console.log('Opened cache');
                return cache.addAll(urlsToCache);
            })
    );
    self.skipWaiting();
});

// Fetch event - serve from cache, fallback to network
self.addEventListener('fetch', event => {
    const { request } = event;
    const url = new URL(request.url);

    // API requests: Network-first strategy
    if (url.pathname.startsWith('/api/') ||
        url.pathname.startsWith('/memo') ||
        url.pathname.startsWith('/meals') ||
        url.pathname.startsWith('/weights') ||
        url.pathname.startsWith('/exercises') ||
        url.pathname.startsWith('/friends') ||
        url.pathname.startsWith('/users') ||
        url.pathname.startsWith('/settings')) {
        event.respondWith(
            fetch(request)
                .then(response => {
                    // Clone and cache successful responses
                    if (response.ok) {
                        const responseClone = response.clone();
                        caches.open(CACHE_NAME).then(cache => {
                            cache.put(request, responseClone);
                        });
                    }
                    return response;
                })
                .catch(() => {
                    // Fallback to cache if offline
                    return caches.match(request);
                })
        );
    }
    // Static assets: Cache-first strategy
    else {
        event.respondWith(
            caches.match(request)
                .then(response => {
                    return response || fetch(request).then(fetchResponse => {
                        return caches.open(CACHE_NAME).then(cache => {
                            cache.put(request, fetchResponse.clone());
                            return fetchResponse;
                        });
                    });
                })
                .catch(() => {
                    // Offline fallback page (optional)
                    if (request.destination === 'document') {
                        return caches.match('/');
                    }
                })
        );
    }
});

// Activate event - clean up old caches
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames.map(cacheName => {
                    if (cacheName !== CACHE_NAME) {
                        console.log('Deleting old cache:', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
    self.clients.claim();
});
