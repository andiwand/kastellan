<script lang="ts">
	import { api } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';

	/** Read a host's collector now (its health, containers, VMs), instead of at the next minute,
		and ask the registries anew whether its containers have updates. */
	let { device }: { device: string } = $props();
	let busy = $state(false);
	let error = $state<string | null>(null);

	async function read() {
		busy = true;
		error = null;
		try {
			await api.collectNow(device);
			await store.refresh();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}
</script>

{#if store.me?.role === 'admin'}
	<!-- Inline (not flex): its baseline is its text's, so it sits level with the words around it. -->
	<button
		class="inline text-xs text-muted hover:text-text disabled:opacity-60"
		onclick={read}
		disabled={busy}
		title={error ?? 'Read the host now, and ask the registries for updates'}
	>
		<RefreshCw class="mr-0.5 inline size-3 align-[-0.125em] {busy ? 'animate-spin' : ''}" />
		{busy ? 'reading…' : error ? "couldn't read" : 'read again'}
	</button>
{/if}
