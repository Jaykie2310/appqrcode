// static/sw.js
const CACHE_NAME = 'qr-manager-cache-v1.1'; // Thay đổi version khi cập nhật cache
const URLS_TO_CACHE = [
  '/quan-ly-san-pham/tong-quan', // Trang bắt đầu của PWA
  '/static/style.css',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-512x512.png',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css',
  'https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap'
  // Bạn có thể thêm các URL tĩnh khác mà bạn muốn cache ở đây
  // Ví dụ: '/static/some-image.png', '/offline.html'
];

// Sự kiện install: được gọi khi service worker được cài đặt
self.addEventListener('install', event => {
  console.log('[Service Worker] Install event in progress.');
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('[Service Worker] Opened cache and caching initial assets:', URLS_TO_CACHE);
        return cache.addAll(URLS_TO_CACHE);
      })
      .catch(error => {
        console.error('[Service Worker] Failed to cache initial assets:', error);
      })
  );
});

// Sự kiện activate: được gọi sau khi install và khi service worker mới được kích hoạt
self.addEventListener('activate', event => {
  console.log('[Service Worker] Activate event in progress.');
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cache => {
          if (cache !== CACHE_NAME) {
            console.log('[Service Worker] Deleting old cache:', cache);
            return caches.delete(cache);
          }
        })
      );
    })
  );
  self.clients.claim(); // Cho phép service worker kiểm soát các client ngay lập tức
});

// Sự kiện fetch: được gọi mỗi khi có một yêu cầu mạng từ ứng dụng
self.addEventListener('fetch', event => {
  // Bỏ qua các request không phải là GET
  if (event.request.method !== 'GET') {
    return;
  }

  event.respondWith(
    caches.match(event.request)
      .then(cachedResponse => {
        if (cachedResponse) {
          // console.log('[Service Worker] Returning from cache:', event.request.url);
          return cachedResponse;
        }

        // console.log('[Service Worker] Network request for:', event.request.url);
        return fetch(event.request).then(networkResponse => {
          if (!networkResponse || networkResponse.status !== 200 || networkResponse.type !== 'basic') {
            return networkResponse; // Trả về nếu không phải là response tốt
          }

          // Clone response để có thể lưu vào cache và trả về cho trình duyệt
          let responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME)
            .then(cache => {
              // console.log('[Service Worker] Caching new asset:', event.request.url);
              cache.put(event.request, responseToCache);
            });
          return networkResponse;
        }).catch(error => {
          console.error('[Service Worker] Fetch failed; returning offline page if available.', error);
          // (Tùy chọn) Trả về một trang offline.html dự phòng nếu có lỗi mạng và không có trong cache
          // return caches.match('/offline.html');
        });
      })
  );
});