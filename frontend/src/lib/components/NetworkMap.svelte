<script lang="ts">
	import { api, type Device, type NetworkRef, type Networks } from '#lib/api.js';
	import { statusColor, statusLabel, statusOf } from '#lib/status.js';
	import { ms } from '#lib/format.js';
	import KindIcon from './KindIcon.svelte';
	import StatusDot from './StatusDot.svelte';
	import Globe from '@lucide/svelte/icons/globe';
	import House from '@lucide/svelte/icons/house';
	import Network from '@lucide/svelte/icons/network';
	import CircleHelp from '@lucide/svelte/icons/circle-question-mark';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';

	/** The devices by network. Each network is a zone; the devices in one are simply connected
	 * to each other. A device in two networks (the router between home and the internet, a VPN
	 * server) sits on the boundary between them. What can be reached from the internet carries a
	 * globe, and the internet zone lists every way in that Kastellan knows of. Devices not in
	 * `matching` are faded, so a search keeps the shape. */
	let { devices, matching }: { devices: Device[]; matching: Set<string> | null } = $props();

	let networks = $state<Networks | null>(null);
	// Fetched again with each refresh of the devices: right after the hub starts, its own
	// address isn't known yet.
	$effect(() => {
		void devices;
		api
			.networks()
			.then((n) => (networks = n))
			.catch(() => {});
	});

	const UNKNOWN: NetworkRef = { id: '?', name: 'Not resolved', cidr: null, local: true };

	interface Boundary {
		above: string;
		below: string;
		devices: Device[];
	}

	const zoneIds = (d: Device) => (d.networks.length ? d.networks.map((n) => n.id) : [UNKNOWN.id]);

	// Zones top to bottom: the internet, the hub's network, then the others, each one next to a
	// zone it shares a device with where it can (a site's router between it and the internet
	// can't always be: then it shows inside its site, "also Internet").
	const zones = $derived.by(() => {
		const byId = new Map<string, NetworkRef>();
		for (const d of devices) for (const n of d.networks) byId.set(n.id, n);
		if (devices.some((d) => d.networks.length === 0)) byId.set(UNKNOWN.id, UNKNOWN);
		// Ways in from outside show in the internet zone, even with nothing else in it.
		if (devices.some((d) => d.public_ways.length) && !byId.has('internet'))
			byId.set('internet', { id: 'internet', name: 'Internet', cidr: null, local: false });
		const hub = networks?.hub?.id;
		const rank = (n: NetworkRef) =>
			n.id === 'internet' ? 0 : n.id === hub ? 1 : n.id === UNKNOWN.id ? 3 : 2;
		const rest = [...byId.values()].sort(
			(a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name)
		);
		const linked = (a: string, b: string) =>
			devices.some((d) => {
				const ids = zoneIds(d);
				return ids.includes(a) && ids.includes(b);
			});
		const order: NetworkRef[] = [];
		while (rest.length) {
			const last = order[order.length - 1];
			// The next of the same rank that shares a device with the last one, else the next.
			const i = last
				? rest.findIndex((n) => rank(n) === rank(rest[0]) && linked(last.id, n.id))
				: -1;
			order.push(...rest.splice(i >= 0 ? i : 0, 1));
		}
		return order;
	});

	// Each device in two or more zones goes on one boundary: between the two of its zones that
	// are next to each other (or, if none are, above the lowest of them).
	const layout = $derived.by(() => {
		const order = zones.map((z) => z.id);
		const inside = new Map<string, Device[]>(order.map((id) => [id, []]));
		const boundaries = new Map<string, Boundary>();
		for (const d of [...devices].sort((a, b) => a.name.localeCompare(b.name))) {
			const ids = zoneIds(d).filter((id) => order.includes(id));
			if (ids.length <= 1) {
				inside.get(ids[0] ?? UNKNOWN.id)?.push(d);
				continue;
			}
			const idx = ids.map((id) => order.indexOf(id)).sort((a, b) => a - b);
			const pair = idx.findIndex((v, i) => i > 0 && v === idx[i - 1] + 1);
			if (pair < 0) {
				// Its zones aren't next to each other: inside the lowest local one, "also ...".
				const local = idx.filter((i) => order[i] !== 'internet');
				inside.get(order[local[local.length - 1] ?? idx[0]])?.push(d);
				continue;
			}
			const below = idx[pair];
			const key = `${order[below - 1]}|${order[below]}`;
			const b = boundaries.get(key) ?? {
				above: order[below - 1],
				below: order[below],
				devices: []
			};
			b.devices.push(d);
			boundaries.set(key, b);
		}
		return { inside, boundaries };
	});

	const boundaryAbove = (id: string) =>
		[...layout.boundaries.values()].find((b) => b.below === id) ?? null;
	// Ways in to devices behind a boundary; a device on the internet itself shows as a card there.
	const ways = $derived(
		devices
			.filter((d) => !d.networks.some((n) => n.id === 'internet'))
			.flatMap((d) => d.public_ways.map((w) => ({ device: d, way: w })))
	);
	// The hub's network, unnamed in config.yaml, is "Home".
	const title = (net: NetworkRef) =>
		net.id === networks?.hub?.id && net.name === net.cidr ? 'Home' : net.name;
	const reachable = (d: Device) =>
		d.exposed || d.public_ways.length > 0 || d.networks.some((n) => n.id === 'internet');
	const dim = (id: string) => matching !== null && !matching.has(id);
	const otherZones = (d: Device, here: string[]) =>
		d.networks.filter((n) => !here.includes(n.id)).map((n) => n.name);
	const host = (target: string | number | null) => {
		try {
			return new URL(String(target)).host;
		} catch {
			return String(target ?? '');
		}
	};
</script>

{#snippet node(device: Device, note: string = '')}
	{@const s = statusOf(device)}
	{@const ping = device.checks.find((c) => c.type === 'ping' && c.up)}
	{@const trouble = s === 'down' || s === 'degraded'}
	<a
		href="/devices/{device.id}"
		class="group relative flex h-[58px] w-full items-center gap-3 rounded-xl border bg-surface px-3 shadow-card transition-all hover:-translate-y-px hover:shadow-pop"
		class:opacity-25={dim(device.id)}
		class:opacity-70={!dim(device.id) && s === 'offline'}
		style:border-color={trouble
			? `color-mix(in srgb, ${statusColor[s].fill} 55%, transparent)`
			: 'var(--line)'}
		title={note || undefined}
	>
		<div
			class="grid size-9 shrink-0 place-items-center rounded-lg bg-surface-2 text-muted group-hover:text-text"
		>
			<KindIcon kind={device.kind} />
		</div>
		<div class="min-w-0 flex-1">
			<p class="truncate text-[13px] font-medium">{device.name}</p>
			<p class="tabular truncate text-[11px] text-faint">
				{ping ? ms(ping.latency_ms) : statusLabel[s]}{note ? ` · ${note}` : ''}
			</p>
		</div>
		{#if reachable(device)}
			<span
				class="grid size-5 shrink-0 place-items-center rounded-full bg-accent-soft text-accent"
				title={device.public_ways.length
					? `From the internet: ${device.public_ways.map((w) => host(w.target)).join(', ')}`
					: device.exposed
						? 'Marked as reachable from the internet'
						: 'On the internet'}
			>
				<Globe class="size-3" />
				<span class="sr-only">reachable from the internet</span>
			</span>
		{/if}
		<StatusDot status={s} size="size-2" />
	</a>
{/snippet}

{#snippet zoneIcon(net: NetworkRef)}
	{#if net.id === 'internet'}<Globe class="size-4" />{:else if net.id === UNKNOWN.id}<CircleHelp
			class="size-4"
		/>{:else if net.id === networks?.hub?.id}<House class="size-4" />{:else}<Network
			class="size-4"
		/>{/if}
{/snippet}

<div class="flex flex-col">
	{#each zones as net, i (net.id)}
		{@const boundary = boundaryAbove(net.id)}
		{#if boundary}
			<!-- Devices on the boundary: half in the zone above, half in this one. -->
			<div class="relative z-10 -my-7 flex flex-wrap justify-center gap-3 px-4">
				{#each boundary.devices as d (d.id)}
					{@const others = otherZones(d, [boundary.above, boundary.below])}
					<div class="w-[min(240px,100%)]">
						{@render node(d, others.length ? `also ${others.join(', ')}` : '')}
					</div>
				{/each}
			</div>
		{:else if i > 0}
			<div class="h-4"></div>
		{/if}
		<section
			class="rounded-2xl border p-4 sm:p-5 {boundary ? 'pt-10' : ''} {boundaryAbove(
				zones[i + 1]?.id ?? ''
			)
				? 'pb-10'
				: ''}"
			style:border-style={net.local ? 'solid' : 'dashed'}
			style:border-color="var(--line-strong)"
			style:background={net.id === 'internet'
				? 'color-mix(in srgb, var(--accent) 4%, var(--surface-2))'
				: 'var(--surface-2)'}
			aria-label={net.name}
		>
			<div class="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
				<h2 class="flex items-center gap-2 text-sm font-semibold">
					<span class="text-muted">{@render zoneIcon(net)}</span>
					{title(net)}
					{#if net.cidr && net.cidr !== title(net)}
						<span class="font-mono text-xs font-normal text-faint">{net.cidr}</span>
					{/if}
				</h2>
				<p class="text-xs text-muted">
					{#if net.id === networks?.hub?.id}
						Kastellan is here{networks?.hub_address ? ` (${networks.hub_address})` : ''}
					{:else if net.id === UNKNOWN.id}
						Names the hub couldn't look up
					{:else if net.id === 'internet'}
						Public addresses, and the ways in from outside
					{/if}
				</p>
			</div>

			{#if net.id === 'internet' && ways.length}
				<ul class="mb-3 flex flex-wrap gap-2">
					{#each ways as { device, way } (`${device.id}/${way.check}`)}
						<li>
							<a
								href="/devices/{device.id}"
								class="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1 text-xs hover:border-line-strong"
								class:opacity-25={dim(device.id)}
								title={way.address ? `leads to ${way.address}` : undefined}
							>
								<Globe class="size-3 text-accent" />
								<span class="font-mono">{host(way.target)}</span>
								<ArrowRight class="size-3 text-faint" />
								<span class="font-medium">{device.name}</span>
								<span class="text-faint">· {way.check}</span>
							</a>
						</li>
					{/each}
				</ul>
			{/if}

			{#if layout.inside.get(net.id)?.length}
				<div
					class="grid gap-2.5"
					style="grid-template-columns: repeat(auto-fill, minmax(212px, 1fr));"
				>
					{#each layout.inside.get(net.id) ?? [] as d (d.id)}
						{@const others = otherZones(d, [net.id])}
						{@render node(
							d,
							d.network_assumed
								? 'name not resolved'
								: others.length
									? `also ${others.join(', ')}`
									: ''
						)}
					{/each}
				</div>
			{:else if !(net.id === 'internet' && ways.length)}
				<p class="text-xs text-faint">Only the devices on its boundary.</p>
			{/if}
		</section>
	{/each}
</div>
