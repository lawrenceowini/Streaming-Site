// Service worker for LiveCam's incoming-call push notifications.
// This file must be served from the root of the deployed frontend (so its
// scope covers the whole site) -- Vercel does this automatically since
// frontend/ is the project root.
//
// This worker does two things:
//   1. Shows a notification when a push arrives (even if no tab is open).
//   2. When that notification is clicked, opens or focuses a tab at
//      /?room=<code> so the person lands straight on the right room.

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (e) {
    payload = {};
  }

  const title = payload.title || 'Incoming call';
  const options = {
    body: payload.body || 'Tap to join the call.',
    tag: 'livecam-incoming-call', // replaces any earlier "incoming call" notification rather than stacking
    requireInteraction: true, // stays on screen until the person acts on it, not just a few seconds
    data: { roomCode: payload.room_code || '', from: payload.from || '' },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const roomCode = (event.notification.data && event.notification.data.roomCode) || '';
  const from = (event.notification.data && event.notification.data.from) || '';
  const qs = new URLSearchParams();
  if (roomCode) qs.set('room', roomCode);
  if (from) qs.set('from', from);
  const targetUrl = new URL(
    qs.toString() ? `/?${qs.toString()}` : '/',
    self.location.origin
  ).href;

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if ('focus' in client) {
          if ('navigate' in client) {
            client.navigate(targetUrl).catch(() => {});
          }
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
    })
  );
});
