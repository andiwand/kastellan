<script lang="ts">
	let { size = 28 }: { size?: number } = $props();
	// Each logo needs its own gradient id: a hidden copy's id would leave the others unfilled.
	const id = $props.id();
</script>

<!-- A keep: the tower that watches over the castle. -->
<svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
	<defs>
		<linearGradient id="logo-{id}" x1="0" y1="0" x2="1" y2="1">
			<stop offset="0" stop-color="#5b7cff" />
			<stop offset="1" stop-color="#3349e0" />
		</linearGradient>
	</defs>
	<rect width="32" height="32" rx="9" fill="url(#logo-{id})" />
	<path d="M9.5 25V12.5h2.6V9.5h2.6v3h2.6v-3h2.6v3h2.6V25Z" fill="#fff" />
	<path d="M14.4 25v-3.4a1.6 1.6 0 0 1 3.2 0V25Z" fill="#3a52e8" />
</svg>
