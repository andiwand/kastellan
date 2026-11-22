<script lang="ts">
	import { api } from '#lib/api.js';
	import { actionsOf, type OwnService } from '#lib/ownServices.js';
	import LogsDialog from './LogsDialog.svelte';

	/** One of the device's own services: its state, and what may be done (an OpenWrt service on
	 * Kastellan's list: start, stop, restart, at boot; a failed systemd unit: its log). */
	let {
		device,
		service,
		canChange = false,
		named = true
	}: { device: string; service: OwnService; canChange?: boolean; named?: boolean } = $props();

	let busy = $state<string | null>(null);
	let note = $state<{ ok: boolean; text: string } | null>(null);
	let logs = $state(false);

	const color = $derived(
		service.state === 'running'
			? 'var(--good)'
			: service.state === 'failed'
				? 'var(--critical)'
				: 'var(--line-strong)'
	);

	async function act(action: string) {
		busy = action;
		note = null;
		try {
			await api.openwrtService(device, service.name, action);
			note = { ok: true, text: `${action}: done; it shows within two minutes` };
		} catch (e) {
			note = { ok: false, text: (e as Error).message };
		} finally {
			busy = null;
		}
	}
</script>

<span class="inline-flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
	<span class="size-1.5 shrink-0 rounded-full" style:background={color}></span>
	{#if named}<span class="font-mono">{service.name}</span>{/if}
	<span
		class="text-muted"
		style:color={service.state === 'failed' ? 'var(--critical-text)' : undefined}
	>
		{service.state}{service.enabled === false ? ' · not at boot' : ''}
	</span>
	{#if canChange && service.source === 'openwrt' && service.actable}
		{#each actionsOf(service) as action (action)}
			<button
				class="text-muted underline decoration-line underline-offset-2 hover:text-text disabled:opacity-50"
				disabled={busy !== null}
				onclick={() => act(action)}
			>
				{busy === action
					? '…'
					: action === 'enable'
						? 'on at boot'
						: action === 'disable'
							? 'off at boot'
							: action}
			</button>
		{/each}
	{/if}
	{#if canChange && service.source === 'systemd'}
		<button
			class="text-muted underline decoration-line underline-offset-2 hover:text-text"
			onclick={() => (logs = true)}
		>
			log
		</button>
	{/if}
	{#if note}<span style:color={note.ok ? 'var(--good-text)' : 'var(--critical-text)'}>
			{note.text}
		</span>{/if}
</span>

{#if logs}
	<LogsDialog {device} what={{ unit: service.name }} onclose={() => (logs = false)} />
{/if}
