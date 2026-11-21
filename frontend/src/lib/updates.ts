import type { Device } from '#lib/api.js';

/** Everything with an update waiting, across the network, from what the devices report:
 * containers (registries), system packages (the collector), reboots after updates, OpenWrt
 * firmware and packages, and Kastellan's own scripts on hosts (set up again to renew them). */
export type Update =
	| {
			kind: 'container';
			device: Device;
			name: string;
			from: string;
			to: string;
			within?: string | null;
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
	| { kind: 'router-packages'; device: Device; names: string[] }
	| { kind: 'scripts'; device: Device; names: string[] }
	| { kind: 'abilities'; device: Device; login: 'actor' | 'openwrt'; names: string[] };

export function updatesOf(devices: Device[]): Update[] {
	const out: Update[] = [];
	for (const d of devices) {
		for (const c of d.containers ?? []) {
			if (c.update && !c.unchecked)
				out.push({
					kind: 'container',
					device: d,
					name: c.name,
					from: c.tag,
					to: c.update_to ?? 'newer',
					within: c.update_within ?? null
				});
		}
		const u = d.host_report?.updates;
		if (u?.available)
			out.push({ kind: 'packages', device: d, count: u.available, security: u.security ?? 0 });
		if (u?.reboot) out.push({ kind: 'reboot', device: d, why: u.reboot_for ?? [] });
		const fw = d.router?.firmware;
		if (fw?.next_step)
			out.push({
				kind: 'firmware',
				device: d,
				from: fw.current,
				to: fw.next_step,
				supported: fw.supported
			});
		const pk = d.router?.packages;
		if (pk?.length) out.push({ kind: 'router-packages', device: d, names: pk });
		if (d.outdated_scripts?.length)
			out.push({ kind: 'scripts', device: d, names: d.outdated_scripts });
		for (const login of ['actor', 'openwrt'] as const) {
			const names = d.new_abilities?.[login];
			if (names?.length) out.push({ kind: 'abilities', device: d, login, names });
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
