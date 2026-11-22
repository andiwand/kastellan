<script lang="ts">
	import { statusLabel, type Status } from '#lib/status.js';
	import { statusTone } from '#lib/states.js';
	import StateDot from './ui/StateDot.svelte';

	let { status, size = 'size-2.5' }: { status: Status; size?: string } = $props();
</script>

<StateDot
	tone={statusTone[status]}
	label={statusLabel[status]}
	{size}
	pulse={status === 'down' || status === 'degraded'}
/>
