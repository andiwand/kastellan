<script lang="ts">
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import VmsTable from '#lib/components/VmsTable.svelte';
	import ReadAgain from '#lib/components/ReadAgain.svelte';
	import KindIcon from '#lib/components/KindIcon.svelte';

	/** Every virtual machine on every host (libvirt/KVM, through the host's collector), grouped
	 * by host; started, shut down, restarted or forced off where the host's actor allows it. */
	const admin = $derived(store.me?.role === 'admin' && !!store.me.user);
	const hosts = $derived((store.devices ?? []).filter((d) => d.vms?.length));
	const all = $derived(hosts.flatMap((d) => d.vms ?? []));
	const running = $derived(all.filter((v) => v.state === 'running').length);
</script>

<svelte:head><title>Virtual machines · Kastellan</title></svelte:head>

<h1 class="text-2xl font-semibold tracking-tight">Virtual machines</h1>
<p class="mt-1 max-w-2xl text-sm text-muted">
	The virtual machines on every host (libvirt, KVM), as each host's collector reports them.
	Kastellan acts only where a host's actor allows it.
</p>

{#if store.devices === null}
	<div class="card mt-6 h-48 animate-pulse"></div>
{:else if hosts.length === 0}
	<div class="card mt-6 px-5 py-8 text-sm text-muted">
		No virtual machines known yet. Kastellan sees a host's VMs through its collector (set it up on
		the host's page); the host needs libvirt (virsh).
	</div>
{:else}
	<p class="mt-6 text-sm">
		<b>{all.length}</b>
		{all.length === 1 ? 'virtual machine' : 'virtual machines'} on {hosts.length}
		{hosts.length === 1 ? 'host' : 'hosts'}, {running} running.
	</p>
	{#each hosts as d (d.id)}
		<div class="mt-8 mb-3 flex flex-wrap items-baseline justify-between gap-2">
			<a href="/devices/{d.id}" class="flex items-center gap-2 text-sm font-semibold">
				<KindIcon kind={d.kind} class="size-4 text-muted" />
				<span class="hover:underline">{d.name}</span>
			</a>
			{#if d.host_checked}<p class="text-xs text-muted">
					from its collector, {ago(d.host_checked, store.now)} · <ReadAgain device={d.id} />
				</p>{/if}
		</div>
		<VmsTable device={d.id} vms={d.vms ?? []} canAct={!!d.act && admin} />
	{/each}
{/if}
