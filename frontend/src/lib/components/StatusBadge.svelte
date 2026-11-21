<script lang="ts">
	import { statusColor, statusLabel, type Status } from '#lib/status.js';

	let { status }: { status: Status } = $props();
	const color = $derived(statusColor[status]);
</script>

<span
	class="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-line bg-surface-2 py-0.5 pr-2.5 pl-2 text-xs font-medium"
	style:color={color.text}
>
	<span
		class="size-1.5 rounded-full"
		class:pulse={status === 'down' || status === 'degraded'}
		style:background={color.fill}
		style:color={color.fill}
	></span>
	{statusLabel[status]}
</span>
