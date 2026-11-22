import type { VitalField, Vitals } from '#lib/api.js';

/** When a vital counts as high (warning) and very high (critical): colour of its bar only (no
 * alarms). Storage follows the disk alarms' limits (config.yaml's alarms:). */
export function limitsOf(
	field: VitalField,
	disk: { warning: number; critical: number }
): { warning: number; critical: number } {
	if (field === 'storage') return disk;
	if (field === 'memory') return { warning: 90, critical: 97 };
	if (field === 'temperature') return { warning: 80, critical: 90 };
	return { warning: 85, critical: 95 };
}

export type Tone = 'normal' | 'warning' | 'critical';

export const toneOf = (v: number | null, l: { warning: number; critical: number }): Tone =>
	v == null ? 'normal' : v >= l.critical ? 'critical' : v >= l.warning ? 'warning' : 'normal';

export const toneColor: Record<Tone, string> = {
	normal: 'var(--accent)',
	warning: 'var(--warning)',
	critical: 'var(--critical)'
};

export const LABEL: Record<VitalField, string> = {
	cpu: 'CPU',
	memory: 'Memory',
	storage: 'Storage',
	temperature: 'Temperature'
};

export const formatVital = (field: VitalField, v: number | null | undefined) =>
	v == null ? '–' : field === 'temperature' ? `${v.toFixed(0)} °C` : `${v.toFixed(0)} %`;

export function bytes(n: number): string {
	if (n >= 1e12) return `${(n / 1e12).toFixed(1)} TB`;
	if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
	if (n >= 1e6) return `${(n / 1e6).toFixed(0)} MB`;
	return `${(n / 1e3).toFixed(0)} kB`;
}

/** What a vital's number is of: "1.2 of 4.0 GB", "/data", "load 0.42 · 4 cores". */
export function detailOf(field: VitalField, v: Vitals): string {
	if (field === 'cpu') {
		const parts = [];
		if (v.load != null) parts.push(`load ${v.load.toFixed(2)}`);
		if (v.cores) parts.push(`${v.cores} ${v.cores === 1 ? 'core' : 'cores'}`);
		return parts.join(' · ');
	}
	if (field === 'memory')
		return v.memory_total && v.memory_total > 100 && v.memory_used != null
			? `${bytes(v.memory_used)} of ${bytes(v.memory_total)}`
			: '';
	if (field === 'storage')
		return v.storage_total && v.storage_used != null
			? `${bytes(v.storage_used)} of ${bytes(v.storage_total)}${v.storage_mount ? ` · ${v.storage_mount}` : ''}`
			: '';
	return '';
}

/** The fields a device tells (the CPU also by its load alone, without a percentage). */
export function fieldsOf(v: Vitals | null | undefined): VitalField[] {
	if (!v) return [];
	const out: VitalField[] = [];
	if (v.cpu != null || v.load != null) out.push('cpu');
	if (v.memory != null) out.push('memory');
	if (v.storage != null) out.push('storage');
	if (v.temperature != null) out.push('temperature');
	return out;
}

/** The slowest source (FRITZ!Box) reads every five minutes. */
export const isStale = (v: Vitals, now: number) =>
	!Number.isFinite(Date.parse(v.checked)) || now - Date.parse(v.checked) > 10 * 60 * 1000;
