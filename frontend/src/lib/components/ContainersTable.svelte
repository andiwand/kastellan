<script lang="ts">
	import type { Container } from '#lib/api.js';
	import ArrowUpCircle from '@lucide/svelte/icons/circle-arrow-up';
	import CircleHelp from '@lucide/svelte/icons/circle-help';
	import Check from '@lucide/svelte/icons/check';

	/** A host's containers: what runs, and whether a newer image exists (or why that's unknown).
	 * With `act`, a signed-in admin can update or restart one through the host's actor (which
	 * acts only on what the host allows). */
	let {
		containers,
		stale = false,
		act = null
	}: {
		containers: Container[];
		stale?: boolean;
		act?:
			((ability: 'update-container' | 'restart-container', name: string) => Promise<string>) | null;
	} = $props();

	let busy = $state<string | null>(null);
	let asking = $state<string | null>(null); // `${ability} ${name}`: asked once before it acts
	let notes = $state<Record<string, { ok: boolean; text: string }>>({});
	async function run(ability: 'update-container' | 'restart-container', name: string) {
		if (!act) return;
		asking = null;
		busy = name;
		try {
			notes[name] = { ok: true, text: await act(ability, name) };
		} catch (e) {
			notes[name] = { ok: false, text: (e as Error).message };
		} finally {
			busy = null;
		}
	}

	const order = (c: Container) => (c.update ? 0 : c.unchecked ? 1 : 2);
	const sorted = $derived(
		[...containers].sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name))
	);
</script>

<div class="card overflow-hidden" class:opacity-60={stale}>
	<div
		class="hidden grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_6rem_minmax(0,1.2fr)] gap-4 border-b border-line px-5 py-2.5 text-xs font-medium text-muted md:grid"
	>
		<span>Container</span><span>Image</span><span>State</span><span>Update</span>
	</div>
	<ul class="divide-y divide-line">
		{#each sorted as c (c.key)}
			<li
				class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-5 py-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_6rem_minmax(0,1.2fr)]"
			>
				<p class="truncate text-sm font-medium">{c.name}</p>
				<p
					class="col-span-2 row-start-2 truncate font-mono text-xs text-muted md:col-span-1 md:row-start-auto"
					title="{c.image}:{c.tag} ({c.image_id})"
				>
					{c.image}<span class="text-faint">:</span><span class="text-text">{c.tag}</span>
				</p>
				<span
					class="hidden text-xs md:block"
					style:color={c.state === 'running' ? 'var(--good-text)' : 'var(--muted)'}
				>
					{c.state}
				</span>
				<span class="flex items-center gap-1.5 text-xs">
					{#if c.update}
						<ArrowUpCircle class="size-3.5 shrink-0 text-accent" />
						<span class="font-medium text-accent">{c.update_to ?? 'newer image'}</span>
					{:else if c.unchecked}
						<CircleHelp class="size-3.5 shrink-0 text-faint" />
						<span class="truncate text-muted" title={c.unchecked}>can't check: {c.unchecked}</span>
					{:else if stale}
						<span class="text-muted">was up to date</span>
					{:else}
						<Check class="size-3.5 shrink-0" style="color: var(--good-text)" />
						<span class="text-muted">up to date</span>
					{/if}
					{#if c.state !== 'running'}
						<span class="ml-auto text-faint md:hidden">{c.state}</span>
					{/if}
					{#if act}
						<span class="ml-auto flex shrink-0 gap-1">
							{#if c.update && c.update_to === 'newer build'}
								<button
									class="h-6 rounded-md border border-line px-2 hover:border-line-strong disabled:opacity-50"
									disabled={busy !== null}
									onclick={() => (asking = `update-container ${c.name}`)}
									title="Pull its image again and recreate it with the same settings"
								>
									{busy === c.name ? '…' : 'Update'}
								</button>
							{/if}
							<button
								class="h-6 rounded-md border border-line px-2 text-muted hover:border-line-strong disabled:opacity-50"
								disabled={busy !== null}
								onclick={() => (asking = `restart-container ${c.name}`)}
							>
								Restart
							</button>
						</span>
					{/if}
				</span>
				{#if asking?.endsWith(` ${c.name}`)}
					{@const ability = asking.split(' ')[0] as 'update-container' | 'restart-container'}
					<p class="col-span-full text-xs">
						{ability === 'update-container'
							? `Update ${c.name}? It is recreated with the new image (the old one comes back if that fails).`
							: `Restart ${c.name}?`}
						<button class="ml-1 underline" onclick={() => run(ability, c.name)}>Yes</button>
						<button class="ml-1 underline" onclick={() => (asking = null)}>No</button>
					</p>
				{/if}
				{#if notes[c.name]}
					<p
						class="col-span-full text-xs"
						style:color={notes[c.name].ok ? 'var(--good-text)' : 'var(--critical-text)'}
					>
						{notes[c.name].text}
					</p>
				{/if}
			</li>
		{/each}
	</ul>
</div>
