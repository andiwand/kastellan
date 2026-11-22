<script lang="ts">
	import type { Bucket, CheckStatus, Container, Device } from '#lib/api.js';
	import { ms, percent, stateFor } from '#lib/format.js';
	import { checkStatusOf, uptimeOf, type Status } from '#lib/status.js';
	import { store } from '#lib/state.svelte.js';
	import StatusBadge from './StatusBadge.svelte';
	import UptimeStrip from './UptimeStrip.svelte';
	import KindIcon from './KindIcon.svelte';
	import CertNote from './CertNote.svelte';
	import OwnServiceActions from './OwnServiceActions.svelte';
	import { servedBy, type OwnService } from '#lib/ownServices.js';

	/** Services with what they run, their state, how long, and their uptime: one row each, the
	 * same columns for all. With `showDevice`, each row names its device (the Services page);
	 * without, it is one device's page: under each check the device's own services that answer
	 * it (dropbear under SSH), then its other services, and the containers serving a check. */
	let {
		rows,
		buckets,
		start,
		step,
		rangeLabel = '24 h',
		showDevice = false,
		own = Object.create(null),
		rest = [],
		onDevice,
		ownBuckets = () => [],
		canChange = () => false
	}: {
		rows: { device: Device; check: CheckStatus }[];
		buckets: (device: string, check: string) => Bucket[];
		start: string | null;
		step: number;
		rangeLabel?: string;
		showDevice?: boolean;
		own?: Record<string, OwnService[]>;
		rest?: OwnService[];
		onDevice?: Device; // the device of the own services (it may have no checks)
		ownBuckets?: (service: string) => Bucket[];
		canChange?: (s: OwnService) => boolean;
	} = $props();
	const SOURCE = { openwrt: 'OpenWrt service', systemd: 'systemd unit' };
	const OWN_STATUS: Record<OwnService['state'], [Status, string]> = {
		running: ['up', 'Running'],
		stopped: ['offline', 'Stopped'],
		failed: ['down', 'Failed']
	};

	const target = (check: CheckStatus) =>
		check.type === 'ping' || check.type === 'dhcp'
			? check.type
			: check.type === 'tcp'
				? `port ${check.target}`
				: check.type === 'dns'
					? `name ${check.target}`
					: String(check.target);
	const cols = 'lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1.3fr)_6.5rem_4.5rem_9rem_minmax(0,1.3fr)]';
	const row = `grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 px-5 py-3.5 ${cols}`;
</script>

{#snippet uptime(b: Bucket[], alwaysOn: boolean, label: string)}
	<div class="col-span-2 flex items-center gap-3 lg:col-span-1">
		<div class="flex-1">
			{#if start && b.length}
				<UptimeStrip buckets={b} {start} {step} {alwaysOn} height="h-4" {label} />
			{/if}
		</div>
		<span class="tabular w-14 shrink-0 text-right text-xs text-muted">{percent(uptimeOf(b))}</span>
	</div>
{/snippet}

{#snippet ownRow(svc: OwnService, under: boolean)}
	{@const [status, label] = OWN_STATUS[svc.state]}
	{@const since = onDevice?.services_since?.[svc.name]}
	<li class="{row} {under ? 'bg-surface-2/40' : ''}">
		<div class="flex min-w-0 items-start gap-2" class:pl-4={under}>
			{#if under}<span
					class="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-bl border-b border-l border-line-strong"
				></span>{/if}
			<div class="min-w-0">
				<p class="truncate font-mono text-sm">{svc.name}</p>
				<p class="truncate text-xs text-faint">
					{SOURCE[svc.source]}{svc.enabled === false ? ' · not started at boot' : ''}
				</p>
				{#if onDevice && canChange(svc)}<OwnServiceActions
						device={onDevice.id}
						service={svc}
						canChange
					/>{/if}
			</div>
		</div>
		<p class="hidden text-sm text-faint lg:block">–</p>
		<div><StatusBadge {status} {label} /></div>
		<span class="tabular hidden text-right text-sm text-faint lg:block">–</span>
		<span class="tabular hidden text-sm text-muted lg:block">
			{since ? stateFor(since, store.now) : '–'}
		</span>
		<!-- Not started at boot: off may be meant, so grey, not red (like a device that may be off). -->
		{@render uptime(ownBuckets(svc.name), svc.enabled !== false, `${svc.name} running`)}
	</li>
{/snippet}

<div class="card overflow-hidden">
	<div
		class="hidden gap-4 border-b border-line px-5 py-2.5 text-xs font-medium text-muted lg:grid {cols}"
	>
		<span>Service</span><span>Software</span><span>Status</span><span class="text-right">
			Response
		</span><span>For</span><span>Uptime {rangeLabel}</span>
	</div>
	<ul class="divide-y divide-line">
		{#each rows as { device, check } (`${device.id}/${check.name}`)}
			{@const containers = showDevice ? [] : servedBy(check, device.containers)}
			<li class={row}>
				<div class="flex min-w-0 items-center gap-3">
					{#if showDevice}
						<a
							href="/devices/{device.id}"
							class="grid size-8 shrink-0 place-items-center rounded-lg bg-surface-2 text-muted hover:text-text"
							title={device.name}
						>
							<KindIcon kind={device.kind} />
						</a>
					{/if}
					<div class="min-w-0">
						<p class="truncate text-sm font-medium">
							{#if showDevice}<a href="/devices/{device.id}" class="hover:underline">
									{device.name}
								</a>
								<span class="font-normal text-muted">· {check.name}</span>{:else}{check.name}{/if}
						</p>
						<p class="truncate font-mono text-xs text-faint" title={target(check)}>
							{target(check)}
						</p>
						{#if containers.length}
							<p class="truncate text-xs text-faint">
								container {#each containers as c, i (c.key)}<a
										href="#containers"
										class="font-mono hover:text-text"
									>
										{c.name}
									</a>{i < containers.length - 1 ? ', ' : ''}{/each}
							</p>
						{/if}
						{#if check.cert || check.address}
							<div class="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-faint">
								{#if check.cert}<CertNote cert={check.cert} />{/if}
								{#if check.address}
									<span class="truncate font-mono" title="the address this name led to">
										via {check.address}
									</span>
								{/if}
							</div>
						{/if}
					</div>
				</div>
				<div class="hidden min-w-0 lg:block">
					{#if check.software}
						<p class="truncate text-sm" title={check.software}>{check.software}</p>
					{:else}
						<p class="text-sm text-faint">–</p>
					{/if}
				</div>
				<div><StatusBadge status={checkStatusOf(device, check)} /></div>
				<!-- Software on narrow screens, under the name -->
				<p class="col-span-2 -mt-1 truncate text-xs text-muted lg:hidden">
					{check.software ?? ''}
				</p>
				<!-- Cut short to its column (an error can be long); all of it on hover. -->
				<span
					class="tabular hidden min-w-0 truncate text-right text-sm lg:block"
					title={check.detail || ''}
				>
					{check.up ? ms(check.latency_ms) : check.detail || '–'}
				</span>
				<span class="tabular hidden text-sm text-muted lg:block">
					{check.up == null ? '–' : stateFor(check, store.now) || '–'}
				</span>
				{@render uptime(buckets(device.id, check.name), device.always_on, `${check.name} uptime`)}
			</li>
			{#each own[check.name] ?? [] as svc (svc.source + svc.name)}
				{@render ownRow(svc, true)}
			{/each}
		{/each}
		{#each rest as svc (svc.source + svc.name)}
			{@render ownRow(svc, false)}
		{/each}
	</ul>
</div>
