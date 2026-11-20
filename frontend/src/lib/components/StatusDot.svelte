<script lang="ts">
	import { statusColor, statusLabel, type Status } from '#lib/status.js';

	let { status, size = 'size-2.5' }: { status: Status; size?: string } = $props();
</script>

<span
	class="inline-block shrink-0 rounded-full {size}"
	class:pulse={status === 'down' || status === 'degraded'}
	style:background={statusColor[status].fill}
	style:color={statusColor[status].fill}
	title={statusLabel[status]}
	role="img"
	aria-label={statusLabel[status]}
></span>
