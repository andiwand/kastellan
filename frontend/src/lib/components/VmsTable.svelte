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
	import Table, { type Column } from './ui/Table.svelte';

	/** A host's virtual machines (libvirt, from its collector): state, CPUs, memory, whether it
	 * starts with the host, the device it is (when known). With `canAct`, a signed-in admin can
	 * start, shut down, restart or force them off on their row through the host's actor (only what
	 * the host allows), through the update queue. The same table on /containers?tab=vms (one per
	 * host), the host's page and the VM's own page. Phones keep the VM, its state and its actions. */
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
	const askingOf = (vm: Vm) => actions(vm).find((a) => asking === keyOf(job(vm, a.kind)));
	const hasActions = (vm: Vm) => canAct && actions(vm).length > 0;

	const columns: Column<Vm>[] = $derived([
		{ key: 'name', label: 'Virtual machine', grow: true, cell: nameCell },
		{ key: 'state', label: 'State', cell: stateCell, width: '8rem' },
		{ key: 'vcpus', label: 'vCPUs', priority: 2, numeric: true, value: (vm) => vm.vcpus },
		{ key: 'memory', label: 'Memory', priority: 2, numeric: true, value: (vm) => bytes(vm.memory) },
		...(canAct
			? [
					{
						key: 'actions',
						label: '',
						priority: 3,
						align: 'right',
						cell: actionsCell
					} satisfies Column<Vm>
				]
			: [])
	]);
</script>

{#snippet nameCell(vm: Vm)}
	<span class="block truncate font-medium">{vm.name}</span>
	{#if vm.autostart || (vm.device && store.byId.get(vm.device))}
		<span class="flex min-w-0 items-center gap-1.5 text-meta text-muted">
			{#if vm.device && store.byId.get(vm.device)}
				{@const linked = store.byId.get(vm.device)!}
				<a href="/devices/{linked.id}" class="flex min-w-0 items-center gap-1.5 hover:text-text">
					<StatusDot status={statusOf(linked)} size="size-1.5" />
					<span class="truncate">{linked.name}</span>
				</a>
				{#if vm.autostart}·{/if}
			{/if}
			{#if vm.autostart}<span class="truncate">starts with the host</span>{/if}
		</span>
	{/if}
	{#if canAct && !VM_NAME.test(vm.name)}<span
			class="block truncate text-meta text-muted"
			title="Kastellan acts on names of letters, digits and _ . + -, never a number or a UUID"
		>
			shown only: Kastellan can't act on this name
		</span>{/if}
{/snippet}

{#snippet stateCell(vm: Vm)}
	<StateBadge state={vmState(vm.state, vm.autostart)} />
{/snippet}

{#snippet buttons(vm: Vm)}
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
{/snippet}

{#snippet actionsCell(vm: Vm)}
	<span class="inline-flex justify-end gap-1">{@render buttons(vm)}</span>
{/snippet}

<!-- Under the row: the actions on narrower screens, the question before one runs, its progress. -->
{#snippet belowRow(vm: Vm)}
	{@const a = askingOf(vm)}
	{@const last = lastOf(vm)}
	{#if hasActions(vm)}
		<div class="flex flex-wrap justify-end gap-1 pb-3 lg:hidden">{@render buttons(vm)}</div>
	{/if}
	{#if a}
		<p class="flex flex-wrap items-center gap-2 pb-3 text-meta">
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
	{#if last}
		<div class="pb-3"><JobStatus job={last.state} dismiss={() => runner.dismiss(last.job)} /></div>
	{/if}
{/snippet}

<Table
	{columns}
	rows={sorted}
	key={(vm) => vm.name}
	below={belowRow}
	hasBelow={(vm) => hasActions(vm) || !!askingOf(vm) || !!lastOf(vm)}
	label="Virtual machines"
	empty="No virtual machines."
/>
