import { api } from './api';

/** Web push on this browser (the hub's push.py): what it can do here, and turning it on or off.
 * Browsers allow it only on secure pages (HTTPS, or localhost), and iOS only in the installed app. */
export type PushState = 'unsupported' | 'insecure' | 'denied' | 'off' | 'on';

const supported = () =>
	typeof window !== 'undefined' &&
	'serviceWorker' in navigator &&
	'PushManager' in window &&
	'Notification' in window;

async function registration() {
	return (await navigator.serviceWorker.getRegistration('/')) ?? null;
}

export async function pushState(): Promise<PushState> {
	if (typeof window !== 'undefined' && !window.isSecureContext) return 'insecure';
	if (!supported()) return 'unsupported';
	if (Notification.permission === 'denied') return 'denied';
	const sub = await (await registration())?.pushManager.getSubscription();
	return sub ? 'on' : 'off';
}

/** The browser on its own, for the list ("Firefox on macOS"). */
export function browserLabel(ua = navigator.userAgent): string {
	const browser = /Edg\//.test(ua)
		? 'Edge'
		: /Firefox\//.test(ua)
			? 'Firefox'
			: /Chrome\//.test(ua)
				? 'Chrome'
				: /Safari\//.test(ua)
					? 'Safari'
					: 'A browser';
	const os = /iPhone|iPad/.test(ua)
		? 'iOS'
		: /Android/.test(ua)
			? 'Android'
			: /Mac OS X/.test(ua)
				? 'macOS'
				: /Windows/.test(ua)
					? 'Windows'
					: /Linux/.test(ua)
						? 'Linux'
						: '';
	return os ? `${browser} on ${os}` : browser;
}

const key = (b64: string) => {
	const raw = atob(
		b64.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (b64.length % 4)) % 4)
	);
	return Uint8Array.from(raw, (c) => c.charCodeAt(0));
};

export async function turnOn(publicKey: string): Promise<void> {
	if (!supported()) throw new Error("This browser can't receive push here");
	if ((await Notification.requestPermission()) !== 'granted')
		throw new Error('Notifications are blocked for this page (see the browser’s site settings)');
	const reg = (await registration()) ?? (await navigator.serviceWorker.register('/sw.js'));
	await navigator.serviceWorker.ready;
	const sub =
		(await reg.pushManager.getSubscription()) ??
		(await reg.pushManager.subscribe({
			userVisibleOnly: true,
			applicationServerKey: key(publicKey)
		}));
	await api.pushSubscribe(sub.toJSON(), browserLabel());
}

export async function turnOff(): Promise<void> {
	const sub = await (await registration())?.pushManager.getSubscription();
	if (!sub) return;
	await api.pushUnsubscribe(sub.endpoint);
	await sub.unsubscribe();
}

/** This browser's subscription, to tell it apart in the list. */
export async function thisEndpoint(): Promise<string | null> {
	if (!supported()) return null;
	return (await (await registration())?.pushManager.getSubscription())?.endpoint ?? null;
}
