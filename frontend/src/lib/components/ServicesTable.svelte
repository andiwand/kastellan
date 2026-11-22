<script lang="ts">
	import type { Bucket, CheckStatus, Device } from '#lib/api.js';
	import { ms, percent, stateFor } from '#lib/format.js';
	import { checkStatusOf, uptimeOf } from '#lib/status.js';
	import { store } from '#lib/state.svelte.js';
	import StatusBadge from './StatusBadge.svelte';
	import UptimeStrip from './UptimeStrip.svelte';
	import KindIcon from './KindIcon.svelte';
	import CertNote from './CertNote.svelte';
	import OwnServiceLine from './OwnServiceLine.svelte';
	import type { OwnService } from '#lib/ownServices.js';

	/** Services with what they run, their state, how long, and their uptime. With `showDevice`,
	 * each row names its device (the Services page); without, it is one device's page. */
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
		canChange = () => false
	}: {
		rows: { device: Device; check: CheckStatus }[];
		buckets: (device: string, check: string) => Bucket[];
		start: string | null;
		step: number;
		rangeLabel?: string;
		showDevice?: boolean;
		// One device's page: its own services under the check each answers, and the others.
		own?: Record<string, OwnService[]>;
		rest?: OwnService[];
		onDevice?: Device; // the device of `rest` (it may have no checks): given with it
		canChange?: (s: OwnService) => boolean;
	} = $props();
	const SOURCE = { openwrt: 'OpenWrt service', systemd: 'systemd unit' };

	const target = (check: CheckStatus) =>
		check.type === 'ping' || check.type === 'dhcp'
			? check.type
			: check.type === 'tcp'
				? `port ${check.target}`
				: check.type === 'dns'
					? `name ${check.target}`
					: String(check.target);
	const cols = 'lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1.3fr)_6.5rem_4.5rem_9rem_minmax(0,1.3fr)]';
</script>

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
			{@const status = checkStatusOf(device, check)}
			{@const b = buckets(device.id, check.name)}
			<li
				class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 px-5 py-3.5 {cols}"
			>
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
						{#each own[check.name] ?? [] as svc (svc.source + svc.name)}
							<div class="mt-1">
								<OwnServiceLine device={device.id} service={svc} canChange={canChange(svc)} />
							</div>
						{/each}
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
				<div><StatusBadge {status} /></div>
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
				<div class="col-span-2 flex items-center gap-3 lg:col-span-1">
					<div class="flex-1">
						{#if start && b.length}
							<UptimeStrip
								buckets={b}
								{start}
								{step}
								alwaysOn={device.always_on}
								height="h-4"
								label="{check.name} uptime"
							/>
						{/if}
					</div>
					<span class="tabular w-14 shrink-0 text-right text-xs text-muted">
						{percent(uptimeOf(b))}
					</span>
				</div>
			</li>
		{/each}
		{#each rest as svc (svc.source + svc.name)}
			<li
				class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-5 py-2.5 {cols}"
			>
				<div class="min-w-0">
					<p class="truncate font-mono text-sm">{svc.name}</p>
					<p class="truncate text-xs text-faint">{SOURCE[svc.source]}</p>
				</div>
				<div class="col-span-2 min-w-0 lg:col-span-5">
					{#if onDevice}<OwnServiceLine
							device={onDevice.id}
							service={svc}
							canChange={canChange(svc)}
							named={false}
						/>{/if}
				</div>
			</li>
		{/each}
	</ul>
</div>
