// Çok Lekker service worker: lets the installed app open, and show the recipes it saw
// last, without a connection. The page and /api/recipes are fetched network-first, so
// online nothing is stale; the copies are only used when the network is gone.
//
// A login proxy (Authentik) answers an expired session with a redirect. Those responses
// are passed through untouched and never cached: the page notices them and signs in again.

var VERSION = 'v1';
var CACHE = 'coklekker-' + VERSION;
var FONTS = 'coklekker-fonts';
var SHELL = ['/', '/manifest.webmanifest', '/icons/favicon.svg', '/icons/icon-192.png', '/icons/apple-touch-icon.png'];

self.addEventListener('install', function(event){
  // Best effort: if the session has expired, these fail and the first online visit fills them.
  event.waitUntil(caches.open(CACHE).then(function(cache){
    return Promise.all(SHELL.map(function(path){
      return fetch(path, { credentials:'same-origin', redirect:'manual' }).then(function(res){
        if(cacheable(res)) return cache.put(path, res);
      }).catch(function(){});
    }));
  }).then(function(){ return self.skipWaiting(); }));
});

self.addEventListener('activate', function(event){
  event.waitUntil(caches.keys().then(function(keys){
    return Promise.all(keys.filter(function(k){ return k !== CACHE && k !== FONTS; })
      .map(function(k){ return caches.delete(k); }));
  }).then(function(){ return self.clients.claim(); }));
});

function cacheable(res){
  return res && res.ok && res.type === 'basic' && !res.redirected;
}

// Try the network, keep a copy under `key`, and fall back to that copy when offline.
function networkFirst(event, key, markOffline){
  var request = event.request;
  return fetch(request).then(function(res){
    if(cacheable(res)){
      var copy = res.clone();
      event.waitUntil(caches.open(CACHE).then(function(cache){ return cache.put(key, copy); }));
    }
    return res;
  }).catch(function(err){
    return caches.open(CACHE).then(function(cache){ return cache.match(key); }).then(function(hit){
      if(!hit) throw err;
      if(!markOffline) return hit;
      // Tell the page this is the saved copy, so it can say it is offline.
      var headers = new Headers(hit.headers);
      headers.set('X-Offline', '1');
      return hit.blob().then(function(body){
        return new Response(body, { status:hit.status, statusText:hit.statusText, headers:headers });
      });
    });
  });
}

// Google Fonts: the CSS may change, so refresh it in the background; font files never do.
function fonts(event){
  var request = event.request;
  var refresh = new URL(request.url).hostname === 'fonts.googleapis.com';
  return caches.open(FONTS).then(function(cache){
    return cache.match(request).then(function(hit){
      if(hit && !refresh) return hit;
      var fresh = fetch(request).then(function(res){
        if(res.ok) return cache.put(request, res.clone()).then(function(){ return res; });
        return res;
      });
      if(!hit) return fresh;
      event.waitUntil(fresh.catch(function(){}));
      return hit;
    });
  });
}

self.addEventListener('fetch', function(event){
  var request = event.request;
  if(request.method !== 'GET') return;  // saving and importing always go to the server
  var url = new URL(request.url);

  if(url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com'){
    event.respondWith(fonts(event));
    return;
  }
  if(url.origin !== self.location.origin) return;

  if(request.mode === 'navigate'){
    // The share target opens /?url=...; any query string is the same page.
    event.respondWith(networkFirst(event, '/'));
  }else if(url.pathname === '/api/recipes'){
    event.respondWith(networkFirst(event, '/api/recipes', true));
  }else if(url.pathname === '/manifest.webmanifest' || url.pathname.indexOf('/icons/') === 0){
    event.respondWith(networkFirst(event, url.pathname));
  }
});
