<script lang="ts">
	import { statusLabel, type Status } from '#lib/status.js';
	import { statusTone } from '#lib/states.js';
	import StateBadge from './ui/StateBadge.svelte';

	/** A device's or a check's status as the State badge (`label`: another word for the same
	 * state). */
	let { status, label }: { status: Status; label?: string } = $props();
</script>

<StateBadge
	word={label ?? statusLabel[status]}
	tone={statusTone[status]}
	pulse={status === 'down' || status === 'degraded'}
/>
