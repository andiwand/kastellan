<script lang="ts">
	import Router from '@lucide/svelte/icons/router';
	import Wifi from '@lucide/svelte/icons/wifi';
	import Network from '@lucide/svelte/icons/network';
	import Server from '@lucide/svelte/icons/server';
	import Monitor from '@lucide/svelte/icons/monitor';
	import Laptop from '@lucide/svelte/icons/laptop';
	import Printer from '@lucide/svelte/icons/printer';
	import Cctv from '@lucide/svelte/icons/cctv';
	import Box from '@lucide/svelte/icons/box';

	let { kind, class: klass = 'size-4' }: { kind: string; class?: string } = $props();

	const icons = {
		router: Router,
		'access-point': Wifi,
		switch: Network,
		server: Server,
		pc: Monitor,
		laptop: Laptop,
		printer: Printer,
		camera: Cctv
	} as Record<string, typeof Box>;
	const Icon = $derived(icons[kind] ?? Box);
</script>

<Icon class={klass} strokeWidth={1.75} />
