/** The words for the same things, everywhere: use these in headings, counts and sentences
 * instead of writing a new word for a known thing. Plain, short English; capitalized only at the
 * start of a heading or a state word (states.ts has the state words). */

/** What each word means (for tooltips, help and other agents). One word per thing. */
export const GLOSSARY = {
	device: 'Anything on the network Kastellan watches: a router, an access point, a server, a PC…',
	check:
		'A test Kastellan runs against a device every few seconds: ping, a TCP port, a web address (HTTP) or a DNS name.',
	service:
		'A check other than ping: something a device offers (its web interface, SSH, Jellyfin). The Services page lists them.',
	'own service':
		'A service the device reports itself: an OpenWrt init script, a failed systemd unit.',
	issue:
		'One thing that needs attention: an open alarm, a check that is down, a security update. The overview headline counts them.',
	alarm:
		'An issue Kastellan sends a message about (notify channels). Every alarm is an issue; not every issue is an alarm yet.',
	'needs attention':
		'The list of issues. The headline, the sidebar badge and the area counts all come from it.',
	state: 'What something is now, in one word from the State vocabulary (Online, Running, Failed…).',
	update:
		'Newer software waiting: a container image, system packages, firmware, a reboot after an update, Kastellan’s own scripts.',
	collector: 'Kastellan’s read-only script on a Linux host: vitals, disks, containers, VMs.',
	actor:
		'Kastellan’s script on a host that acts: updates, restarts, reboots (only what the host allows).',
	'access point': 'A device that broadcasts Wi-Fi: the router itself, a repeater, an OpenWrt AP.',
	network: 'A Wi-Fi network: one name (SSID) on one or more access points and bands.',
	client: 'A phone, laptop or other device connected to a Wi-Fi network.',
	'sign-in': 'A login (or a try) on SSH, a VPN, a router’s web interface or Kastellan.',
	backup:
		'A backup job that reports in (restic, borg, a script); a configuration backup is one Kastellan takes of a device itself.'
} as const;

/** The words, as written in the UI. */
export const WORD = {
	attention: 'Needs attention',
	allGood: 'Everything is running',
	notRead: 'Not read',
	setUp: 'Set up',
	signInToManage: 'Sign in to manage',
	adminsOnly: 'Admins only',
	state: 'State', // a table column: never "Status"
	updated: 'Updated'
} as const;

/** "1 device", "3 devices"; `many` for irregular plurals ("1 entry", "2 entries"). */
export function count(n: number, one: string, many = `${one}s`): string {
	return `${n} ${n === 1 ? one : many}`;
}

/** The overview's headline: how many issues need attention, or that all is well. */
export function attentionHeadline(n: number): string {
	return n === 0 ? WORD.allGood : `${n} ${n === 1 ? 'thing needs' : 'things need'} attention`;
}
