<script lang="ts">
	import StateBadge from './ui/StateBadge.svelte';
	import { updateState } from '#lib/states.js';
	import type { Device } from '#lib/api.js';
	import { runner, type Job } from '#lib/runner.svelte.js';
	import JobStatus from './JobStatus.svelte';

	/** What the device runs and what is waiting for it: OpenWrt's release and the next step (its
	 * packages come with it), FRITZ!OS and its update, a Linux host's system updates (Install
	 * through the actor) and whether it wants a reboot. Firmware updates and Reboot sit at the
	 * top of the page. */
	let { device, canAct = false }: { device: Device; canAct?: boolean } = $props();

	const fw = $derived(device.router?.firmware);
	const release = $derived(device.router?.board.release);
	const packages = $derived(device.router?.packages ?? null);
	const updates = $derived(device.host_report?.updates);
	const fritz = $derived(device.fritz);

	const upgrade = $derived<Job>({ device: device.id, kind: 'packages' });
	const busy = (j: Job) => ['queued', 'running'].includes(runner.state(j)?.state ?? '');
	let asking = $state(false);

	// "name - 1.0 - 1.1" (opkg) or "name-1.1-r0 arch {origin} ... [upgradable from: name-1.0-r0]" (apk)
	function packageName(line: string): string {
		const first = line.split(' ')[0];
		return line.includes(' - ') ? first : first.replace(/-\d[^-]*-r\d+$/, '');
	}
	const shown = $derived(!!device.router || !!fritz || !!updates);
</script>

{#if shown}
	<section class="mt-8 scroll-mt-6" id="software">
		<h2 class="text-sm font-semibold">Software</h2>
		<ul class="card mt-3 divide-y divide-line text-sm">
			{#if device.router}
				<li class="flex flex-wrap items-baseline gap-x-2 gap-y-1 px-5 py-3">
					<span class="font-medium">OpenWrt {release?.version ?? ''}</span>
					{#if fw?.next_step}
						<StateBadge
							state={fw.supported ? updateState.available : updateState.unsupported}
							quiet
						/>
						<span>
							{fw.supported
								? `${fw.next_step} is out`
								: `${fw.series} gets no more security fixes; next step ${fw.next_step}`}
						</span>
						<span class="text-xs text-muted">(supported: {fw.stable} and {fw.oldstable})</span>
					{:else if fw?.supported}
						<StateBadge state={updateState.current} quiet />
					{:else if fw}
						<StateBadge
							word="Not listed"
							tone="warning"
							title="Not among the supported releases"
							quiet
						/>
					{/if}
					<span class="w-full text-xs text-muted">
						{#if device.router.packages_skipped}
							Packages: not asked ({device.router.packages_skipped}).
						{:else if device.router.errors?.packages}
							Package updates unknown: the last check failed.
						{:else if packages == null}
							Packages: not checked yet.
						{:else if packages.length === 0}
							No package updates.
						{:else}
							{packages.length} package {packages.length === 1 ? 'update' : 'updates'}
							(come with the next firmware): {packages
								.slice(0, 6)
								.map(packageName)
								.join(', ')}{packages.length > 6 ? ', …' : ''}
						{/if}
					</span>
				</li>
			{/if}
			{#if fritz}
				<li class="flex flex-wrap items-baseline gap-x-2 px-5 py-3">
					<span class="font-medium">
						{device.info?.firmware ?? (fritz.firmware ? `FRITZ!OS ${fritz.firmware}` : 'FRITZ!OS')}
					</span>
					{#if fritz.update?.available}
						<StateBadge state={updateState.available} quiet />
						<span>{fritz.update.version ?? 'an update'} is out</span>
					{:else if fritz.update}
						<StateBadge state={updateState.current} quiet />
					{/if}
				</li>
			{/if}
			{#if updates}
				<li class="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-3">
					<span>
						System updates:
						{updates.available == null
							? 'unknown'
							: updates.available === 0
								? 'none pending'
								: `${updates.available} pending`}
						{#if updates.security}<span class="ml-1">
								<StateBadge
									word="{updates.security} for security"
									tone={updateState.security.tone}
									quiet
								/>
							</span>{/if}
						{#if updates.reboot}<span class="ml-1">
								<StateBadge
									word="Wants a reboot"
									tone="warning"
									title={canAct ? 'Reboot is at the top of the page' : undefined}
									quiet
								/>
							</span>{/if}
					</span>
					{#if canAct && updates.available}
						{#if asking}
							<span class="flex flex-wrap items-center gap-2 text-xs">
								Install them now (apt; keeps config files, removes nothing)?
								<button
									class="btn btn-sm btn-primary"
									onclick={() => {
										runner.add([upgrade]);
										asking = false;
									}}
								>
									Install
								</button>
								<button class="btn btn-sm" onclick={() => (asking = false)}>Not now</button>
							</span>
						{:else}
							<button class="btn btn-sm" disabled={busy(upgrade)} onclick={() => (asking = true)}>
								Install
							</button>
						{/if}
					{/if}
					<div class="w-full empty:hidden"><JobStatus job={runner.state(upgrade)} /></div>
				</li>
			{/if}
		</ul>
	</section>
{/if}
