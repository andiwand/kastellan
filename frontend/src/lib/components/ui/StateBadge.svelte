<script lang="ts">
	import { toneColor, type State, type Tone } from '#lib/states.js';

	/** The State badge: a dot and a capitalized word, in the state's tone (states.ts has the
	 * words). The only place a state's colour meets text. `pulse` for live trouble (a device or
	 * check down or degraded); `quiet` drops the outline and the word's colour, for a state inside a
	 * line of text: only its dot is coloured. */
	let {
		state,
		word,
		tone = 'neutral',
		title,
		pulse = false,
		quiet = false
	}: {
		state?: State;
		word?: string;
		tone?: Tone;
		title?: string;
		pulse?: boolean;
		quiet?: boolean;
	} = $props();
	const s = $derived(state ?? { word: word ?? '', tone });
	const color = $derived(toneColor[s.tone]);
</script>

<span
	class="inline-flex shrink-0 items-center gap-1.5 rounded-full text-xs font-medium whitespace-nowrap {quiet
		? 'text-muted'
		: 'border border-line bg-surface-2 py-0.5 pr-2.5 pl-2'}"
	style:color={quiet ? undefined : color.text}
	title={title ?? s.title}
>
	<span
		class="size-1.5 shrink-0 rounded-full"
		class:pulse
		style:background={color.fill}
		style:color={color.fill}
	></span>
	{s.word}
</span>
