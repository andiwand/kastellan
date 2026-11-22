<script lang="ts">
	import type { Vm } from '#lib/api.js';
	import { bytes } from '#lib/format.js';
	import { runner, type Job } from '#lib/runner.svelte.js';
	import JobStatus from './JobStatus.svelte';
	import { VM_NAME, vmActions, type VmAction } from '#lib/actions.js';
	import { vmState } from '#lib/states.js';
	import StateBadge from './ui/StateBadge.svelte';
	import { store } from '#lib/state.svelte.js';
	import { statusOf } from '#lib/status.js';
	import StatusDot from './StatusDot.svelte';

	/** A host's virtual machines (libvirt, from its collector): state, CPUs, memory, whether it
	 * starts with the host. With `canAct`, a signed-in admin can start, shut down, restart or force
	 * them off through the host's actor (only what the host allows), through the update queue. */
	let { device, vms, canAct = false }: { device: string; vms: Vm[]; canAct?: boolean } = $props();

	type Kind = VmAction;
	let asking = $state<string | null>(null); // a job's key: asked once before it runs

	const job = (vm: Vm, kind: Kind): Job => ({ device, kind, name: vm.name });
	const lastOf = (vm: Vm) =>
		runner.latest(
			KINDS.map((kind) => job(vm, kind)),
			store.now
		);
	const keyOf = (j: Job) => `${j.kind}:${'name' in j ? j.name : ''}`;
	const busy = (j: Job) => ['queued', 'running'].includes(runner.state(j)?.state ?? '');
	const KINDS: Kind[] = ['vm-start', 'vm-shutdown', 'vm-reboot', 'vm-force-off'];

	/** What can be done to it now (only what can work from its state), each with its button and
	 * its question. A name the actor can't take (a space, a number) gets none. */
	function actions(vm: Vm): { kind: Kind; label: string; ask: string; strong?: boolean }[] {
		if (!VM_NAME.test(vm.name)) return [];
		const paused = vm.state === 'paused';
		const all: Record<Kind, { label: string; ask: string; strong?: boolean }> = {
			'vm-start': {
				label: paused ? 'Resume' : 'Start',
				ask: paused ? `Resume ${vm.name}?` : `Start ${vm.name}?`
			},
			'vm-shutdown': {
				label: 'Shut down',
				ask: `Shut ${vm.name} down? Its system is asked to, as with the power button.`
			},
			'vm-reboot': {
				label: 'Restart',
				ask: `Ask ${vm.name} to restart? Its system is asked to (libvirt can't confirm it did).`
			},
			'vm-force-off': {
				label: 'Force off',
				ask: `Force ${vm.name} off? Like pulling the plug: what isn't saved in it is lost, and its disks may need checking. Only for a guest that hangs.`,
				strong: true
			}
		};
		return vmActions(vm.state).map((kind) => ({ kind, ...all[kind] }));
	}

	const sorted = $derived([...vms].sort((a, b) => a.name.localeCompare(b.name)));
</script>

<div class="card overflow-hidden">
	<div
		class="hidden grid-cols-[minmax(0,1.4fr)_8rem_5rem_6rem_minmax(0,1fr)] gap-4 border-b border-line px-5 py-2.5 text-xs font-medium text-muted md:grid"
	>
		<span>Virtual machine</span><span>State</span><span>vCPUs</span><span>Memory</span><span></span>
	</div>
	<ul class="divide-y divide-line">
		{#each sorted as vm (vm.name)}
			<li
				class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-5 py-3 md:grid-cols-[minmax(0,1.4fr)_8rem_5rem_6rem_minmax(0,1fr)]"
			>
				<p class="min-w-0 truncate text-sm font-medium">
					{vm.name}
					{#if vm.autostart}<span class="ml-1 text-xs font-normal text-faint">
							starts with the host
						</span>{/if}
					{#if vm.device && store.byId.get(vm.device)}
						{@const linked = store.byId.get(vm.device)!}
						<a
							href="/devices/{linked.id}"
							class="mt-0.5 flex items-center gap-1.5 text-xs font-normal text-muted hover:text-text"
						>
							<StatusDot status={statusOf(linked)} size="size-1.5" />
							{linked.name}
						</a>
					{/if}
					{#if canAct && !VM_NAME.test(vm.name)}<span
							class="block text-xs font-normal text-faint"
							title="Kastellan acts on names of letters, digits and _ . + -, never a number or a UUID"
						>
							shown only: Kastellan can't act on this name
						</span>{/if}
				</p>
				<span><StateBadge state={vmState(vm.state, vm.autostart)} /></span>
				<span class="hidden text-xs text-muted md:block">{vm.vcpus ?? '–'}</span>
				<span class="hidden text-xs text-muted md:block">{bytes(vm.memory)}</span>
				<span class="col-span-full flex justify-end gap-1 md:col-span-1">
					{#if canAct}
						{#each actions(vm) as a (a.kind)}
							<button
								class="btn btn-sm {a.strong ? '' : 'text-muted'}"
								style:color={a.strong ? 'var(--critical-text)' : undefined}
								disabled={busy(job(vm, a.kind))}
								onclick={() => (asking = keyOf(job(vm, a.kind)))}
							>
								{a.label}
							</button>
						{/each}
					{/if}
				</span>
				{#each actions(vm) as a (a.kind)}
					{#if asking === keyOf(job(vm, a.kind))}
						<p class="col-span-full flex flex-wrap items-center gap-2 text-xs">
							{a.ask}
							<button
								class="btn btn-sm {a.strong ? '' : 'btn-primary'}"
								style:color={a.strong ? 'var(--critical-text)' : undefined}
								onclick={() => {
									runner.add([job(vm, a.kind)]);
									asking = null;
								}}
							>
								{a.label}
							</button>
							<button class="btn btn-sm" onclick={() => (asking = null)}>Not now</button>
						</p>
					{/if}
				{/each}
				{#if lastOf(vm)}
					{@const last = lastOf(vm)!}
					<div class="col-span-full">
						<JobStatus job={last.state} dismiss={() => runner.dismiss(last.job)} />
					</div>{/if}
			</li>
		{/each}
	</ul>
</div>
