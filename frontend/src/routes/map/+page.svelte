<script lang="ts">
	import { store } from '#lib/state.svelte.js';
	import { layoutTree } from '#lib/layout.js';
	import { statusColor, statusLabel, statusOf, type Status } from '#lib/status.js';
	import { ms } from '#lib/format.js';
	import KindIcon from '#lib/components/KindIcon.svelte';
	import StatusDot from '#lib/components/StatusDot.svelte';
	import Plus from '@lucide/svelte/icons/plus';
	import Minus from '@lucide/svelte/icons/minus';
	import Scan from '@lucide/svelte/icons/scan';

	const W = 212;
	const H = 58;
	const PAD = 32;
	const layout = $derived(layoutTree(store.devices ?? [], { w: W, h: H, gapX: 56, gapY: 12 }));

	// Fit the whole network into the card by default; the buttons zoom from there.
	let viewport = $state(0);
	let zoom = $state<number | null>(null);
	const contentW = $derived(layout.width + PAD * 2);
	const contentH = $derived(layout.height + PAD * 2);
	const fit = $derived(viewport ? Math.min(1, viewport / contentW) : 1);
	const scale = $derived(zoom ?? fit);
	const step = (factor: number) => (zoom = Math.min(1.5, Math.max(0.3, scale * factor)));

	// A link is drawn in the child's color when it is in trouble, so a broken branch stands out.
	const linkPath = (fx: number, fy: number, tx: number, ty: number) => {
		const mid = (fx + tx) / 2;
		return `M${fx},${fy} C${mid},${fy} ${mid},${ty} ${tx},${ty}`;
	};
	const legend: Status[] = ['up', 'degraded', 'down', 'offline'];
</script>

<svelte:head><title>Network map · Kastellan</title></svelte:head>

<div class="flex flex-wrap items-end justify-between gap-4">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">Network map</h1>
		<p class="mt-1 text-sm text-muted">
			Who is connected through whom, as each device's parent in config.yaml says.
		</p>
	</div>
	<ul class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
		{#each legend as s (s)}
			<li class="flex items-center gap-1.5">
				<StatusDot status={s} size="size-2" />{statusLabel[s]}
			</li>
		{/each}
	</ul>
</div>

<div class="card relative mt-6 overflow-hidden">
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
			title="Fit the network"
			aria-label="Fit the network"
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
		{#if store.devices === null}
			<div class="h-96 animate-pulse"></div>
		{:else if !layout.nodes.length}
			<p class="px-6 py-16 text-center text-sm text-muted">No devices yet.</p>
		{:else}
			<div class="mx-auto" style:width="{contentW * scale}px" style:height="{contentH * scale}px">
				<div
					class="relative origin-top-left transition-transform duration-200"
					style:width="{contentW}px"
					style:height="{contentH}px"
					style:transform="scale({scale})"
				>
					<svg
						class="absolute inset-0"
						width={layout.width + PAD * 2}
						height={layout.height + PAD * 2}
						aria-hidden="true"
					>
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
							/>
						{/each}
					</svg>
					{#each layout.nodes as node (node.device.id)}
						{@const device = node.device}
						{@const s = statusOf(device)}
						{@const ping = device.checks.find((c) => c.type === 'ping' && c.up)}
						<a
							href="/devices/{device.id}"
							class="group absolute flex items-center gap-3 rounded-xl border bg-surface px-3 shadow-card transition-all hover:-translate-y-px hover:shadow-pop"
							class:opacity-70={s === 'offline'}
							style:left="{node.x + PAD}px"
							style:top="{node.y - H / 2 + PAD}px"
							style:width="{W}px"
							style:height="{H}px"
							style:border-color={s === 'down' || s === 'degraded'
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
					{/each}
				</div>
			</div>
		{/if}
	</div>
</div>
