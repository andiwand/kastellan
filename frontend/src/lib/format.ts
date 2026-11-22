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

/* Times: one format everywhere. Relative while recent ("5 min ago", `ago`), the clock for
   today ("14:05", `clock`), the day and the clock before that ("Nov 22, 14:05", `day`), both in
   lists (`dayClock`: "14:05", "Yesterday 14:05", "Nov 22, 14:05"), durations ("3 h 05 min",
   `duration`). Always 24-hour, month names in English; the year only when it isn't this one. */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad = (n: number) => String(n).padStart(2, '0');
const toDate = (t: string | number | Date) =>
	t instanceof Date ? t : new Date(typeof t === 'number' ? t * 1000 : t);

/** A time as "14:05" (`seconds`: "14:05:07"). Takes ISO text, a Date or Unix seconds. */
export function clock(t: string | number | Date, seconds = false): string {
	const d = toDate(t);
	const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
	return seconds ? `${hm}:${pad(d.getSeconds())}` : hm;
}

/** A date as "Nov 22", or "Nov 22 2025" in another year. */
export function date(t: string | number | Date, now = Date.now()): string {
	const d = toDate(t);
	const year = d.getFullYear() === new Date(now).getFullYear() ? '' : ` ${d.getFullYear()}`;
	return `${MONTHS[d.getMonth()]} ${d.getDate()}${year}`;
}

/** A date and time as "Nov 22, 14:05" (`seconds`: "Nov 22, 14:05:07"). */
export function day(t: string | number | Date, now = Date.now(), seconds = false): string {
	return `${date(t, now)}, ${clock(t, seconds)}`;
}

/** A time in a list: "14:05" today, "Yesterday 14:05", else "Nov 22, 14:05". */
export function dayClock(t: string | number | Date, now = Date.now(), seconds = false): string {
	const d = toDate(t);
	const today = new Date(now);
	if (d.toDateString() === today.toDateString()) return clock(d, seconds);
	const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
	if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${clock(d, seconds)}`;
	return day(d, now, seconds);
}

/** A time a device writes as text ("2026-11-12 03:41:07 +0100", apcupsd) in our format
 * ("Nov 12 2026, 03:41"); text that isn't a time stays as it is. */
export function stamp(text: string, now = Date.now()): string {
	const m = text.trim().match(/^(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d(?::\d\d)?)\s*([+-]\d\d):?(\d\d)?$/);
	const t = m ? Date.parse(`${m[1]}T${m[2]}${m[3]}:${m[4] ?? '00'}`) : NaN;
	return Number.isNaN(t) ? text : day(new Date(t), now);
}

/** A time range by its hours, as range pickers name it: "1 h", "24 h", "7 d", "30 d"; 0 is "All". */
export function hoursLabel(hours: number): string {
	if (hours === 0) return 'All';
	return hours < 48 || hours % 24 ? `${hours} h` : `${hours / 24} d`;
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

/** What a software change is about: a check's name, or the hub's internal version keys
 * (hub.py) "@firmware" and "@container <key>" said in words. */
export function versionSubject(check: string): string {
	if (check === '@firmware') return 'Firmware';
	if (check.startsWith('@container ')) return `Container ${check.slice('@container '.length)}`;
	return check;
}

/** A size in bytes, in SI units: "512 B", "12 kB", "4.3 GB", "340 GB" (a decimal below 10). */
export function bytes(n: number | null | undefined): string {
	if (n == null) return '–';
	const units = ['B', 'kB', 'MB', 'GB', 'TB'];
	let i = 0;
	while (n >= 1000 && i < units.length - 1) {
		n /= 1000;
		i++;
	}
	return `${i && n < 10 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

/** A rate in bits per second: "0.4 Mbit/s", "95 Mbit/s", "1.2 Gbit/s" (a decimal below 10). */
export function bitrate(bits: number | null | undefined): string {
	if (bits == null) return '–';
	const [n, unit] = bits >= 1e9 ? [bits / 1e9, 'Gbit/s'] : [bits / 1e6, 'Mbit/s'];
	return `${n < 10 ? n.toFixed(1) : Math.round(n)} ${unit}`;
}

/** A rate the FRITZ!Box tells in kbit/s (DSL, mesh links). */
export const kbitrate = (kbit: number | null | undefined) =>
	bitrate(kbit == null ? null : kbit * 1000);

const BANDS: Record<string, string> = { '2g': '2.4 GHz', '5g': '5 GHz', '6g': '6 GHz' };

/** A Wi-Fi band as OpenWrt and the FRITZ!Box name it ("2g", "5g", "6g") in words; '' for none. */
export const bandLabel = (band: string | null | undefined) => (band ? (BANDS[band] ?? band) : '');

/** The Wi-Fi band a frequency in MHz is in, in words. */
export const bandOfFrequency = (mhz: number | null | undefined) =>
	mhz ? bandLabel(mhz < 3000 ? '2g' : mhz < 5950 ? '5g' : '6g') : '';
