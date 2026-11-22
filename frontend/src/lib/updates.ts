import type { Container, Device } from '#lib/api.js';

/** Everything with an update waiting, across the network, from what the devices report:
 * containers (registries), system packages (the collector), reboots after updates, OpenWrt
 * firmware and packages, and Kastellan's own scripts on hosts (set up again to renew them). */
export type Update =
	| {
			kind: 'container';
			device: Device;
			key: string; // the container's key on its host (WUD's watcher/name, or the name)
			name: string;
			from: string;
			to: string;
			within?: string | null;
			compose?: string | null;
	  }
	| { kind: 'packages'; device: Device; count: number; security: number }
	| { kind: 'reboot'; device: Device; why: string[] }
	| {
			kind: 'firmware';
			device: Device;
			from: string;
			to: string;
			supported: boolean;
	  }
	// offered: new OpenWrt abilities, shown here since one setup brings both (not a row of its own)
	| { kind: 'scripts'; device: Device; names: string[]; offered?: string[] }
	| { kind: 'abilities'; device: Device; login: 'actor' | 'openwrt'; names: string[] };

export function updatesOf(devices: Device[]): Update[] {
	const out: Update[] = [];
	for (const d of devices) {
		for (const c of d.containers ?? []) {
			if (c.update && !c.unchecked && !c.skipped)
				out.push({
					kind: 'container',
					device: d,
					key: c.key,
					name: c.name,
					from: c.tag,
					to: updateTarget(c.update_to),
					within: c.update_within ?? null,
					compose: c.compose ?? null
				});
		}
		const u = d.host_report?.updates;
		const passed = new Set(d.updates_skipped ?? []); // said to be not needed, as they are now
		if (u?.available && !passed.has('packages'))
			out.push({ kind: 'packages', device: d, count: u.available, security: u.security ?? 0 });
		if (u?.reboot && !passed.has('reboot'))
			out.push({ kind: 'reboot', device: d, why: u.reboot_for ?? [] });
		const fw = d.router?.firmware;
		if (fw?.next_step && !passed.has('firmware'))
			out.push({
				kind: 'firmware',
				device: d,
				from: fw.current,
				to: fw.next_step,
				supported: fw.supported
			});
		// An OpenWrt device's package updates aren't listed: they come with its next firmware,
		// which Kastellan offers (firmware); the device page lists them.
		// The OpenWrt setup renews its helpers and adds abilities at once: one row for both.
		const fold = !!d.outdated_scripts?.includes('openwrt') && !!d.new_abilities?.openwrt?.length;
		if (d.outdated_scripts?.length)
			out.push({
				kind: 'scripts',
				device: d,
				names: d.outdated_scripts,
				...(fold ? { offered: d.new_abilities!.openwrt } : {})
			});
		for (const login of ['actor', 'openwrt'] as const) {
			const names = d.new_abilities?.[login];
			if (names?.length && !(fold && login === 'openwrt'))
				out.push({ kind: 'abilities', device: d, login, names });
		}
	}
	// What matters most first: no more fixes, security updates, internet-facing devices.
	const weight = (u: Update) =>
		(u.kind === 'firmware' && !u.supported ? 4 : 0) +
		(u.kind === 'packages' && u.security ? 3 : 0) +
		(u.device.exposed || u.device.checks.some((c) => c.exposed) ? 2 : 0) +
		(u.kind === 'reboot' ? 1 : 0);
	return out.sort((a, b) => weight(b) - weight(a) || a.device.name.localeCompare(b.device.name));
}

/** What a script on Updates and the device page is called (api.py outdated_scripts). */
export const SCRIPT_NAMES: Record<string, string> = {
	collector: 'collector',
	actor: 'actor',
	openwrt: 'OpenWrt helpers',
	'vpn-pki': 'OpenVPN PKI script',
	'vpn-crl': 'OpenVPN CRL script'
};
export const scriptName = (n: string) => SCRIPT_NAMES[n] ?? n;

/** What updating a container asks first: a new build of its tag, or another tag (with what to
 * mind: a compose file that names the old tag, a new major version). */
export function containerAsk(c: {
	name: string;
	from: string; // the tag it runs
	to: string; // a tag, or NEWER_BUILD
	compose?: string | null;
}): string {
	if (!tagOf(c.to))
		return `Update ${c.name} to a new build of :${c.from}? The old container returns if startup fails.`;
	const major = (tag: string) => tag.match(/^v?(\d+)(?:[.-]|$)/)?.[1];
	const newMajor =
		major(c.from) !== undefined && major(c.to) !== undefined && major(c.from) !== major(c.to);
	return [
		`Update ${c.name} from ${c.from} to ${c.to}? Settings are kept; the old container returns if startup fails.`,
		newMajor ? 'New major version: check release notes and back up data first.' : '',
		c.compose ? `Change the tag in ${c.compose} too, or Compose will restore ${c.from}.` : ''
	]
		.filter(Boolean)
		.join(' ');
}

/** A new build of the tag a container runs: what the hub says for one (docker.py); WUD says
 * nothing (null) for the same. */
export const NEWER_BUILD = 'newer build';

/** The tag an update goes to, for the actor: none for a new build of the same tag. */
export const tagOf = (to: string | null | undefined) => (to && to !== NEWER_BUILD ? to : undefined);

/** What an update goes to, in words: its tag, or NEWER_BUILD. */
export const updateTarget = (to: string | null | undefined) => tagOf(to) ?? NEWER_BUILD;

/** What "Update all" updates: new builds of the tags they run (another tag is one at a time,
 * asked first), not said to be not needed. */
export const sameTagUpdates = (containers: Container[]) =>
	containers.filter((c) => c.update && !c.unchecked && !c.skipped && !tagOf(c.update_to));
