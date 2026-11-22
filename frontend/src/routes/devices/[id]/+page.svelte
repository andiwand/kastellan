<script lang="ts">
	import RangePicker from '#lib/components/ui/RangePicker.svelte';
	import PageHeader from '#lib/components/ui/PageHeader.svelte';
	import Section from '#lib/components/ui/Section.svelte';
	import StateDot from '#lib/components/ui/StateDot.svelte';
	import { issuesOf } from '#lib/attention.js';
	import { HOSTS, presentOf, sectionsOf } from '#lib/devicePage.js';
	import DeviceFacts from '#lib/components/DeviceFacts.svelte';
	import DeviceAttention from '#lib/components/DeviceAttention.svelte';
	import DeviceWifi from '#lib/components/DeviceWifi.svelte';
	import RouterInternet from '#lib/components/RouterInternet.svelte';
	import { page } from '$app/state';
	import { api, more, type Change, type Series } from '#lib/api.js';
	import { scriptName } from '#lib/updates.js';
	import { store } from '#lib/state.svelte.js';
	import { statusOf } from '#lib/status.js';
	import { ago, dayClock } from '#lib/format.js';
	import KindIcon from '#lib/components/KindIcon.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import LatencyChart from '#lib/components/LatencyChart.svelte';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import SquareTerminal from '@lucide/svelte/icons/square-terminal';
	import MonitorPlay from '@lucide/svelte/icons/monitor-play';
	import Bell from '@lucide/svelte/icons/bell';
	import BellOff from '@lucide/svelte/icons/bell-off';
	import Wrench from '@lucide/svelte/icons/wrench';
	import MaintenanceButton from '#lib/components/MaintenanceButton.svelte';
	import ActivityItem from '#lib/components/ActivityItem.svelte';
	import ServicesTable from '#lib/components/ServicesTable.svelte';
	import ContainersTable from '#lib/components/ContainersTable.svelte';
	import VmsTable from '#lib/components/VmsTable.svelte';
	import UpsCard from '#lib/components/UpsCard.svelte';
	import VulnsList from '#lib/components/VulnsList.svelte';
	import AccessSetup from '#lib/components/AccessSetup.svelte';
	import DeviceEditor from '#lib/components/DeviceEditor.svelte';
	import ConfigFile from '#lib/components/ConfigFile.svelte';
	import Tabs from '#lib/components/Tabs.svelte';
	import { goto } from '$app/navigation';
	import DeviceKeys from '#lib/components/DeviceKeys.svelte';
	import CollectorSetup from '#lib/components/CollectorSetup.svelte';
	import ActorSetup from '#lib/components/ActorSetup.svelte';
	import SoftwareSection from '#lib/components/SoftwareSection.svelte';
	import NetworkSection from '#lib/components/NetworkSection.svelte';
	import StorageSection from '#lib/components/StorageSection.svelte';
	import { groupOwn, ownServicesOf, type OwnService } from '#lib/ownServices.js';
	import VitalsPanel from '#lib/components/VitalsPanel.svelte';
	import BackupsCard from '#lib/components/BackupsCard.svelte';
	import ConfigBackups from '#lib/components/ConfigBackups.svelte';
	import ReadAgain from '#lib/components/ReadAgain.svelte';
	import FritzSetup from '#lib/components/FritzSetup.svelte';
	import SyslogSetup from '#lib/components/SyslogSetup.svelte';
	import OpenWrtSetup from '#lib/components/OpenWrtSetup.svelte';
	import RouterReboot from '#lib/components/RouterReboot.svelte';
	import HostReboot from '#lib/components/HostReboot.svelte';
	import WebLogin from '#lib/components/WebLogin.svelte';
	import SetupCard from '#lib/components/SetupCard.svelte';
	import WifiDialog from '#lib/components/WifiDialog.svelte';
	import FirmwareDialog from '#lib/components/FirmwareDialog.svelte';
	import CircleFadingArrowUp from '@lucide/svelte/icons/circle-fading-arrow-up';

	const id = $derived(page.params.id ?? '');
	const device = $derived(store.byId.get(id));
	const status = $derived(device ? statusOf(device) : 'waiting');
	const children = $derived((store.devices ?? []).filter((d) => d.parent === id));
	// Watching it (Status) is for everyone; setting it up (Setup) and changing its part of
	// config.yaml (Config: a form or the text) are for admins. Old links (?tab=edit|file) still
	// lead to the right place.
	type Tab = 'status' | 'setup' | 'config';
	const admin = $derived(store.me?.role === 'admin');
	// The device's own services (OpenWrt's, failed systemd units): under their check, or rows.
	const own = $derived(
		device ? groupOwn(device, ownServicesOf(device)) : { byCheck: {}, rest: [] }
	);
	const signedAdmin = $derived(store.me?.role === 'admin' && !!store.me.user);
	const canChangeOwn = (s: OwnService) =>
		signedAdmin && (s.source === 'openwrt' ? true : !!device?.act);
	const tab = $derived.by((): Tab => {
		const t = page.url.searchParams.get('tab');
		if (!admin) return 'status';
		// Setup opens doors on the device: one without an address of its own (a UPS read through
		// its host) has none.
		if (t === 'setup' && device?.host) return 'setup';
		return t === 'config' || t === 'edit' || t === 'file' ? 'config' : 'status';
	});
	const view = $derived(
		page.url.searchParams.get('view') === 'yaml' || page.url.searchParams.get('tab') === 'file'
			? 'yaml'
			: 'form'
	);
	function show(t: Tab, v: 'form' | 'yaml' = 'form') {
		const q =
			t === 'status' ? '?' : `?tab=${t}${t === 'config' && v === 'yaml' ? '&view=yaml' : ''}`;
		goto(q, { replace: true });
	}
	// Through the hub on its own name (webproxy.py) for signed-in admins when web: is set: the
	// hub hands out a one-time ticket and sends the browser there; else the device directly.
	const webUi = $derived.by(() => {
		if (!device?.web_url) return undefined;
		if (store.health?.web_domain && signedAdmin)
			return `/api/devices/${encodeURIComponent(device.id)}/web`;
		const http = device.checks.find((c) => c.type === 'http')?.target;
		return typeof http === 'string' ? http : device.web_url;
	});

	const ranges = [
		{ hours: 1, buckets: 60, label: '1 h' },
		{ hours: 24, buckets: 96, label: '24 h' },
		{ hours: 24 * 7, buckets: 84, label: '7 d' }
	];
	let hours = $state(24);
	const range = $derived(ranges.find((r) => r.hours === hours) ?? ranges[1]);
	let series = $state<Series | null>(null);
	let changes = $state<Change[]>([]);
	let chosen = $state<string | null>(null);
	let wifiOpen = $state(false);
	let firmwareOpen = $state(false);
	let fritzUpdate = $state<'asking' | 'started' | null>(null);
	let fritzNote = $state<string | null>(null);
	// Going to another device keeps this page: what was chosen or open here was for the last one
	// (the components below are keyed on the device and start afresh by themselves).
	$effect.pre(() => {
		void id;
		[chosen, wifiOpen, firmwareOpen, fritzUpdate, fritzNote] = [null, false, false, null, null];
	});
	async function updateFritz() {
		try {
			await more.fritzFirmware(id);
			fritzUpdate = 'started';
		} catch (e) {
			fritzUpdate = null;
			fritzNote = (e as Error).message;
		}
	}

	let loadError = $state<string | null>(null);
	let latest = 0;
	async function load() {
		// Only the newest request may write: an older one finishing late must not win.
		const mine = ++latest;
		try {
			const [s, c] = await Promise.all([
				api.series(range.hours, range.buckets, id),
				api.changes(range.hours, id, 50)
			]);
			if (mine === latest) [series, changes, loadError] = [s, c, null];
		} catch (e) {
			if (mine === latest) loadError = e instanceof Error ? e.message : String(e);
		}
	}
	$effect(() => {
		void range;
		void id;
		load();
		const timer = setInterval(load, 30000);
		return () => clearInterval(timer);
	});

	const checkSeries = $derived(series?.devices[id] ?? {});
	const latencyCheck = $derived(
		chosen && device?.checks.some((c) => c.name === chosen)
			? chosen
			: (device?.checks.find((c) => c.type === 'ping')?.name ?? device?.checks[0]?.name ?? null)
	);

	// What needs attention about it, and its sections in its kind's order (devicePage.ts).
	const issues = $derived(issuesOf(store.attention, id));
	const sections = $derived(
		device ? sectionsOf(device.kind, presentOf(device, own.rest.length)) : []
	);
</script>

<svelte:head><title>{device?.name ?? 'Device'} · Kastellan</title></svelte:head>

<a href="/devices" class="mb-4 inline-flex items-center gap-1.5 text-sm text-muted hover:text-text">
	<ArrowLeft class="size-4" /> Devices
</a>

{#if store.devices && !device}
	<div class="card mt-2 px-6 py-10 text-center text-sm text-muted">
		There is no device <code class="font-mono">{id}</code> in config.yaml.
	</div>
{:else if device}
	<!-- Keyed: another device's page starts afresh, never keeping (or saving) this one's forms,
	     questions or answers. -->
	{#key device.id}
		<!-- The name, its state and what can be done; the facts are under it (DeviceFacts). On a
		     phone the actions get a row of their own. -->
		<PageHeader title={device.name}>
			{#snippet badges()}
				<StatusBadge {status} />
				{#if store.alarms?.maintenance?.[device.id]}
					{@const m = store.alarms.maintenance[device.id]}
					<span
						class="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-0.5 text-xs font-medium text-muted"
						title="Problems show but are sent only if still there when it ends"
					>
						<Wrench class="size-3.5" />
						{m.reason} · until {dayClock(m.until)}
					</span>
				{/if}
			{/snippet}
			{#snippet actions()}
				<!-- Getting to it, acting on it, then its alarms. -->
				{#if device.ssh_port && signedAdmin}
					<a href="/devices/{device.id}/terminal" class="btn-header">
						<SquareTerminal class="size-4 text-muted" /> Terminal
					</a>
				{/if}
				{#if device.desktop && signedAdmin}
					<a href="/devices/{device.id}/desktop" class="btn-header">
						<MonitorPlay class="size-4 text-muted" /> Desktop
					</a>
				{/if}
				{#if webUi}
					<a
						href={webUi}
						target="_blank"
						rel="noopener noreferrer"
						class="btn-header shrink-0 self-start"
					>
						Open web interface <ExternalLink class="size-3.5 text-muted" />
					</a>
				{/if}
				{#if webUi && signedAdmin}
					<WebLogin device={device.id} proxied={!!store.health?.web_domain} />
				{/if}
				{#if device.fritzbox && device.fritz && signedAdmin}
					<RouterReboot device={device.id} kind="fritzbox" />
					{#if device.fritz.update?.available}
						<button
							class="btn-header"
							title="Install {device.fritz.update.version ?? 'the update FRITZ!OS offers'}"
							onclick={() => (fritzUpdate = 'asking')}
						>
							<CircleFadingArrowUp class="size-4 text-muted" />
							{fritzUpdate === 'asking'
								? 'Install it?'
								: fritzUpdate === 'started'
									? 'Updating…'
									: 'Update firmware'}
						</button>
						{#if fritzUpdate === 'asking'}
							<span class="inline-flex h-9 items-center gap-2 text-sm">
								<button class="underline" style:color="var(--critical-text)" onclick={updateFritz}>
									Yes, it restarts
								</button>
								<button class="underline" onclick={() => (fritzUpdate = null)}>No</button>
							</span>
						{/if}
					{/if}
					{#if fritzNote}<span class="self-center text-xs" style:color="var(--critical-text)">
							{fritzNote}
						</span>{/if}
				{/if}
				{#if device.act && signedAdmin}
					<HostReboot device={device.id} wanted={!!device.host_report?.updates?.reboot} />
				{/if}
				{#if device.openwrt && device.router && signedAdmin}
					<RouterReboot device={device.id} />
					{#if device.router.firmware?.next_step}
						<button
							class="btn-header"
							title="To OpenWrt {device.router.firmware.next_step}"
							onclick={() => (firmwareOpen = true)}
						>
							<CircleFadingArrowUp class="size-4 text-muted" /> Update firmware
						</button>
					{/if}
					{#if firmwareOpen && device.router.firmware}
						<FirmwareDialog
							device={device.id}
							advice={device.router.firmware}
							model={device.router.board.model}
							onclose={() => (firmwareOpen = false)}
						/>
					{/if}
				{/if}
				{#if admin}
					{@const mutedUntil = store.alarms?.muted[device.id]}
					<button
						onclick={async () => {
							await api.mute(device.id, mutedUntil ? 0 : 60);
							store.refreshHistory();
						}}
						class="btn-header"
						title={mutedUntil ? `No alarms until ${dayClock(mutedUntil)}` : 'No alarms for an hour'}
					>
						{#if mutedUntil}<BellOff class="size-4 text-muted" /> Muted{:else}<Bell
								class="size-4 text-muted"
							/> Mute 1 h{/if}
					</button>
					<MaintenanceButton kind="devices" id={device.id} devices={[device.id]} />
				{/if}
			{/snippet}
			{#snippet tabs()}
				{#if admin}
					<Tabs
						tabs={[
							{ id: 'status', label: 'Status' },
							...(device.host ? [{ id: 'setup' as const, label: 'Setup' }] : []),
							{ id: 'config', label: 'Config' }
						]}
						current={tab}
						onselect={(t) => show(t)}
					/>
				{/if}
			{/snippet}
		</PageHeader>

		{#if tab === 'config'}
			{#if store.editBlocked !== null}
				<div class="card p-5 text-sm text-muted">
					Its config can't be changed from here: {store.editBlocked}.
				</div>
			{:else}
				<div class="flex flex-wrap items-center gap-3">
					<Tabs
						tabs={[
							{ id: 'form', label: 'Form' },
							{ id: 'yaml', label: 'YAML', mono: true }
						]}
						current={view}
						onselect={(v) => show('config', v)}
					/>
					<p class="text-xs text-muted">
						{view === 'form'
							? 'Name, address, kind, tags and services; the rest stays as it is in config.yaml.'
							: 'Its part of config.yaml, comments and all.'}
					</p>
				</div>
				{#if view === 'form'}
					<div class="mt-4"><DeviceEditor id={device.id} onclose={() => show('status')} /></div>
				{:else}
					<div class="mt-4"><ConfigFile device={device.id} /></div>
				{/if}
			{/if}
		{:else if tab === 'setup'}
			{#if store.me}
				<p class="mb-4 max-w-2xl text-sm text-muted">
					How Kastellan reaches this device and what it may do there. Each part is set up once, with
					your login (used for that one connection) or with commands to run yourself.
				</p>
				{#if device.outdated_scripts.length}
					<p class="mb-3 text-sm" style:color="var(--warning-text)">
						Kastellan's {device.outdated_scripts.map(scriptName).join(' and ')} here {device
							.outdated_scripts.length === 1
							? 'is'
							: 'are'} outdated. Renew the host or OpenWrt setup below.
						{#if device.outdated_scripts.some((n) => n.startsWith('vpn-'))}
							Renew VPN helpers in <a class="underline" href="/vpn?tab=setup">VPN → Setup</a>.
						{/if}
					</p>
				{/if}
				{@const hostLike = !device.openwrt && HOSTS.includes(device.kind)}
				{@const fritzish = !!device.fritzbox || device.driver === 'fritzbox'}
				{@const parts = [
					{
						id: 'openwrt',
						shown:
							!!device.openwrt ||
							(!fritzish && ['router', 'access-point', 'switch'].includes(device.kind)),
						set: !!device.openwrt
					},
					{
						id: 'fritzbox',
						shown:
							fritzish || (!device.openwrt && ['router', 'access-point'].includes(device.kind)),
						set: !!device.fritzbox
					},
					{ id: 'wifi', shown: !!device.openwrt && !!device.router?.radios?.length, set: true },
					{ id: 'collector', shown: !!device.collect || hostLike, set: !!device.collect },
					{ id: 'actor', shown: !!device.act || hostLike, set: !!device.act },
					{ id: 'access', shown: true, set: !!device.ssh },
					{ id: 'syslog', shown: !device.openwrt && !fritzish, set: !!device.syslog_cert },
					{ id: 'keys', shown: !!device.ssh, set: true }
				].filter((p) => p.shown)}
				{#each [{ title: 'Set up', set: true }, { title: 'Can be set up', set: false }] as group (group.title)}
					{@const these = parts.filter((p) => p.set === group.set)}
					{#if these.length}
						<h2 class="mt-6 mb-3 text-sm font-semibold">{group.title}</h2>
						<div class="grid auto-rows-fr gap-4 sm:grid-cols-2 xl:grid-cols-3">
							{#each these as p (p.id)}
								{#if p.id === 'openwrt'}
									<OpenWrtSetup
										device={device.id}
										canInstall={signedAdmin}
										services={(device.router?.services ?? []).map((s) => s.name)}
										allowedServices={device.router?.services_allowed ?? []}
									/>
								{:else if p.id === 'fritzbox'}
									<FritzSetup device={device.id} canSetUp={signedAdmin} />
								{:else if p.id === 'wifi'}
									{@const names = (device.router?.radios ?? []).flatMap((r) =>
										r.interfaces.map((i) => i.ssid ?? i.section)
									)}
									<SetupCard
										title="Wi-Fi"
										about="Network names, passwords, radios on or off, channels. Applied with a rollback: undone if the device can't be reached afterwards."
										stage="set"
										note={[...new Set(names)].join(', ')}
										button="Change"
										onclick={() => (wifiOpen = true)}
									/>
								{:else if p.id === 'syslog'}
									<SyslogSetup device={device.id} canSetUp={signedAdmin} />
								{:else if p.id === 'collector'}
									<CollectorSetup device={device.id} canInstall={signedAdmin} />
								{:else if p.id === 'actor'}
									<ActorSetup
										device={device.id}
										containers={(device.containers ?? []).map((c) => c.name)}
										canInstall={signedAdmin}
									/>
								{:else if p.id === 'access'}
									<AccessSetup device={device.id} canSetUp={signedAdmin} />
								{:else}
									<DeviceKeys device={device.id} canChange={signedAdmin} />
								{/if}
							{/each}
						</div>
					{/if}
				{/each}
				{#if wifiOpen}
					<WifiDialog device={device.id} onclose={() => (wifiOpen = false)} />
				{/if}
			{/if}
		{:else}
			<DeviceAttention {issues} device={device.id} name={device.name} shown={sections} />
			<DeviceFacts {device} />

			<!-- What the device is for first (sectionsOf): the same order on every device of a kind,
			     empty sections left out. -->
			{#each sections as s (s)}
				{#if s === 'ups'}
					<Section title="Power" id="ups">
						<UpsCard {device} canChange={store.editBlocked === null} />
					</Section>
				{:else if s === 'internet' && device.fritz}
					<Section title="Internet" id="internet">
						{#snippet actions()}<a class="hover:text-text" href="/internet">
								Internet page
							</a>{/snippet}
						<RouterInternet fritz={device.fritz} />
					</Section>
				{:else if s === 'wifi'}
					<DeviceWifi {device} />
				{:else if s === 'network'}
					<div id="network" class="scroll-mt-6"><NetworkSection {device} /></div>
				{:else if s === 'vitals' && device.vitals}
					<VitalsPanel device={device.id} vitals={device.vitals} />
				{:else if s === 'storage' && device.host_report}
					<StorageSection
						device={device.id}
						report={device.host_report}
						forecast={device.host_forecast ?? {}}
					/>
				{:else if s === 'services'}
					<Section title="Services" id="services">
						{#snippet actions()}
							<RangePicker
								value={hours}
								options={ranges.map((r) => r.hours)}
								onchange={(h) => (hours = h)}
							/>
						{/snippet}
						{#if loadError}
							<p class="mb-3 text-sm" style:color="var(--critical-text)">
								Cannot load the history: {loadError}. Showing what was loaded before.
							</p>
						{/if}
						<ServicesTable
							rows={device.checks.map((check) => ({ device: device!, check }))}
							buckets={(_, name) => checkSeries[name] ?? []}
							start={series?.start ?? null}
							step={series?.step_seconds ?? 0}
							rangeLabel={range.label}
							own={own.byCheck}
							rest={own.rest}
							onDevice={device}
							canChange={canChangeOwn}
							ownBuckets={(name) => series?.services?.[name] ?? []}
						/>
					</Section>
				{:else if s === 'containers'}
					<Section
						title="Containers"
						id="containers"
						count={device.containers?.length}
						meta={containersMeta}
					>
						{#if device.containers}<ContainersTable
								device={device.id}
								containers={device.containers}
								checks={device.checks}
								stale={!!device.containers_error}
								canAct={!!device.act && signedAdmin}
							/>{/if}
					</Section>
				{:else if s === 'vm' && device.runs_on}
					{@const host = store.byId.get(device.runs_on.host)}
					{@const vm = host?.vms?.find((v) => v.name === device.runs_on?.vm)}
					{#if host && vm}
						<Section title="Its virtual machine" meta="on {host.name}" href="/devices/{host.id}">
							<VmsTable device={host.id} vms={[vm]} canAct={!!host.act && signedAdmin} />
						</Section>
					{/if}
				{:else if s === 'vms' && device.vms}
					<Section title="Virtual machines" id="vms" count={device.vms.length}>
						<VmsTable device={device.id} vms={device.vms} canAct={!!device.act && signedAdmin} />
					</Section>
				{:else if s === 'software'}
					<SoftwareSection {device} canAct={!!device.act && signedAdmin} />
				{:else if s === 'backups'}
					<BackupsCard device={device.id} />
					{#if admin}<div id="config-backups" class="scroll-mt-6">
							<ConfigBackups device={device.id} />
						</div>{/if}
				{:else if s === 'vulnerabilities' && device.vulns}
					<Section title="Vulnerabilities" id="vulnerabilities">
						<VulnsList vulns={device.vulns} exposed={device.exposed} />
					</Section>
				{:else if s === 'history'}
					<!-- Nothing checked (a UPS read through its host): no response time, only its activity. -->
					<div
						class="mt-section grid min-w-0 grid-cols-1 gap-8 {device.checks.length
							? 'xl:grid-cols-[minmax(0,1fr)_20rem]'
							: ''}"
					>
						{#if device.checks.length}<section class="min-w-0">
								<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
									<h2 class="text-section">Response time</h2>
									{#if device.checks.length > 1}
										<div class="flex flex-wrap gap-1">
											{#each device.checks as check (check.name)}
												<button
													onclick={() => (chosen = check.name)}
													class="h-7 rounded-lg border px-2.5 text-xs font-medium transition-colors {latencyCheck ===
													check.name
														? 'border-accent bg-accent-soft text-accent'
														: 'border-line text-muted hover:text-text'}"
												>
													{check.name}
												</button>
											{/each}
										</div>
									{/if}
								</div>
								<div class="card px-2 pt-4 pb-1">
									{#if series && latencyCheck && checkSeries[latencyCheck]}
										<LatencyChart
											buckets={checkSeries[latencyCheck]}
											start={series.start}
											step={series.step_seconds}
											height={220}
										/>
									{:else}
										<div class="h-[220px] animate-pulse rounded-lg bg-surface-2"></div>
									{/if}
								</div>
							</section>{/if}

						<section>
							<h2 class="mb-3 text-section">Activity</h2>
							<div class="card p-2">
								{#if changes.length === 0}
									<p class="px-3 py-6 text-center text-sm text-muted">
										No changes in the last {range.label}.
									</p>
								{:else}
									<ol class="max-h-[22rem] overflow-y-auto">
										{#each changes as change (`${change.kind}/${change.time}/${change.check}`)}
											<li><ActivityItem {change} {device} /></li>
										{/each}
									</ol>
								{/if}
							</div>

							{#if children.length}
								<h2 class="mt-8 mb-3 text-section">Connected through it</h2>
								<div class="card divide-y divide-line">
									{#each children as child (child.id)}
										<a
											href="/devices/{child.id}"
											class="flex items-center gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-surface-2/70"
										>
											<KindIcon kind={child.kind} class="size-4 text-muted" />
											<span class="flex-1 truncate">{child.name}</span>
											<StatusBadge status={statusOf(child)} />
										</a>
									{/each}
								</div>
							{/if}
						</section>
					</div>
				{/if}
			{/each}
		{/if}
	{/key}
{/if}

{#snippet containersMeta()}
	{#if device}
		{#if device.containers_error}
			<span class="inline-flex items-center gap-1.5">
				<StateDot tone="warning" label="Not read" />
				{device.containers_error}
			</span>
			{#if device.containers_checked}
				<span>last read {ago(device.containers_checked, store.now)}</span>
			{/if}
		{:else}
			<span>
				from {device.host_report?.docker ? 'the collector' : 'WUD'}, {ago(
					device.containers_checked,
					store.now
				)}
			</span>
		{/if}
		{#if device.collect && device.host_report?.docker}<ReadAgain device={device.id} />{/if}
	{/if}
{/snippet}
