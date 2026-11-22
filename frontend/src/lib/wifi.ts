import type { Device, WifiClient, WifiFinding, WifiNetwork, WifiRadio, WifiScanView } from './api';
import { bandLabel } from './format';
import type { State } from './states';

/** What the Wi-Fi page and the device pages' Wi-Fi sections show, worked out once: a client's
 * signal in words, the access points (radios and FRITZ!Mesh repeaters) as rows, how busy each
 * channel is. The page and its entity tables (components/Wifi*.svelte) read these. */

/** The channels of a band, as the charts' axes show them. */
export const CHANNELS: Record<string, number[]> = {
	'2g': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13],
	'5g': [
		36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 149, 153,
		157, 161, 165
	]
};

// 5 GHz blocks by width (wifi.py BLOCKS): the first channel of each.
const BLOCKS: Record<number, number[]> = {
	40: [36, 44, 52, 60, 100, 108, 116, 124, 132, 140, 149, 157],
	80: [36, 52, 100, 116, 132, 149],
	160: [36, 100]
};

/** A client's signal in words: Good, Fair or Weak. OpenWrt says dBm (weak below -75, as
 * wifi.diagnose), a FRITZ!Box percent (weak below 30). Null when not known. */
export function signalState(c: Pick<WifiClient, 'kind' | 'signal'>): State | null {
	const s = c.signal;
	if (s == null) return null;
	const [good, fair] = c.kind === 'fritzbox' ? [60, 30] : [-65, -75];
	if (s >= good) return { word: 'Good', tone: 'good' };
	if (s >= fair) return { word: 'Fair', tone: 'neutral' };
	return { word: 'Weak', tone: 'warning' };
}

/** The signal as measured: "-48 dBm" or "64 %". */
export const signalText = (c: Pick<WifiClient, 'kind' | 'signal'>) =>
	c.signal == null ? '' : c.kind === 'fritzbox' ? `${c.signal} %` : `${c.signal} dBm`;

/** A radio's state: On, Off (by choice), Down (meant to be on, isn't up). */
export function radioState(r: Pick<WifiRadio, 'state'>): State {
	if (r.state === 'off') return { word: 'Off', tone: 'neutral' };
	if (r.state === 'down') return { word: 'Down', tone: 'critical' };
	return { word: 'On', tone: 'good' };
}

/** A shared SSID may be off on one radio and still on elsewhere on the same device. */
export function networkState(n: WifiNetwork, device?: string): State {
	const on = n.on.filter((e) => !device || e.device === device);
	if (!on.length) return { word: 'Not read', tone: 'neutral' };
	const off = on.filter((e) => e.disabled).length;
	if (!off) return { word: 'On', tone: 'good' };
	return { word: off === on.length ? 'Off' : 'Partly off', tone: 'neutral' };
}

/** One row of the access points table: a radio of an OpenWrt device or a FRITZ!Box, or a
 * FRITZ!Mesh repeater the box reports (not a Kastellan device: no radio of its own here). */
export interface AccessPoint {
	key: string;
	device: string | null; // the Kastellan device; null for a mesh repeater
	name: string;
	radio: WifiRadio | null;
	band: string | null;
	channel: number | null;
	width: number | null;
	clients: number | null;
	busy: number | null;
	networks: string[];
	note: string; // the line under the name: band, mesh
	state: State;
}

/** The networks a radio carries (a FRITZ!Box's by band: its guest network is another wlanN). */
export function networksOn(r: WifiRadio, networks: WifiNetwork[]): string[] {
	return networks
		.filter((n) =>
			n.on.some(
				(e) =>
					e.device === r.device &&
					!e.disabled &&
					(e.radio === r.radio || (r.kind === 'fritzbox' && e.band === r.band))
			)
		)
		.map((n) => n.ssid);
}

/** Every access point's radios, and the FRITZ!Mesh repeaters, sorted by name then band. */
export function accessPoints(
	radios: WifiRadio[],
	networks: WifiNetwork[],
	devices: Device[],
	name: (id: string) => string
): AccessPoint[] {
	const out: AccessPoint[] = radios.map((r) => {
		const master = devices.find((d) => d.id === r.device)?.fritz?.mesh?.members.length;
		return {
			key: `${r.device}/${r.radio}`,
			device: r.device,
			name: name(r.device),
			radio: r,
			band: r.band,
			channel: r.channel,
			width: r.width,
			clients: r.clients,
			busy: r.busy,
			networks: networksOn(r, networks),
			note: [
				bandLabel(r.band),
				r.mesh != null ? `mesh ${r.mesh || '(no id)'}` : '',
				master ? 'FRITZ!Mesh master' : ''
			]
				.filter(Boolean)
				.join(' · '),
			state: radioState(r)
		};
	});
	for (const d of devices) {
		const mesh = d.fritz?.mesh;
		for (const m of mesh?.members ?? []) {
			if (m.role === 'master') continue;
			const link = mesh!.links.find((l) => l.a === m.uid || l.b === m.uid);
			const how = !link
				? ''
				: link.type === 'WLAN'
					? `Wi-Fi${link.signal != null ? ` ${link.signal} dBm` : ''}`
					: link.type === 'LAN'
						? 'cable'
						: (link.type ?? '');
			out.push({
				key: `mesh/${d.id}/${m.uid}`,
				device: null,
				name: m.name ?? m.model ?? m.uid,
				radio: null,
				band: null,
				channel: null,
				width: null,
				clients: null,
				busy: null,
				networks: [],
				note: ['FRITZ!Mesh repeater', how ? `${how} to ${d.name}` : '', m.model ?? '']
					.filter(Boolean)
					.join(' · '),
				state: { word: 'In mesh', tone: 'good' }
			});
		}
	}
	const band = (b: string | null) => (b ? Object.keys(CHANNELS).indexOf(b) : 9);
	return out.sort((a, b) => a.name.localeCompare(b.name) || band(a.band) - band(b.band));
}

/** The channel of a frequency in MHz. */
export function channelOf(mhz: number): number {
	if (mhz === 2484) return 14;
	return mhz < 3000 ? (mhz - 2407) / 5 : (mhz - 5000) / 5;
}

/** The channels a radio on `channel` at `width` MHz covers: on 2.4 GHz two either side for
 * 20 MHz (a signal is 22 MHz wide), on 5 GHz its whole block. */
export function covers(channel: number, width: number, band: string): number[] {
	if (band === '2g') {
		const side = Math.max(2, Math.round(width / 10));
		return CHANNELS['2g'].filter((c) => Math.abs(c - channel) <= side);
	}
	const starts = BLOCKS[width];
	if (!starts) return [channel];
	const size = width / 20;
	const start = [...starts].reverse().find((s) => s <= channel && channel < s + 4 * size);
	return start == null ? [channel] : Array.from({ length: size }, (_, i) => start + 4 * i);
}

/** How busy a channel was where our radios listened, and who they heard on it. */
export interface ChannelUse {
	channel: number;
	busy: number | null; // the highest share any radio measured, in percent
	by: { name: string; busy: number }[];
	heard: { ssid: string; signal: number | null }[];
}

/** One band's channels with what the newest scans say. `scans` is keyed "device/radio". */
export function channelUse(
	band: string,
	scans: Record<string, WifiScanView>,
	name: (id: string) => string
): ChannelUse[] {
	const mine = Object.entries(scans).filter(([, s]) => s.band === band);
	const extra = mine.flatMap(([, s]) => [
		...s.survey.map((x) => channelOf(x.mhz)),
		...Object.keys(s.neighbours).map(Number)
	]);
	const channels = [...new Set([...(CHANNELS[band] ?? []), ...extra])]
		.filter((c) => Number.isInteger(c) && c > 0)
		.sort((a, b) => a - b);
	return channels.map((channel) => {
		const by = mine.flatMap(([key, s]) =>
			s.survey
				.filter((x) => channelOf(x.mhz) === channel)
				.map((x) => ({ name: name(key.split('/')[0]), busy: x.busy }))
		);
		const loudest = new Map<string, number | null>();
		for (const [, s] of mine)
			for (const n of s.neighbours[String(channel)] ?? []) {
				const was = loudest.get(n.ssid);
				if (was === undefined || (n.signal ?? -200) > (was ?? -200)) loudest.set(n.ssid, n.signal);
			}
		return {
			channel,
			busy: by.length ? Math.max(...by.map((b) => b.busy)) : null,
			by,
			heard: [...loudest].map(([ssid, signal]) => ({ ssid, signal }))
		};
	});
}

/** The busiest channel any radio measured, over every band. */
export function busiest(
	scans: Record<string, WifiScanView>
): { band: string; channel: number; busy: number } | null {
	let best: { band: string; channel: number; busy: number } | null = null;
	for (const s of Object.values(scans))
		for (const x of s.survey)
			if (!best || x.busy > best.busy)
				best = { band: s.band, channel: channelOf(x.mhz), busy: x.busy };
	return best;
}

/** Whether a client matches a search: its name, MAC, address, network or access point. */
export function clientMatches(c: WifiClient, query: string, apName: string): boolean {
	const q = query.trim().toLowerCase();
	if (!q) return true;
	return [c.name, c.mac, c.address, c.ssid, apName].some((v) => v?.toLowerCase().includes(q));
}

/** The findings an open alarm doesn't already say (the alarm is shown instead), about
 * `device` when given. */
export function openFindings(
	findings: WifiFinding[],
	issueKeys: Iterable<string>,
	device?: string
): WifiFinding[] {
	const open = new Set(issueKeys);
	return findings.filter(
		(f) =>
			(!device || f.devices.includes(device)) &&
			!(f.alarms.length && f.alarms.every((k) => open.has(k)))
	);
}
