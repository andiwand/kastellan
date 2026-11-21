<script lang="ts">
	import type { Finding, Vulns } from '#lib/api.js';
	import { ago } from '#lib/format.js';
	import { store } from '#lib/state.svelte.js';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';
	import Shield from '@lucide/svelte/icons/shield';
	import ExternalLink from '@lucide/svelte/icons/external-link';

	/** Known vulnerabilities in what a device runs, most serious first, honest about doubt. */
	let { vulns, exposed }: { vulns: Vulns; exposed: boolean } = $props();

	const order = ['critical', 'high', 'medium', 'low', 'unknown'];
	const rank = (f: Finding) => (f.exploited ? -1 : order.indexOf(f.severity));
	const items = $derived(
		vulns.items
			.map((i) => ({ ...i, findings: [...i.findings].sort((a, b) => rank(a) - rank(b)) }))
			.sort((a, b) => b.findings.length - a.findings.length)
	);
	const count = $derived(items.reduce((n, i) => n + i.findings.length, 0));
	const tone: Record<string, string> = {
		critical: 'var(--critical-text)',
		high: 'var(--warning-text)',
		medium: 'var(--muted)',
		low: 'var(--faint)',
		unknown: 'var(--faint)'
	};
	let open = $state<Record<string, boolean>>({});
</script>

<div class="card p-4">
	<div class="flex flex-wrap items-baseline justify-between gap-2">
		<p class="flex items-center gap-2 text-sm font-medium">
			{#if count}
				<ShieldAlert class="size-4" style="color: var(--warning-text)" />
				{count} known {count === 1 ? 'vulnerability' : 'vulnerabilities'}
			{:else if vulns.items.length}
				<ShieldCheck class="size-4" style="color: var(--good-text)" />
				None known for {vulns.items.map((i) => i.product).join(', ')}
			{:else}
				<Shield class="size-4 text-faint" />
				Nothing here could be looked up
			{/if}
			{#if exposed}<span class="rounded-md bg-surface-2 px-1.5 py-0.5 text-xs font-normal">
					internet-facing
				</span>{/if}
		</p>
		<p class="text-xs text-muted">NVD, OSV, CISA KEV · {ago(vulns.checked, store.now)}</p>
	</div>

	{#each items as item (item.where)}
		{#if item.findings.length}
			<div class="mt-4">
				<p class="text-xs text-muted">
					<span class="font-medium text-text">{item.product}</span> · {item.where}
				</p>
				<ul class="mt-1.5 divide-y divide-line rounded-lg border border-line">
					{#each open[item.where] ? item.findings : item.findings.slice(0, 5) as f (f.id)}
						<li class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 py-2 text-sm">
							<a
								href={f.url}
								target="_blank"
								rel="noopener noreferrer"
								class="inline-flex items-center gap-1 font-mono text-xs hover:underline"
							>
								{f.id}<ExternalLink class="size-3 text-faint" />
							</a>
							<span class="text-xs font-medium capitalize" style:color={tone[f.severity]}>
								{f.severity}{f.score != null ? ` ${f.score.toFixed(1)}` : ''}
							</span>
							{#if f.exploited}
								<span
									class="rounded px-1.5 text-[11px] font-semibold"
									style:background="color-mix(in srgb, var(--critical) 14%, transparent)"
									style:color="var(--critical-text)"
									title="In CISA's catalogue of vulnerabilities exploited in the wild"
								>
									exploited
								</span>
							{/if}
							{#if f.confidence === 'patch'}
								<span
									class="text-[11px] text-faint"
									title="NVD distinguishes patch levels that this banner omits. Check the full version on the device."
								>
									patch level unknown: check the device
								</span>
							{:else if f.confidence !== 'exact'}
								<span
									class="text-[11px] text-faint"
									title="The distribution may have fixed this without changing the upstream version; check its security notice"
								>
									maybe fixed by your distribution
								</span>
							{/if}
							{#if f.fixed_in}<span class="text-[11px] text-muted">fixed in {f.fixed_in}</span>{/if}
							<span class="w-full truncate text-xs text-muted" title={f.summary}>{f.summary}</span>
						</li>
					{/each}
				</ul>
				{#if item.findings.length > 5 && !open[item.where]}
					<button
						class="mt-1 text-xs font-medium text-accent hover:underline"
						onclick={() => (open[item.where] = true)}
					>
						Show all {item.findings.length}
					</button>
				{/if}
			</div>
		{/if}
	{/each}

	{#if vulns.unknown.length}
		<p class="mt-3 text-xs" style:color="var(--warning-text)">
			Couldn't be looked up just now: {vulns.unknown.map((u) => u.product).join(', ')}
			{#if Object.keys(vulns.errors).length}({Object.values(vulns.errors).join('; ')}){/if}. That's
			not the same as safe.
		</p>
	{/if}
	{#if vulns.kev}
		<p class="mt-2 text-xs" style:color="var(--warning-text)">
			{vulns.kev}: the "exploited" marks may be out of date.
		</p>
	{/if}
	{#if vulns.not_looked_up?.length}
		<p class="mt-2 text-xs text-muted">
			Not looked up (a moving tag, no exact version): {vulns.not_looked_up.join(', ')}.
		</p>
	{/if}
	<p class="mt-3 text-[11px] text-faint">
		Only what Kastellan can identify is looked up: services that name their version, firmware,
		images with an exact tag. NVD lags behind with recent CVEs.
	</p>
</div>
