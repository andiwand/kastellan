import type { HostReport } from '#lib/api.js';
import { diskState, raidState, type State } from '#lib/states.js';
import { toneOf, type Limits, type Tone } from '#lib/vitals.js';

/** A host's storage as rows of its two tables (StorageSection): volumes (filesystems: how full,
 * when full at the rate they fill) and disks (RAID arrays and single disks: health, temperature,
 * age). From the collector's report. */

export interface Volume {
	mount: string;
	device: string;
	type: string;
	total: number;
	used: number;
	free: number;
	percent: number; // used of used + free (what a user can still write)
	tone: Tone; // the bar's colour, by the disk alarms' limits
	fullIn: number | null; // days until full at the rate it filled, when it fills
	soon: boolean; // full within the alarm's days
}

export function volumesOf(
	report: HostReport,
	forecast: Record<string, number>,
	limits: Limits,
	soonDays = 14
): Volume[] {
	return (report.filesystems ?? []).map((fs) => {
		const percent = fs.used + fs.available ? (100 * fs.used) / (fs.used + fs.available) : 0;
		const fullIn = forecast[fs.mount] ?? null;
		return {
			mount: fs.mount,
			device: fs.device,
			type: fs.type,
			total: fs.total,
			used: fs.used,
			free: fs.available,
			percent,
			tone: toneOf(percent, limits),
			fullIn,
			soon: fullIn != null && fullIn < soonDays
		};
	});
}

/** "within a day", "in ~5 d". */
export const fullInText = (days: number) =>
	days < 1 ? 'within a day' : `in ~${Math.round(days)} d`;

export interface DiskRow {
	key: string;
	kind: 'raid' | 'disk';
	name: string; // md0, sda
	what: string; // "raid1 of sda1, sdb1", the model
	state: State;
	note: string | null; // what the state can't say: asleep, an error, a resync's progress
	temperature: number | null;
	years: number | null; // powered on, in years (one decimal)
	status: string | null; // the array's member map, e.g. [UU_]
}

/** Its wear: reallocated, pending and uncorrectable sectors and NVMe media errors. */
const wearOf = (d: NonNullable<HostReport['smart']>['disks'][number]) =>
	(d.reallocated ?? 0) + (d.pending ?? 0) + (d.uncorrectable ?? 0) + (d.nvme_media_errors ?? 0);

/** RAID arrays first (a degraded one is what matters), then the disks, each with one State. */
export function disksOf(report: HostReport): DiskRow[] {
	const raid: DiskRow[] = (report.raid ?? []).map((md) => {
		const bad = !md.active || md.status.includes('_') || md.failed.length > 0;
		return {
			key: `md:${md.name}`,
			kind: 'raid',
			name: md.name,
			what: `${md.level} of ${md.devices.join(', ')}`,
			state: raidState(bad),
			note: md.progress
				? `${md.progress.what} ${md.progress.percent}%`
				: md.failed.length
					? `failed: ${md.failed.join(', ')}`
					: null,
			temperature: null,
			years: null,
			status: md.status ? `[${md.status}]` : null
		};
	});
	const disks: DiskRow[] = (report.smart?.disks ?? []).map((d) => {
		const wear = wearOf(d);
		const read = !d.asleep && !d.error && (d.passed != null || wear > 0);
		return {
			key: `disk:${d.disk}`,
			kind: 'disk',
			name: d.disk,
			what: d.model ?? '',
			state: read ? diskState(d.passed, wear) : { word: 'Not read', tone: 'neutral' },
			note: d.asleep
				? 'asleep (not woken to read it)'
				: d.error
					? d.error
					: read
						? null
						: 'health unknown',
			temperature: d.temperature ?? null,
			years: d.power_on_hours != null ? Math.round((d.power_on_hours / 24 / 365) * 10) / 10 : null,
			status: null
		};
	});
	return [...raid, ...disks];
}
