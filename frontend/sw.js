// Service worker for LiveCam's incoming-call push notifications.
// This file must be served from the root of the deployed frontend (so its
// scope covers the whole site) -- Vercel does this automatically since
// frontend/ is the project root.
//
// This worker does three things:
//   1. For an incoming call, checks whether a tab of this app is already
//      focused -- if so, messages that page directly so it can show a small
//      in-app popup instead of also throwing a system notification on top
//      of whatever the person is already looking at. There's no web API
//      that can make a *system* notification literally full-screen (that's
//      reserved for native calling apps registered with the OS's telecom
//      framework) -- but a page that's genuinely not in the foreground can
//      still show its own full-screen incoming-call UI once opened, which
//      is what happens in case 3 below.
//   2. Otherwise (no focused tab, or it's a message notification), shows a
//      normal system notification, even if no tab is open at all.
//   3. When that notification is clicked, opens or focuses a tab at
//      /?room=<code> so the person lands straight on the right room --
//      that page then shows its own full-screen incoming-call takeover,
//      since arriving this way means they weren't already using the app.

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (e) {
    payload = {};
  }
  const isCall = payload.kind !== 'message' && payload.kind !== 'group_message';

  event.waitUntil(
    (async () => {
      if (isCall) {
        const windowClients = await self.clients.matchAll({
          type: 'window',
          includeUncontrolled: true,
        });
        const focused = windowClients.find((c) => c.focused);
        if (focused) {
          focused.postMessage({
            type: 'incoming-call-live',
            roomCode: payload.room_code || '',
            from: payload.from || '',
          });
          return; // the page handles it directly -- no system notification needed
        }
      }

      const title = payload.title || (isCall ? 'Incoming call' : 'New message');
      const options = {
        body: payload.body || (isCall ? 'Tap to join the call.' : ''),
        // Messages replace only the notification for that same conversation/
        // group, so several different chats can each show their own; calls
        // always replace any earlier "incoming call" notification instead
        // of stacking.
        tag: isCall
          ? 'livecam-incoming-call'
          : payload.kind === 'group_message'
            ? `livecam-group-${payload.group_id || ''}`
            : `livecam-message-${payload.conversation_id || ''}`,
        requireInteraction: isCall, // a call stays on screen until acted on; a message behaves like a normal notification
        // Browsers/OSes play their own default notification sound automatically
        // here (there's no web API to use the device's actual ringtone file --
        // that's OS-private) -- vibration is the one extra native-feeling touch
        // we can add on top of that.
        vibrate: isCall ? [400, 200, 400, 200, 400, 600] : [200],
        data: {
          kind: payload.kind || 'call',
          roomCode: payload.room_code || '',
          conversationId: payload.conversation_id || '',
          groupId: payload.group_id || '',
          from: payload.from || '',
        },
      };
      await self.registration.showNotification(title, options);
    })()
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const qs = new URLSearchParams();
  if (data.kind === 'message' && data.conversationId) {
    qs.set('chat', data.conversationId);
  } else if (data.kind === 'group_message' && data.groupId) {
    qs.set('group', data.groupId);
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
