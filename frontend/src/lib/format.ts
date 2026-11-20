/** "now", "12 s ago", "4 min ago", "3 h ago", "2 d ago". */
export function ago(iso: string | null | undefined, now = Date.now()): string {
	if (!iso) return '';
	const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
	if (s < 5) return 'now';
	if (s < 60) return `${s} s ago`;
	if (s < 3600) return `${Math.floor(s / 60)} min ago`;
	if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
	return `${Math.floor(s / 86400)} d ago`;
}

/** A duration in seconds as "45 s", "12 min", "3 h 05 min", "2 d 4 h". */
export function duration(seconds: number): string {
	const s = Math.max(0, Math.round(seconds));
	if (s < 60) return `${s} s`;
	const m = Math.floor(s / 60);
	if (m < 60) return `${m} min`;
	const h = Math.floor(m / 60);
	if (h < 48) return `${h} h ${String(m % 60).padStart(2, '0')} min`;
	return `${Math.floor(h / 24)} d ${h % 24} h`;
}

export function since(iso: string | null | undefined, now = Date.now()): string {
	return iso ? duration((now - Date.parse(iso)) / 1000) : '';
}

export function ms(value: number | null | undefined): string {
	if (value == null) return '–';
	if (value < 1) return `${value.toFixed(2)} ms`;
	if (value < 10) return `${value.toFixed(1)} ms`;
	return `${Math.round(value)} ms`;
}

/** 0.99712 → "99.71 %", 1 → "100 %". */
export function percent(value: number | null | undefined): string {
	if (value == null) return '–';
	if (value >= 0.99995) return '100 %';
	return `${(Math.floor(value * 10000) / 100).toFixed(2)} %`;
}

export function clock(iso: string | Date): string {
	return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function dayClock(iso: string | Date): string {
	const d = new Date(iso);
	const today = new Date();
	const time = clock(d);
	if (d.toDateString() === today.toDateString()) return time;
	const yesterday = new Date(today.getTime() - 86400000);
	if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${time}`;
	return `${d.toLocaleDateString([], { day: 'numeric', month: 'short' })} ${time}`;
}

/** How long a check has been in its state: "12 min", or "at least 3 h" when the state began
 * before the oldest result the hub still had at its last start. */
export function stateFor(
	check: { since?: string | null; since_exact?: boolean },
	now = Date.now()
) {
	if (!check.since) return '';
	const d = since(check.since, now);
	return check.since_exact === false ? `at least ${d}` : d;
}
