<script lang="ts">
	import { api, more } from '#lib/api.js';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';

	/** Reboot an OpenWrt device (or, with `kind`, a FRITZ!Box) through its API, from the top of
	 * its page (next to Terminal): asked once more, then said how it went. */
	let { device, kind = 'openwrt' }: { device: string; kind?: 'openwrt' | 'fritzbox' } = $props();
	let asking = $state(false);
	let note = $state<{ ok: boolean; text: string } | null>(null);

	async function reboot() {
		asking = false;
		try {
			await (kind === 'fritzbox' ? more.fritzReboot(device) : api.openwrtReboot(device));
			note = { ok: true, text: 'Rebooting; back in a minute or two.' };
		} catch (e) {
			note = { ok: false, text: (e as Error).message };
		}
	}
</script>

{#if asking}
	<span class="btn-header hover:border-line">
		Reboot it?
		<button class="underline" style:color="var(--critical-text)" onclick={reboot}>Yes</button>
		<button class="underline" onclick={() => (asking = false)}>No</button>
	</span>
{:else}
	<button
		class="btn-header"
		title={note?.text ?? 'Restart the device'}
		onclick={() => ((asking = true), (note = null))}
	>
		<RotateCcw class="size-4 text-muted" />
		{#if note}<span style:color={note.ok ? 'var(--good-text)' : 'var(--critical-text)'}>
				{note.ok ? 'Rebooting…' : 'Reboot failed'}
			</span>{:else}Reboot{/if}
	</button>
{/if}
