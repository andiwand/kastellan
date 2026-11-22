import { expect, it } from 'vitest';
import {
	backupState,
	containerState,
	deviceState,
	signinState,
	vmState,
	vpnServerState,
	worst
} from './states';

it('says every state in one capitalized word with one tone', () => {
	expect(deviceState('down')).toEqual({ word: 'Down', tone: 'critical' });
	expect(deviceState('offline')).toEqual({ word: 'Offline', tone: 'neutral' });
	expect(containerState('running')).toEqual({ word: 'Running', tone: 'good' });
	expect(containerState('exited')).toEqual({ word: 'Exited', tone: 'neutral' }); // off by choice
	expect(containerState('dead').tone).toBe('critical');
	expect(backupState.failed).toEqual({ word: 'Failed', tone: 'critical' });
	expect(signinState.attempt.word).toBe('Broken off');
});

it('judges a VM by whether it is meant to run', () => {
	expect(vmState('shut off')).toEqual({ word: 'Shut off', tone: 'neutral' });
	expect(vmState('shut off', true).tone).toBe('warning');
	expect(vmState('crashed').tone).toBe('critical');
	expect(vmState('').word).toBe('Unknown');
});

it("tells a VPN server that runs from one whose state is old or can't be read", () => {
	expect(vpnServerState({ running: true })).toEqual({ word: 'Running', tone: 'good' });
	expect(vpnServerState({ running: true, stale: true }).tone).toBe('warning');
	expect(vpnServerState({ running: false, state: 'exited' })).toEqual({
		word: 'Exited',
		tone: 'critical'
	});
	expect(vpnServerState({ running: null, error: 'timeout' })).toMatchObject({ word: 'Not read' });
});

it('picks the worst tone', () => {
	expect(worst([])).toBe('good');
	expect(worst(['good', 'warning', 'neutral'])).toBe('warning');
	expect(worst(['warning', 'critical'])).toBe('critical');
});
