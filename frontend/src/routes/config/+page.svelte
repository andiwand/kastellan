<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { store } from '#lib/state.svelte.js';
	import { settings } from '#lib/settings.svelte.js';
	import SettingsForms from '#lib/components/SettingsForms.svelte';
	import ConfigFile from '#lib/components/ConfigFile.svelte';
	import Tabs from '#lib/components/Tabs.svelte';

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
	<div class="mt-5">
		<Tabs
			tabs={[
				{ id: 'settings', label: 'Settings' },
				{ id: 'file', label: 'config.yaml', mono: true }
			]}
			current={tab}
			onselect={show}
		/>
	</div>

	{#if tab === 'settings'}
		<SettingsForms />
	{:else}
		<div class="mt-6"><ConfigFile /></div>
	{/if}
{/if}
