<script lang="ts">
	import Tabs from '../Tabs.svelte';
	import { hoursLabel } from '#lib/format.js';

	/** The one range picker: 1 h · 24 h · 7 d · 30 d, or the subset a page has data for (Logs
	 * adds 0: All). The value is in hours. Put it in the PageHeader's controls or a Section's
	 * actions, once per page. */
	let {
		value,
		onchange,
		options = [1, 24, 168, 720]
	}: { value: number; onchange: (hours: number) => void; options?: number[] } = $props();
</script>

<Tabs
	small
	label="Time range"
	tabs={options.map((h) => ({ id: h, label: hoursLabel(h) }))}
	current={value}
	onselect={onchange}
/>
