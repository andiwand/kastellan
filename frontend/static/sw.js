// Kastellan's service worker: shows the alarms the hub pushes (push.py), and opens the device's
// page when one is clicked. Nothing is cached: the app always talks to the hub.
self.addEventListener('push', (event) => {
	let data = {};
	try {
		data = event.data ? event.data.json() : {};
	} catch {
		data = { title: event.data ? event.data.text() : 'Kastellan' };
	}
	event.waitUntil(
		self.registration.showNotification(data.title || 'Kastellan', {
			body: data.body || '',
			tag: data.tag,
			renotify: !!data.tag,
			icon: '/favicon.svg',
			data: { url: data.url || '/' }
		})
	);
});

self.addEventListener('notificationclick', (event) => {
	event.notification.close();
	const url = new URL(event.notification.data?.url || '/', self.location.origin).href;
	event.waitUntil(
		self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
			const open = windows.find((w) => new URL(w.url).origin === self.location.origin);
			return open ? open.navigate(url).then((w) => (w || open).focus()) : self.clients.openWindow(url);
		})
	);
});
