<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { store } from '#lib/state.svelte.js';
	import { settings } from '#lib/settings.svelte.js';
	import SettingsForms from '#lib/components/SettingsForms.svelte';
	import ConfigFile from '#lib/components/ConfigFile.svelte';

	/** The settings: as forms, or config.yaml itself (second tab). */
	const tab = $derived(page.url.searchParams.get('tab') === 'file' ? 'file' : 'settings');

	function show(t: 'settings' | 'file') {
		if (t === 'settings') settings.load(); // the file may have changed in the other tab
		goto(t === 'file' ? '?tab=file' : '?', { replaceState: true });
	}
</script>

<svelte:head><title>Config · Kastellan</title></svelte:head>

<h1 class="text-2xl font-semibold tracking-tight">Config</h1>
<p class="mt-1 max-w-2xl text-sm text-muted">
	Kastellan's settings. Devices are set on their pages, groups on Groups, the VPN on VPN.
</p>

{#if store.editBlocked !== null}
	<div class="card mt-6 p-5 text-sm text-muted">
		The config can't be changed from here: {store.editBlocked}.
	</div>
{:else}
	<div
		class="mt-5 inline-flex gap-1 rounded-xl border border-line bg-surface p-1 shadow-card"
		role="tablist"
	>
		{#each [['settings', 'Settings'], ['file', 'config.yaml']] as [id, label] (id)}
			<button
				role="tab"
				aria-selected={tab === id}
				class="h-8 rounded-lg px-3.5 text-sm font-medium {tab === id
					? 'bg-surface-3 text-text'
					: 'text-muted hover:text-text'} {id === 'file' ? 'font-mono text-[13px]' : ''}"
				onclick={() => show(id as 'settings' | 'file')}
			>
				{label}
			</button>
		{/each}
	</div>

	{#if tab === 'settings'}
		<SettingsForms />
	{:else}
		<div class="mt-6"><ConfigFile /></div>
	{/if}
{/if}
