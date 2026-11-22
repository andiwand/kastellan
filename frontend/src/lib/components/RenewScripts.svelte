<script lang="ts">
	import { api } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { onMount } from 'svelte';
	import SetupDialog from './SetupDialog.svelte';

	/** Renewing Kastellan's collector and/or actor on a host, or its OpenWrt login, with this
	 * hub's versions, keeping what each allows now: one login for each (the hub's keys may run
	 * these scripts, never replace them). The collector first, then the actor. */
	let { device, names, onclose }: { device: string; names: string[]; onclose: () => void } =
		$props();

	type Part = {
		what: string;
		title: string;
		intro: string;
		path: string;
		body?: Record<string, unknown>;
		script: string;
		port: number;
		needsRoot?: boolean; // false: root's own login (OpenWrt), no sudo
		defaultLogin?: string;
	};
	let parts = $state<Part[]>([]);
	let error = $state<string | null>(null);
	let succeeded = $state(false);
	const canRun = $derived(!!store.me?.user);

	onMount(async () => {
		const enc = encodeURIComponent(device);
		try {
			const out: Part[] = [];
			if (names.includes('collector')) {
				const s = await api.collectSetup(device);
				out.push({
					what: 'collector',
					title: 'Renew the collector',
					intro: 'Install the current collector as root using your login.',
					path: `/devices/${enc}/collect/install`,
					script: s.script,
					port: s.port
				});
			}
			if (names.includes('actor')) {
				const current = await api.actScript(device, [], [], []);
				if (current.allowed == null)
					throw new Error(
						current.error ||
							'Cannot read the actor’s permissions. Try again when the host is reachable.'
					);
				const allowed = current.allowed;
				const abilities = Object.keys(allowed);
				const s = await api.actScript(device, abilities, [], [], allowed);
				out.push({
					what: 'actor',
					title: 'Renew the actor',
					intro: `Install the current actor as root, keeping these permissions: ${
						abilities.join(', ') || 'nothing but listing'
					}. Enable new abilities in Setup.`,
					path: `/devices/${enc}/act/install`,
					body: { abilities, allowed, renew: true },
					script: s.script,
					port: s.port
				});
			}
			if (names.includes('openwrt')) {
				const current = await api.openwrtScript(device, []);
				if (current.allowed == null)
					throw new Error(
						"Cannot read what Kastellan's login there may do. Try again when the device is reachable."
					);
				const abilities = current.allowed;
				if (abilities.includes('services') && current.services == null)
					throw new Error(
						'Cannot read the service permissions. Try again when the device is reachable.'
					);
				const services = current.services ?? [];
				const s = await api.openwrtScript(device, abilities, services);
				out.push({
					what: 'openwrt',
					title: 'Renew the OpenWrt login',
					intro: `Install the current helpers over SSH as root, keeping these permissions: ${['reading', ...abilities].join(', ')}. Enable new abilities in Setup.`,
					path: `/devices/${enc}/openwrt/install`,
					body: { abilities, services, renew: true },
					script: s.script,
					port: s.port,
					needsRoot: false,
					defaultLogin: 'root'
				});
			}
			parts = out;
		} catch (e) {
			error = (e as Error).message;
		}
	});
	const current = $derived(parts[0]);
</script>

{#if error}
	<p class="text-xs" style:color="var(--critical-text)">{error}</p>
	<button class="btn btn-sm" onclick={onclose}>Close</button>
{:else if current}
	{#key current.what}
		<SetupDialog
			title={current.title}
			intro={current.intro}
			path={current.path}
			body={current.body}
			script={current.script}
			port={current.port}
			needsRoot={current.needsRoot ?? true}
			defaultLogin={current.defaultLogin ?? ''}
			{canRun}
			onclose={() => {
				if (!succeeded) return onclose();
				succeeded = false;
				parts = parts.slice(1);
				if (!parts.length) onclose();
			}}
			ondone={async (o) => {
				succeeded = o.ok;
				if (o.ok) await store.refresh();
			}}
		/>
	{/key}
{/if}
