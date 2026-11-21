<script lang="ts">
	import type { DhcpPool } from '#lib/api.js';

	/** A router's DHCP server: how much of its pool is in use, and who has an address. */
	let { pool }: { pool: DhcpPool } = $props();
	let open = $state(false);
	const full = $derived(pool.size ? pool.used / pool.size : 0);
</script>

<p class="mt-1 text-sm">
	{#if pool.server === false}
		<span class="text-muted">Its DHCP server is off.</span>
	{:else}
		<span style:color={full >= 0.9 ? 'var(--warning-text)' : undefined}>
			{pool.used}
			{pool.size ? `of ${pool.size} addresses in use` : pool.used === 1 ? 'lease' : 'leases'}
		</span>
		{#if pool.pools?.length}<span class="text-xs text-muted">
				· {pool.pools
					.map((p) => (p.end ? `${p.start} – ${p.end}` : `${p.interface}: from .${p.start}`))
					.join(', ')}
			</span>{:else if pool.pools === null}<span class="text-xs text-faint">
				· pool size unknown (set its login up again to read it)
			</span>{/if}
		{#if pool.leases.length}
			<button class="ml-1 text-xs underline" onclick={() => (open = !open)}>
				{open ? 'hide' : 'show'}
			</button>
		{/if}
	{/if}
</p>
{#if open}
	<ul class="mt-1 grid gap-x-6 gap-y-0.5 text-xs sm:grid-cols-2">
		{#each pool.leases as l, i (l.mac ?? i)}
			<li class="flex min-w-0 gap-2">
				<span class="truncate">{l.name ?? '–'}</span>
				<span class="font-mono text-muted">{l.address}</span>
				<span class="font-mono text-faint">{l.mac}</span>
			</li>
		{/each}
	</ul>
{/if}
