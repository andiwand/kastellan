<script lang="ts">
	import type { Device } from '#lib/api.js';
	import { layoutTree, splitLinked } from '#lib/layout.js';
	import { statusColor, statusLabel, statusOf } from '#lib/status.js';
	import { ms } from '#lib/format.js';
	import KindIcon from './KindIcon.svelte';
	import StatusDot from './StatusDot.svelte';
	import Plus from '@lucide/svelte/icons/plus';
	import Minus from '@lucide/svelte/icons/minus';
	import Scan from '@lucide/svelte/icons/scan';

	/** The devices as a graph: the links from config.yaml's `parent` as a left-to-right tree, and
	 * the devices without any link as a grid below. Devices not in `matching` are faded, so a
	 * search keeps the shape of the network. */
	let { devices, matching }: { devices: Device[]; matching: Set<string> | null } = $props();

	const W = 212;
	const H = 58;
	const PAD = 28;
	const split = $derived(splitLinked(devices));
	const layout = $derived(layoutTree(split.linked, { w: W, h: H, gapX: 56, gapY: 12 }));

	// Fit the tree into the card by default; the buttons zoom from there.
	let viewport = $state(0);
	let zoom = $state<number | null>(null);
	const contentW = $derived(layout.width + PAD * 2);
	const contentH = $derived(layout.height + PAD * 2);
	// Never below 60 %: on a phone the tree scrolls sideways rather than shrinking out of reach.
	const fit = $derived(viewport ? Math.max(0.6, Math.min(1, viewport / contentW)) : 1);
	const scale = $derived(zoom ?? fit);
	const step = (factor: number) => (zoom = Math.min(1.5, Math.max(0.3, scale * factor)));

	const linkPath = (fx: number, fy: number, tx: number, ty: number) => {
		const mid = (fx + tx) / 2;
		return `M${fx},${fy} C${mid},${fy} ${mid},${ty} ${tx},${ty}`;
	};
	const dim = (id: string) => matching !== null && !matching.has(id);
</script>

{#snippet node(device: Device)}
	{@const s = statusOf(device)}
	{@const ping = device.checks.find((c) => c.type === 'ping' && c.up)}
	{@const trouble = s === 'down' || s === 'degraded'}
	<a
		href="/devices/{device.id}"
		class="group flex h-full w-full items-center gap-3 rounded-xl border bg-surface px-3 shadow-card transition-all hover:-translate-y-px hover:shadow-pop"
		class:opacity-25={dim(device.id)}
		class:opacity-70={!dim(device.id) && s === 'offline'}
		style:border-color={trouble
			? `color-mix(in srgb, ${statusColor[s].fill} 55%, transparent)`
			: 'var(--line)'}
	>
		<div
			class="grid size-9 shrink-0 place-items-center rounded-lg bg-surface-2 text-muted group-hover:text-text"
		>
			<KindIcon kind={device.kind} />
		</div>
		<div class="min-w-0 flex-1">
			<p class="truncate text-[13px] font-medium">{device.name}</p>
			<p class="tabular truncate text-[11px] text-faint">
				{ping ? ms(ping.latency_ms) : statusLabel[s]}
			</p>
		</div>
		<StatusDot status={s} size="size-2" />
	</a>
{/snippet}

<div class="space-y-4">
	{#if split.linked.length}
		<div class="card relative overflow-hidden">
			<div
				class="absolute top-3 right-3 z-10 flex overflow-hidden rounded-lg border border-line bg-surface shadow-card"
			>
				<button
					class="grid size-8 place-items-center text-muted hover:bg-surface-2 hover:text-text"
					onclick={() => step(1 / 1.2)}
					title="Zoom out"
					aria-label="Zoom out"
				>
					<Minus class="size-4" />
				</button>
				<button
					class="grid size-8 place-items-center border-x border-line text-muted hover:bg-surface-2 hover:text-text"
					onclick={() => (zoom = null)}
					title="Fit"
					aria-label="Fit the graph"
				>
					<Scan class="size-4" />
				</button>
				<button
					class="grid size-8 place-items-center text-muted hover:bg-surface-2 hover:text-text"
					onclick={() => step(1.2)}
					title="Zoom in"
					aria-label="Zoom in"
				>
					<Plus class="size-4" />
				</button>
			</div>
			<div
				class="overflow-auto"
				bind:clientWidth={viewport}
				style="background-image: radial-gradient(var(--line-strong) 1px, transparent 1px); background-size: 20px 20px;"
			>
				<div class="mx-auto" style:width="{contentW * scale}px" style:height="{contentH * scale}px">
					<div
						class="relative origin-top-left transition-transform duration-200"
						style:width="{contentW}px"
						style:height="{contentH}px"
						style:transform="scale({scale})"
					>
						<svg class="absolute inset-0" width={contentW} height={contentH} aria-hidden="true">
							{#each layout.links as link (`${link.from.device.id}-${link.to.device.id}`)}
								{@const s = statusOf(link.to.device)}
								{@const trouble = s === 'down' || s === 'degraded'}
								<path
									d={linkPath(
										link.from.x + W + PAD,
										link.from.y + PAD,
										link.to.x + PAD,
										link.to.y + PAD
									)}
									fill="none"
									stroke={trouble ? statusColor[s].fill : 'var(--line-strong)'}
									stroke-width={trouble ? 2 : 1.5}
									stroke-dasharray={s === 'offline' || s === 'down' ? '4 4' : undefined}
									opacity={dim(link.to.device.id) ? 0.3 : 1}
								/>
							{/each}
						</svg>
						{#each layout.nodes as n (n.device.id)}
							<div
								class="absolute"
								style:left="{n.x + PAD}px"
								style:top="{n.y - H / 2 + PAD}px"
								style:width="{W}px"
								style:height="{H}px"
							>
								{@render node(n.device)}
							</div>
						{/each}
					</div>
				</div>
			</div>
		</div>
	{/if}

	{#if split.unlinked.length}
		<section class="card p-4 sm:p-5">
			<div class="mb-3 flex flex-wrap items-baseline justify-between gap-2">
				<h2 class="text-sm font-semibold">
					{split.linked.length ? 'Not linked' : 'Devices'}
					<span class="tabular ml-1 font-normal text-faint">{split.unlinked.length}</span>
				</h2>
				{#if split.linked.length}
					<p class="text-xs text-muted">
						No <code class="font-mono">parent</code> and nothing behind them, like most phones, laptops
						and Wi-Fi gadgets.
					</p>
				{/if}
			</div>
			<div
				class="grid gap-2.5"
				style="grid-template-columns: repeat(auto-fill, minmax({W}px, 1fr));"
			>
				{#each split.unlinked as device (device.id)}
					<div style:height="{H}px">{@render node(device)}</div>
				{/each}
			</div>
		</section>
	{/if}
</div>
