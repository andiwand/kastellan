<script lang="ts">
	import { api } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { dayClock } from '#lib/format.js';
	import Wrench from '@lucide/svelte/icons/wrench';

	/** Maintenance for a device or a group from here: problems keep showing, marked, but are
	 * only sent if still there when it ends (maintenance.py). Planned windows are in Settings. */
	let { kind, id, devices }: { kind: 'devices' | 'groups'; id: string; devices: string[] } =
		$props();

	const on = $derived(devices.map((d) => store.alarms?.maintenance?.[d]).filter((m) => !!m));
	// Ended here only when started here (a planned window ends by itself).
	const fromHere = $derived(on.some((m) => !m.planned));
	const until = $derived(
		on.length ? on.map((m) => m.until).reduce((a, b) => (a > b ? a : b)) : null
	);
	let open = $state(false);
	let reason = $state('');
	let error = $state<string | null>(null);

	async function set(minutes: number) {
		error = null;
		try {
			await api.maintenance(kind, id, minutes, reason.trim());
			open = false;
			reason = '';
			await store.refreshHistory();
		} catch (e) {
			error = (e as Error).message;
		}
	}
	const button =
		'inline-flex h-9 items-center gap-2 rounded-xl border border-line bg-surface px-3.5 text-sm font-medium shadow-card transition-colors hover:border-line-strong';
</script>

<div class="relative">
	{#if fromHere}
		<button
			class={button}
			onclick={() => set(0)}
			title="Until {until ? dayClock(until) : ''}: {on.map((m) => m.reason).join(', ')}"
		>
			<Wrench class="size-4 text-muted" /> End maintenance
		</button>
	{:else}
		<button class={button} onclick={() => (open = !open)} aria-expanded={open}>
			<Wrench class="size-4 text-muted" /> Maintenance
		</button>
	{/if}
	{#if open}
		<div class="card absolute right-0 z-20 mt-2 w-72 p-4 shadow-pop">
			<p class="text-xs leading-relaxed text-muted">
				Problems keep showing, marked; they're sent only if still there when it ends.
			</p>
			<label class="field mt-3">
				Why (optional)
				<input class="input" bind:value={reason} maxlength="200" placeholder="Disk swap" />
			</label>
			<div class="mt-3 flex flex-wrap gap-2">
				{#each [[30, '30 min'], [60, '1 h'], [120, '2 h'], [240, '4 h'], [1440, '1 day']] as [m, label] (m)}
					<button class="btn btn-sm" onclick={() => set(m as number)}>{label}</button>
				{/each}
			</div>
		</div>
	{/if}
	{#if error}<p class="mt-2 text-xs" style:color="var(--critical-text)">{error}</p>{/if}
</div>
