<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { store } from '#lib/state.svelte.js';
	import { settings } from '#lib/settings.svelte.js';
	import SettingsForms from '#lib/components/SettingsForms.svelte';
	import ConfigFile from '#lib/components/ConfigFile.svelte';
	import AccessPanel from '#lib/components/AccessPanel.svelte';
	import WebSetup from '#lib/components/WebSetup.svelte';
	import Tabs from '#lib/components/Tabs.svelte';
	import HubResources from '#lib/components/HubResources.svelte';
	import KeysPanel from '#lib/components/KeysPanel.svelte';
	import PageHeader from '#lib/components/ui/PageHeader.svelte';

	/** Kastellan's settings: as forms, its own SSH key (Access), the key rings (Keys; /keys
	 * redirects here), what it costs the machine (Resources), or config.yaml itself. */
	type Tab = 'settings' | 'access' | 'keys' | 'web' | 'resources' | 'file';
	const TABS: Tab[] = ['settings', 'access', 'keys', 'web', 'resources', 'file'];
	const tab = $derived.by((): Tab => {
		const t = page.url.searchParams.get('tab') as Tab | null;
		return t && TABS.includes(t) ? t : 'settings';
	});

	function show(t: Tab) {
		if (t === 'settings') settings.load(); // the file may have changed in the other tab
		goto(t === 'settings' ? '?' : `?tab=${t}`, { replace: true });
	}
</script>

<svelte:head><title>Settings · Kastellan</title></svelte:head>

<PageHeader
	title="Settings"
	summary="Kastellan's own settings. Devices are set on their pages, groups on Groups, the VPN on VPN."
/>
<!-- The tabs sit outside the header: each tab's content brings its own gap above. -->
<Tabs
	tabs={[
		{ id: 'settings', label: 'Settings' },
		{ id: 'access', label: 'Access' },
		{ id: 'keys', label: 'Keys' },
		{ id: 'web', label: 'Names & HTTPS' },
		{ id: 'resources', label: 'Resources' },
		{ id: 'file', label: 'config.yaml', mono: true }
	]}
	current={tab}
	onselect={show}
/>

{#if tab === 'access'}
	<AccessPanel />
{:else if tab === 'keys'}
	<KeysPanel />
{:else if tab === 'web'}
	<WebSetup />
{:else if tab === 'resources'}
	<HubResources />
{:else if store.editBlocked !== null}
	<div class="card mt-6 p-5 text-sm text-muted">
		The config can't be changed from here: {store.editBlocked}.
	</div>
{:else if tab === 'settings'}
	<SettingsForms />
{:else}
	<div class="mt-6"><ConfigFile /></div>
{/if}
