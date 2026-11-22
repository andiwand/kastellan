import type { Backup, Container, SignIn, VpnServer } from './api';
import { statusLabel, type Status } from './status';

/** The one vocabulary of states: every state Kastellan shows is a word from here with one tone,
 * shown by the State badge (ui/StateBadge.svelte) or, where there is no room, its dot. Status
 * colour lives only in the badge, dots, strips, bars and chart marks; never in running text. */

/** good: works as it should. warning: worth a look. critical: broken, act now. neutral: off,
 * stopped by choice, not known yet. accent: something new is available (never a problem). */
export type Tone = 'good' | 'warning' | 'critical' | 'neutral' | 'accent';

/** A state: its word (capitalized) and tone; `title` says more on hover. */
export interface State {
	word: string;
	tone: Tone;
	title?: string;
}

/** Each tone's fill (dots, bars, marks) and text colour (the badge's word). */
export const toneColor: Record<Tone, { fill: string; text: string }> = {
	good: { fill: 'var(--good)', text: 'var(--good-text)' },
	warning: { fill: 'var(--warning)', text: 'var(--warning-text)' },
	critical: { fill: 'var(--critical)', text: 'var(--critical-text)' },
	neutral: { fill: 'var(--offline)', text: 'var(--muted)' },
	accent: { fill: 'var(--accent)', text: 'var(--accent)' }
};

/** Worst first: to sort, or to pick the worst of several. */
export const toneRank: Record<Tone, number> = {
	critical: 0,
	warning: 1,
	accent: 2,
	neutral: 3,
	good: 4
};

/** The worst of several tones (good when there are none). */
export const worst = (tones: Tone[]): Tone =>
	tones.reduce<Tone>((w, t) => (toneRank[t] < toneRank[w] ? t : w), 'good');

/** A device's or a check's status (status.ts) as a state. */
export const statusTone: Record<Status, Tone> = {
	up: 'good',
	degraded: 'warning',
	down: 'critical',
	offline: 'neutral',
	waiting: 'neutral'
};
export const deviceState = (s: Status): State => ({ word: statusLabel[s], tone: statusTone[s] });

/** Docker's states. A stopped container is off by choice (neutral) unless it died. */
export function containerState(state: Container['state']): State {
	switch (state) {
		case 'running':
			return { word: 'Running', tone: 'good' };
		case 'restarting':
			return { word: 'Restarting', tone: 'warning' };
		case 'paused':
			return { word: 'Paused', tone: 'warning' };
		case 'exited':
			return { word: 'Exited', tone: 'neutral' };
		case 'created':
			return { word: 'Created', tone: 'neutral' };
		case 'dead':
			return { word: 'Dead', tone: 'critical' };
		case 'removing':
			return { word: 'Removing', tone: 'neutral' };
		default:
			return { word: state ? capitalize(state) : 'Unknown', tone: 'neutral' };
	}
}

/** libvirt's states. A VM set to start with the host is meant to run: off, it is worth a look. */
export function vmState(state: string, autostart = false): State {
	switch (state) {
		case 'running':
			return { word: 'Running', tone: 'good' };
		case 'paused':
			return { word: 'Paused', tone: 'warning' };
		case 'in shutdown':
			return { word: 'Shutting down', tone: 'warning' };
		case 'shut off':
			return { word: 'Shut off', tone: autostart ? 'warning' : 'neutral' };
		case 'crashed':
			return { word: 'Crashed', tone: 'critical' };
		case 'pmsuspended':
			return { word: 'Suspended', tone: 'neutral' };
		case '':
			return { word: 'Unknown', tone: 'neutral' };
		default:
			return { word: capitalize(state), tone: 'warning' };
	}
}

/** A VPN server: runs, stopped, or Kastellan couldn't read it (lately). */
export function vpnServerState(s: Pick<VpnServer, 'running' | 'stale' | 'state' | 'error'>): State {
	if (s.error) return { word: 'Not read', tone: 'warning', title: s.error };
	if (s.running === true && s.stale)
		return { word: 'Running', tone: 'warning', title: 'Its last reading is old' };
	if (s.running === true) return { word: 'Running', tone: 'good' };
	if (s.running === false)
		return {
			word: s.state && s.state !== 'running' ? capitalize(s.state) : 'Stopped',
			tone: 'critical'
		};
	return { word: 'Unknown', tone: 'neutral' };
}

/** A backup job that reports in (backups.py). */
export const backupState: Record<Backup['state'], State> = {
	ok: { word: 'OK', tone: 'good' },
	failed: { word: 'Failed', tone: 'critical' },
	late: { word: 'Late', tone: 'warning', title: 'It hasn’t reported in as often as expected' },
	waiting: { word: 'Waiting', tone: 'neutral', title: 'Waiting for its first report' }
};

/** A device configuration backup Kastellan takes itself (devbackups.py). */
export function configBackupState(status: { error?: string | null; denied?: boolean }): State {
	if (status.error && status.denied)
		return { word: 'Not allowed', tone: 'neutral', title: status.error };
	if (status.error) return { word: 'Failed', tone: 'warning', title: status.error };
	return { word: 'OK', tone: 'good' };
}

/** One sign-in's outcome. A failure is worth a look, not a breakage; "broken off" is a
 * connection that ended before a login (a scanner, usually). */
export const signinState: Record<SignIn['outcome'], State> = {
	ok: { word: 'Signed in', tone: 'good' },
	failed: { word: 'Failed', tone: 'warning' },
	attempt: { word: 'Broken off', tone: 'neutral', title: 'The connection ended before a login' }
};

/** Why an address on Sign-ins is worth a look (signins.py flags). */
export const signinFlag: Record<string, State> = {
	'new-outside': {
		word: 'New from outside',
		tone: 'critical',
		title: 'A success from an outside address that never succeeded before this week'
	},
	'outside-ok': {
		word: 'Signed in from outside',
		tone: 'warning',
		title: 'A success from a public address'
	},
	repeated: {
		word: 'Many failures',
		tone: 'warning',
		title: 'Ten or more failed or broken-off tries: a password guesser or a scanner'
	}
};

/** A disk's health (SMART) and a RAID array's. */
export const diskState = (passed: boolean | null | undefined, wear = 0): State =>
	passed === false
		? { word: 'Failing', tone: 'critical', title: 'It fails its health check' }
		: wear > 0
			? { word: 'Wear', tone: 'warning', title: `${wear} bad sectors or errors` }
			: passed == null
				? { word: 'Not read', tone: 'neutral' }
				: { word: 'Healthy', tone: 'good' };
export const raidState = (degraded: boolean): State =>
	degraded ? { word: 'Degraded', tone: 'critical' } : { word: 'Healthy', tone: 'good' };

/** The internet line (internet.py). */
export const internetState: Record<'up' | 'degraded' | 'down' | 'unknown', State> = {
	up: { word: 'Online', tone: 'good' },
	degraded: { word: 'Slow', tone: 'warning' },
	down: { word: 'Down', tone: 'critical' },
	unknown: { word: 'Not read', tone: 'neutral' }
};

/** A piece of software: up to date, an update waiting, a security update, out of support. */
export const updateState = {
	current: { word: 'Up to date', tone: 'good' },
	available: { word: 'Update available', tone: 'accent' },
	security: { word: 'Security update', tone: 'warning' },
	unsupported: {
		word: 'Out of support',
		tone: 'critical',
		title: 'It gets no more security fixes'
	},
	unknown: { word: 'Can’t be checked', tone: 'neutral' }
} satisfies Record<string, State>;

/** A certificate Kastellan watches. */
export const certState = {
	valid: { word: 'Valid', tone: 'good' },
	ending: { word: 'Ends soon', tone: 'warning' },
	invalid: { word: 'Not valid', tone: 'critical' }
} satisfies Record<string, State>;

function capitalize(s: string) {
	return s.charAt(0).toUpperCase() + s.slice(1);
}
