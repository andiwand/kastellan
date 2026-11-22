import type { FritzReport } from '#lib/api.js';

/** A way in from the internet a router opens (a FRITZ!Box's enabled port forwards, its remote
 * access, MyFRITZ!): what the router's "Ways in" table lists. `warn`: worth knowing it is open. */
export interface WayIn {
	key: string;
	kind: string; // "Port forward", "Remote access", "MyFRITZ!"
	what: string | null; // "TCP 443 → 192.168.178.20:443", "HTTPS, port 8443"
	name: string | null;
	warn: boolean;
}

export function waysIn(fritz: FritzReport): WayIn[] {
	const out: WayIn[] = (fritz.forwards ?? [])
		.filter((f) => f.enabled)
		.map((f, i) => ({
			key: `forward:${i}`,
			kind: 'Port forward',
			what: `${f.protocol ?? ''} ${f.port ?? '?'} → ${f.to ?? '?'}:${f.to_port ?? '?'}`.trim(),
			name: f.name,
			warn: true
		}));
	if (fritz.remote.remote_access)
		out.push({
			key: 'remote',
			kind: 'Remote access',
			what: `HTTPS${fritz.remote.remote_port ? `, port ${fritz.remote.remote_port}` : ''}`,
			name: 'to the FRITZ!Box',
			warn: true
		});
	if (fritz.remote.myfritz)
		out.push({ key: 'myfritz', kind: 'MyFRITZ!', what: null, name: 'is on', warn: false });
	return out;
}

/** Whether a router's ways in couldn't all be read (then "none" isn't known). */
export const waysInUnknown = (fritz: FritzReport) =>
	fritz.forwards === null || fritz.remote.remote_access == null || fritz.remote.myfritz == null;
