/** Which actions a container or a virtual machine offers in its state: only those that can work
 * from there (the host checks again, but a button that can only fail wastes its lock). */

export type VmAction = 'vm-start' | 'vm-shutdown' | 'vm-reboot' | 'vm-force-off';
export type ContainerAction = 'start' | 'stop' | 'restart' | 'update' | 'rollback';

/** libvirt's states: shut off; running; paused; and states of an active guest that only forcing
 * it off can end (crashed, pmsuspended, in shutdown, blocked, ...). */
export function vmActions(state: string): VmAction[] {
	if (state === 'shut off') return ['vm-start'];
	if (state === 'running') return ['vm-shutdown', 'vm-reboot', 'vm-force-off'];
	if (state === 'paused') return ['vm-start', 'vm-force-off']; // vm-start resumes it
	return ['vm-force-off'];
}

/** Docker's states. A paused or restarting container counts as running for Docker (start
 * refuses it); update works on a stopped one too. */
export function containerActions(
	state: string,
	updatable: boolean,
	previous = false // an image kept from before its last update: it can go back to it
): ContainerAction[] {
	const out: ContainerAction[] = [];
	if (updatable) out.push('update');
	if (previous) out.push('rollback');
	if (['exited', 'created', 'dead'].includes(state)) out.push('start');
	if (['running', 'restarting', 'paused'].includes(state)) out.push('restart', 'stop');
	return out;
}

/** The VM names the actor accepts (as act-common's VM_NAME): libvirt allows more, but never a
 * number or a UUID, which virsh would read as another domain's id. */
export const VM_NAME =
	/^(?!\d+$)(?![0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$)[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$/;
