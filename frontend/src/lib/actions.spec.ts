import { describe, expect, it } from 'vitest';
import { VM_NAME, containerActions, vmActions } from './actions';

describe('vmActions', () => {
	it('offers only what can work from the state', () => {
		expect(vmActions('shut off')).toEqual(['vm-start']);
		expect(vmActions('running')).toEqual(['vm-shutdown', 'vm-reboot', 'vm-force-off']);
		expect(vmActions('paused')).toEqual(['vm-start', 'vm-force-off']);
		for (const s of ['crashed', 'pmsuspended', 'in shutdown', 'blocked'])
			expect(vmActions(s)).toEqual(['vm-force-off']);
	});
});

describe('containerActions', () => {
	it('starts only what is stopped, restarts and stops what Docker calls running', () => {
		expect(containerActions('exited', false)).toEqual(['start']);
		expect(containerActions('dead', true)).toEqual(['update', 'start']);
		expect(containerActions('running', true)).toEqual(['update', 'restart', 'stop']);
		expect(containerActions('restarting', false)).toEqual(['restart', 'stop']);
		expect(containerActions('paused', false)).toEqual(['restart', 'stop']);
	});
});

describe('VM_NAME', () => {
	it('never takes a number or a UUID (virsh reads those as another domain)', () => {
		expect(VM_NAME.test('win11')).toBe(true);
		expect(VM_NAME.test('deb+test')).toBe(true);
		expect(VM_NAME.test('2')).toBe(false);
		expect(VM_NAME.test('01234567-89ab-cdef-0123-456789ABCDEF')).toBe(false);
		expect(VM_NAME.test('my vm')).toBe(false);
	});
});
