<script lang="ts">
	import { toneColor, type Tone } from '#lib/states.js';

	/** A state's dot alone, where the word is said next to it or in its title (a list row's
	 * sentence, a chip). `label` is read out and shown on hover. */
	let {
		tone,
		label,
		size = 'size-2',
		pulse = false
	}: { tone: Tone; label: string; size?: string; pulse?: boolean } = $props();
</script>

<span
	class="inline-block shrink-0 rounded-full {size}"
	class:pulse
	style:background={toneColor[tone].fill}
	style:color={toneColor[tone].fill}
	title={label}
	role="img"
	aria-label={label}
></span>
