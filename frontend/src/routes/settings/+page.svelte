<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { store } from '#lib/state.svelte.js';
	import { settings } from '#lib/settings.svelte.js';
	import SettingsForms from '#lib/components/SettingsForms.svelte';
	import ConfigFile from '#lib/components/ConfigFile.svelte';
	import AccessPanel from '#lib/components/AccessPanel.svelte';
	import Tabs from '#lib/components/Tabs.svelte';

	/** Kastellan's settings: as forms, its own SSH key (Access), or config.yaml itself. */
	type Tab = 'settings' | 'access' | 'file';
	const tab = $derived.by((): Tab => {
		const t = page.url.searchParams.get('tab');
		return t === 'file' || t === 'access' ? t : 'settings';
	});

	function show(t: Tab) {
		if (t === 'settings') settings.load(); // the file may have changed in the other tab
		goto(t === 'settings' ? '?' : `?tab=${t}`, { replaceState: true });
	}
</script>

<svelte:head><title>Settings · Kastellan</title></svelte:head>

<h1 class="text-2xl font-semibold tracking-tight">Settings</h1>
<p class="mt-1 max-w-2xl text-sm text-muted">
	Kastellan's own settings. Devices are set on their pages, groups on Groups, the VPN on VPN.
</p>

<div class="mt-5">
	<Tabs
		tabs={[
			{ id: 'settings', label: 'Settings' },
			{ id: 'access', label: 'Access' },
			{ id: 'file', label: 'config.yaml', mono: true }
		]}
		current={tab}
		onselect={show}
	/>
</div>

{#if tab === 'access'}
	<AccessPanel />
{:else if store.editBlocked !== null}
	<div class="card mt-6 p-5 text-sm text-muted">
		The config can't be changed from here: {store.editBlocked}.
	</div>
{:else if tab === 'settings'}
	<SettingsForms />
{:else}
	<div class="mt-6"><ConfigFile /></div>
{/if}
