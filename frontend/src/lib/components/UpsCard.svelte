<script lang="ts">
	import { api, type Device } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago, duration, stamp } from '#lib/format.js';
	import { statusOf, upsLabel, upsStatus } from '#lib/status.js';
	import { statusTone, toneColor } from '#lib/states.js';
	import StateBadge from './ui/StateBadge.svelte';
	import StatusDot from './StatusDot.svelte';

	/** A UPS's page: how it is (as its host reads it), and the devices it powers, which an admin
	 * can change here (each device's powered_by in config.yaml). */
	let { device, canChange = false }: { device: Device; canChange?: boolean } = $props();

	const u = $derived(device.ups);
	const now = $derived(u?.state ?? '');
	const via = $derived(
		(store.devices ?? []).find((d) => d.reads_ups?.some((r) => r.device === device.id))
	);
	const powers = $derived((device.powers ?? []).map((id) => store.byId.get(id)).filter((d) => !!d));

	let editing = $state(false);
	let chosen = $state<string[]>([]);
	let version = $state<string | null>(null);
	let error = $state<string | null>(null);
	let busy = $state(false);
	async function edit() {
		error = null;
		chosen = [...(device.powers ?? [])];
		editing = true;
		try {
			version = (await api.deviceForm(device.id)).version ?? null;
		} catch (e) {
			error = (e as Error).message;
		}
	}
	async function save() {
		if (!version) return;
		busy = true;
		error = null;
		try {
			await api.setPowered(device.id, chosen, version);
			await store.refresh();
			editing = false;
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}
	// A full battery on mains is calm (as healthy cells in uptime strips); trouble takes its tone.
	const barFill = $derived.by(() => {
		const t = statusTone[upsStatus[now] ?? 'waiting'];
		return t === 'good' ? 'var(--good-soft)' : toneColor[t].fill;
	});
	const others = $derived((store.devices ?? []).filter((d) => d.id !== device.id));
	const row = 'flex justify-between gap-4 py-1.5';
</script>

<div class="card mt-6 p-5">
	<div class="flex flex-wrap items-baseline justify-between gap-2">
		<p class="flex items-center gap-2">
			<StateBadge word={upsLabel[now] ?? now} tone={statusTone[upsStatus[now] ?? 'waiting']} />
			{#if u?.flags.includes('replace_battery') && now !== 'replace_battery'}
				<StateBadge word={upsLabel.replace_battery} tone="warning" />
			{/if}
		</p>
		<p class="text-xs text-muted">
			{#if via}read by <a class="hover:underline" href="/devices/{via.id}">{via.name}</a>{/if}
			{#if u?.checked}· {ago(u.checked, store.now)}{/if}
		</p>
	</div>
	{#if u}
		{#if u.charge != null}
			<div class="mt-4">
				<div class="flex justify-between text-xs text-muted">
					<span>Charge</span><span class="tabular text-text">{Math.round(u.charge)} %</span>
				</div>
				<div class="mt-1 h-2 overflow-hidden rounded-full bg-surface-2">
					<div
						class="h-full rounded-full"
						style:width="{u.charge}%"
						style:background={barFill}
					></div>
				</div>
			</div>
		{/if}
		<div class="mt-3 grid gap-x-8 text-sm sm:grid-cols-2">
			<div class="divide-y divide-line">
				{#if u.runtime != null}<p class={row}>
						<span class="text-muted">Runtime left</span><span>{duration(u.runtime)}</span>
					</p>{/if}
				{#if u.load != null}<p class={row}>
						<span class="text-muted">Load</span><span>
							{Math.round(u.load)} %{#if u.nominal_power}
								<span class="text-faint">
									· ~{Math.round((u.load / 100) * u.nominal_power)} W of {u.nominal_power}
								</span>{/if}
						</span>
					</p>{/if}
				{#if u.input_voltage != null}<p class={row}>
						<span class="text-muted">Input</span><span>{u.input_voltage} V</span>
					</p>{/if}
				{#if u.battery_voltage != null}<p class={row}>
						<span class="text-muted">Battery</span><span>{u.battery_voltage} V</span>
					</p>{/if}
			</div>
			<div class="divide-y divide-line">
				{#if u.last_transfer}<p class={row}>
						<span class="text-muted">Last on battery</span>
						<span class="text-right">
							{u.on_battery_since ? stamp(u.on_battery_since) : ''}<span
								class="block text-xs text-muted"
							>
								{u.last_transfer}
							</span>
						</span>
					</p>{/if}
				{#if u.transfers != null}<p class={row}>
						<span class="text-muted">Times on battery</span><span>{u.transfers}</span>
					</p>{/if}
				{#if u.battery_date}<p class={row}>
						<span class="text-muted">Battery from</span><span>{u.battery_date}</span>
					</p>{/if}
				{#if u.model || u.serial}<p class={row}>
						<span class="text-muted">Model</span>
						<span class="text-right">
							{u.model ?? ''}<span class="block font-mono text-xs text-muted">
								{u.serial ?? ''}{u.firmware ? ` · ${u.firmware}` : ''}
							</span>
						</span>
					</p>{/if}
			</div>
		</div>
	{:else}
		<p class="mt-3 text-sm text-muted">
			Nothing read yet: its host's collector reports it (apcupsd or NUT on that host).
		</p>
	{/if}
</div>

<div class="mt-8 mb-3 flex items-center justify-between gap-2">
	<h2 class="text-sm font-semibold">
		Powers <span class="tabular ml-1 font-normal text-faint">{powers.length}</span>
	</h2>
	{#if canChange && !editing}<button class="btn btn-sm" onclick={edit}>Change</button>{/if}
</div>
{#if editing}
	<div class="card p-4">
		<p class="text-xs text-muted">What runs on this UPS (its powered_by in config.yaml).</p>
		<div class="mt-3 grid gap-x-6 gap-y-1.5 sm:grid-cols-2 lg:grid-cols-3">
			{#each others as d (d.id)}
				<label class="choice text-sm">
					<input type="checkbox" value={d.id} bind:group={chosen} />
					<span class="truncate">{d.name}</span>
				</label>
			{/each}
		</div>
		{#if error}<p class="mt-2 text-xs" style:color="var(--critical-text)">{error}</p>{/if}
		<div class="mt-3 flex gap-2">
			<button class="btn btn-sm btn-primary" disabled={busy || !version} onclick={save}>
				Save
			</button>
			<button class="btn btn-sm" onclick={() => (editing = false)}>Cancel</button>
		</div>
	</div>
{:else if powers.length}
	<div class="card divide-y divide-line">
		{#each powers as d (d!.id)}
			<a
				href="/devices/{d!.id}"
				class="flex items-center gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-surface-2/70"
			>
				<StatusDot status={statusOf(d!)} size="size-2" />
				<span class="flex-1 truncate">{d!.name}</span>
			</a>
		{/each}
	</div>
{:else}
	<p class="card p-4 text-sm text-muted">
		No devices on it yet{canChange ? ': Change says which run on it.' : '.'}
	</p>
{/if}
