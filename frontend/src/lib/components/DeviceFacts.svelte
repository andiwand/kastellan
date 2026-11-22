<script lang="ts">
	import type { Device } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago, duration } from '#lib/format.js';
	import { statusTone } from '#lib/states.js';
	import { upsLabel, upsStatus } from '#lib/status.js';
	import { readable, sourcesOf, systemOf, unreadOf, uptimeOf } from '#lib/devicePage.js';
	import KeyValues, { type KeyValue } from './ui/KeyValues.svelte';
	import StateDot from './ui/StateDot.svelte';
	import ReadAgain from './ReadAgain.svelte';
	import Copy from '@lucide/svelte/icons/copy';
	import Check from '@lucide/svelte/icons/check';

	/** A device's facts in fixed places: what it is (kind, model, firmware or kernel), where (its
	 * address, what it is connected through, its site), how long it has been up, its power, and
	 * where its readings come from and how fresh they are. A reading that could be set up but
	 * isn't says so once ("Not read · Set up"); a printer or camera, only checked, says so. */
	let { device }: { device: Device } = $props();

	const KIND: Record<string, string> = {
		router: 'Router',
		'access-point': 'Access point',
		switch: 'Switch',
		server: 'Server',
		pc: 'PC',
		laptop: 'Laptop',
		printer: 'Printer',
		camera: 'Camera',
		ups: 'UPS',
		other: 'Device'
	};
	const admin = $derived(store.me?.role === 'admin');
	const parent = $derived(device.parent ? store.byId.get(device.parent) : undefined);
	const sources = $derived(sourcesOf(device));
	const unread = $derived(unreadOf(device));
	const uptime = $derived(uptimeOf(device));
	const system = $derived(systemOf(device));
	const nameOf = (id: string) => store.byId.get(id)?.name ?? id;
	// The UPS it is powered by is said once; one it only reads, apart.
	const reads = $derived(
		(device.reads_ups ?? []).filter((r) => r.device !== device.powered_by?.ups)
	);

	let copied = $state(false);
	async function copyHost() {
		await navigator.clipboard.writeText(device.host);
		copied = true;
		setTimeout(() => (copied = false), 1500);
	}

	const items = $derived.by((): KeyValue[] => {
		const list: KeyValue[] = [{ label: 'Kind', value: KIND[device.kind] ?? device.kind }];
		if (device.info?.model) list.push({ label: 'Model', value: device.info.model });
		if (system) list.push({ label: system.label, value: system.value, mono: true });
		if (device.host) list.push({ label: 'Address', content: address });
		if (uptime != null) list.push({ label: 'Up for', value: duration(uptime) });
		if (parent)
			list.push({ label: 'Connected through', value: parent.name, href: `/devices/${parent.id}` });
		if (device.site && device.site !== 'home')
			list.push({ label: 'Site', value: device.site_name });
		if (device.runs_on) list.push({ label: 'Runs on', content: runsOn });
		if (device.powered_by) list.push({ label: 'Power', content: power });
		if (reads.length) list.push({ label: 'Reads', content: readsUps });
		if (sources.length) list.push({ label: 'Readings', content: readings });
		else if (readable(device))
			list.push({
				label: 'Readings',
				value: null,
				setup: admin ? `/devices/${encodeURIComponent(device.id)}?tab=setup` : undefined
			});
		else list.push({ label: 'Readings', value: 'Its checks only' });
		if (device.tags.length) list.push({ label: 'Tags', content: tags });
		return list;
	});
</script>

{#snippet address()}
	<button
		onclick={copyHost}
		class="inline-flex items-center gap-1.5 font-mono hover:text-accent"
		title="Copy the address"
	>
		{device.host}
		{#if copied}<Check class="size-3.5 text-muted" />{:else}<Copy
				class="size-3.5 text-faint"
			/>{/if}
	</button>
	{#if device.address && device.address !== device.host}
		<span class="block font-mono text-meta text-faint">{device.address}</span>
	{/if}
{/snippet}

{#snippet upsLine(id: string, state: string)}
	<span class="inline-flex items-center gap-1.5">
		<a class="hover:underline" href="/devices/{id}">{nameOf(id)}</a>
		<span class="text-faint">·</span>
		<StateDot tone={statusTone[upsStatus[state] ?? 'waiting']} label={upsLabel[state] ?? state} />
		{upsLabel[state] ?? state}
	</span>
{/snippet}

{#snippet power()}
	{#if device.powered_by}{@render upsLine(device.powered_by.ups, device.powered_by.state)}{/if}
{/snippet}

{#snippet readsUps()}
	{#each reads as r (r.device)}
		<span class="block">{@render upsLine(r.device, r.state)}</span>
	{/each}
{/snippet}

{#snippet runsOn()}
	{#if device.runs_on}
		<a class="hover:underline" href="/devices/{device.runs_on.host}">
			{nameOf(device.runs_on.host)}
		</a>
		<span class="block text-meta text-faint">
			virtual machine {device.runs_on.vm} · {device.runs_on.state || 'not reported'}
		</span>
	{/if}
{/snippet}

{#snippet readings()}
	{#each sources as s (s.name)}
		<span class="flex items-center justify-end gap-1.5">
			{#if s.error}
				<StateDot tone="warning" label="Not read" />
				<span title={s.error}>{s.name}: {s.error}</span>
			{:else}
				<span>{s.name}{s.at ? ` · ${ago(s.at, store.now)}` : ''}</span>
			{/if}
		</span>
		{#if s.collector}<ReadAgain device={device.id} />{/if}
	{/each}
	{#if unread.length}
		<span class="block text-meta text-faint">couldn't read {unread.join('; ')}</span>
	{/if}
{/snippet}

{#snippet tags()}
	<span class="inline-flex flex-wrap justify-end gap-1">
		{#each device.tags as tag (tag)}
			<span class="rounded-md bg-surface-2 px-1.5 py-0.5 text-meta">{tag}</span>
		{/each}
	</span>
{/snippet}

<KeyValues {items} columns={3} />
