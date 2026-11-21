<script lang="ts">
	import DhcpPool from './DhcpPool.svelte';
	import RoamingInfo from './RoamingInfo.svelte';
	import { api, type RouterReport } from '#lib/api.js';
	import { ago, duration } from '#lib/format.js';
	import { store } from '#lib/state.svelte.js';

	/** An OpenWrt device through its own API: system, Wi-Fi and who is connected, package
	 * updates, firmware advice; services where the device allows. Reboot and Update firmware sit at the
	 * top of the page, Wi-Fi settings under Setup. */
	let {
		device,
		report,
		checked,
		error,
		canChange
	}: {
		device: string;
		report: RouterReport;
		checked: string | null;
		error: string | null;
		canChange: boolean;
	} = $props();

	let note = $state<{ ok: boolean; text: string } | null>(null);

	const release = $derived(report.board.release);
	const fw = $derived(report.firmware);
	const clients = $derived(
		(report.radios ?? []).reduce(
			(n, r) => n + r.interfaces.reduce((m, i) => m + i.clients.length, 0),
			0
		)
	);
	const mem = $derived(report.system.memory);

	let busy = $state<string | null>(null);
	const allowed = $derived(new Set(report.services_allowed ?? []));
	// Services worth seeing: running ones, enabled ones, and the ones Kastellan may act on.
	const services = $derived(
		(report.services ?? []).filter((s) => s.running || s.enabled || allowed.has(s.name))
	);

	async function act(name: string, action: string) {
		busy = `${name}:${action}`;
		try {
			await api.openwrtService(device, name, action);
			note = { ok: true, text: `${name}: ${action} done; the list updates within two minutes.` };
		} catch (e) {
			note = { ok: false, text: `${name}: ${(e as Error).message}` };
		} finally {
			busy = null;
		}
	}

	// "name - 1.0 - 1.1" (opkg) or "name-1.1-r0 arch {origin} ... [upgradable from: name-1.0-r0]" (apk)
	function packageName(line: string): string {
		const first = line.split(' ')[0];
		return line.includes(' - ') ? first : first.replace(/-\d[^-]*-r\d+$/, '');
	}
</script>

<section class="card mt-8 p-5">
	<div class="flex flex-wrap items-baseline justify-between gap-2">
		<h2 class="text-sm font-semibold">OpenWrt</h2>
		<p class="text-xs text-muted">
			{#if error}<span style:color="var(--warning-text)">{error}</span> ·
			{/if}through its API{checked ? `, ${ago(checked, store.now)}` : ''}
		</p>
	</div>

	<dl class="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
		<div>
			<dt class="text-xs text-muted">Model</dt>
			<dd class="truncate" title={report.board.board_name ?? ''}>{report.board.model ?? '–'}</dd>
		</div>
		<div>
			<dt class="text-xs text-muted">OpenWrt</dt>
			<dd>
				{release.version ?? '–'}
				{#if fw && !fw.supported}<span class="text-xs" style:color="var(--warning-text)">
						· no fixes any more
					</span>{/if}
			</dd>
		</div>
		<div>
			<dt class="text-xs text-muted">Up</dt>
			<dd class="tabular">{report.system.uptime != null ? duration(report.system.uptime) : '–'}</dd>
		</div>
		<div>
			<dt class="text-xs text-muted">Load · memory free</dt>
			<dd class="tabular">
				{report.system.load.map((l) => l.toFixed(2)).join(' · ')}
				{#if mem.total}· {Math.round((100 * (mem.available ?? mem.free ?? 0)) / mem.total)}%{/if}
			</dd>
		</div>
	</dl>

	{#if fw}
		<p class="mt-4 text-sm">
			{#if fw.next_step}
				<span style:color={fw.supported ? undefined : 'var(--warning-text)'}>
					{fw.supported ? 'A newer release:' : `OpenWrt ${fw.series} gets no more security fixes;`}
					next step {fw.next_step}
				</span>
				<span class="text-xs text-muted">(supported: {fw.stable} and {fw.oldstable})</span>
			{:else}
				<span style:color="var(--good-text)">On the newest release.</span>
			{/if}
		</p>
	{/if}

	{#if report.radios?.length}
		<h3 class="mt-5 text-xs font-medium text-muted">
			Wi-Fi · {clients}
			{clients === 1 ? 'client' : 'clients'}
		</h3>
		<ul class="mt-2 space-y-2 text-sm">
			{#each report.radios as radio (radio.name)}
				<li>
					<span class="font-mono text-xs">{radio.name}</span>
					<span class="text-muted">{radio.band ?? ''} · channel {radio.channel ?? 'auto'}</span>
					{#if radio.disabled}<span class="text-faint"> · off</span>{/if}
					<ul class="mt-1 ml-4 space-y-0.5">
						{#each radio.interfaces as iface (iface.section)}
							<li>
								<span class="font-medium">{iface.ssid ?? iface.section}</span>
								<span class="text-xs text-muted">{iface.mode}</span>
								{#if iface.clients.length}
									<span class="text-xs text-muted">
										· {iface.clients
											.map(
												(c) => `${c.name ?? c.mac}${c.signal != null ? ` (${c.signal} dBm)` : ''}`
											)
											.join(', ')}
									</span>
								{/if}
							</li>
						{/each}
					</ul>
				</li>
			{/each}
		</ul>
	{/if}

	{#if report.dhcp}
		<h3 class="mt-5 text-xs font-medium text-muted">DHCP</h3>
		<DhcpPool pool={report.dhcp} />
	{/if}

	{#if store.me?.role === 'admin'}<RoamingInfo {device} />{/if}

	{#if services.length}
		<h3 class="mt-5 text-xs font-medium text-muted">Services</h3>
		<ul class="mt-2 grid gap-x-6 text-sm sm:grid-cols-2">
			{#each services as svc (svc.name)}
				<li class="flex min-h-8 items-center gap-2">
					<span
						class="size-2 shrink-0 rounded-full"
						style:background={svc.running ? 'var(--good)' : 'var(--line-strong)'}
						title={svc.running ? 'running' : 'not running'}
					></span>
					<span class="font-mono text-xs">{svc.name}</span>
					<span class="text-xs text-muted">
						{svc.running ? 'running' : 'stopped'}{svc.enabled ? '' : ' · not at boot'}
					</span>
					{#if canChange && allowed.has(svc.name)}
						<span class="ml-auto flex gap-2 text-xs">
							{#each svc.running ? ['restart', 'stop'] : ['start'] as action (action)}
								<button
									class="underline decoration-line underline-offset-2 hover:decoration-current disabled:opacity-50"
									disabled={busy !== null}
									onclick={() => act(svc.name, action)}
								>
									{busy === `${svc.name}:${action}` ? '…' : action}
								</button>
							{/each}
							<button
								class="underline decoration-line underline-offset-2 hover:decoration-current disabled:opacity-50"
								disabled={busy !== null}
								onclick={() => act(svc.name, svc.enabled ? 'disable' : 'enable')}
							>
								{svc.enabled ? 'off at boot' : 'on at boot'}
							</button>
						</span>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	<div class="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
		<p>
			<span class="text-xs text-muted">Package updates</span><br />
			{report.packages == null
				? 'not looked yet'
				: report.packages.length === 0
					? 'none'
					: `${report.packages.length}: ${report.packages
							.slice(0, 5)
							.map(packageName)
							.join(', ')}${report.packages.length > 5 ? ', …' : ''}`}
		</p>
	</div>
	{#if note}
		<p class="mt-2 text-xs" style:color={note.ok ? 'var(--good-text)' : 'var(--critical-text)'}>
			{note.text}
		</p>
	{/if}
</section>
