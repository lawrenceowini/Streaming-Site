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

  const isMessage = payload.kind === 'message';
  const title = payload.title || (isMessage ? 'New message' : 'Incoming call');
  const options = {
    body: payload.body || (isMessage ? '' : 'Tap to join the call.'),
    // Messages replace only the notification for that same conversation, so
    // several different chats can each show their own; calls always replace
    // any earlier "incoming call" notification instead of stacking.
    tag: isMessage
      ? `livecam-message-${payload.conversation_id || ''}`
      : 'livecam-incoming-call',
    requireInteraction: !isMessage, // a call stays on screen until acted on; a message behaves like a normal notification
    // Browsers/OSes play their own default notification sound automatically
    // here (there's no web API to use the device's actual ringtone file --
    // that's OS-private) -- vibration is the one extra native-feeling touch
    // we can add on top of that.
    vibrate: isMessage ? [200] : [400, 200, 400, 200, 400, 600],
    data: {
      kind: payload.kind || 'call',
      roomCode: payload.room_code || '',
      conversationId: payload.conversation_id || '',
      from: payload.from || '',
    },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const qs = new URLSearchParams();
  if (data.kind === 'message' && data.conversationId) {
    qs.set('chat', data.conversationId);
  } else {
    if (data.roomCode) qs.set('room', data.roomCode);
    if (data.from) qs.set('from', data.from);
  }
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
