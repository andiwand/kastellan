<script lang="ts">
	import { api } from '#lib/api.js';
	import { actionsOf, type OwnService } from '#lib/ownServices.js';
	import LogsDialog from './LogsDialog.svelte';

	/** What may be done to one of the device's own services (an OpenWrt service on Kastellan's
	 * list: start, stop, restart, at boot; a failed systemd unit: its log), and how it went. Its
	 * state is the row's (ServicesTable). */
	let {
		device,
		service,
		canChange = false
	}: { device: string; service: OwnService; canChange?: boolean } = $props();

	let busy = $state<string | null>(null);
	let note = $state<{ ok: boolean; text: string } | null>(null);
	let logs = $state(false);

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

<span class="inline-flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-xs empty:hidden">
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
