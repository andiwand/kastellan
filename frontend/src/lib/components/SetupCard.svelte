<script lang="ts">
	import type { Snippet } from 'svelte';
	import { modal } from '#lib/modal.js';
	import X from '@lucide/svelte/icons/x';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleDashed from '@lucide/svelte/icons/circle-dashed';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';

	/** One part of a device's setup as a card of its own (the same size as its neighbours): what
	 * it is, whether it is set up, and a button; what it takes opens in a dialog (`children`),
	 * loaded on first opening (`onopen`). A part with a dialog of its own passes `onclick` instead. */
	let {
		title,
		about,
		stage,
		note = '',
		onopen,
		onclick,
		button,
		children
	}: {
		title: string;
		about: string; // what it is, in a line
		stage: 'set' | 'available' | 'outdated';
		note?: string; // how it stands, in a few words
		onopen?: () => void;
		onclick?: () => void; // instead of the dialog below
		button?: string; // instead of Manage / Renew / Set up
		children?: Snippet;
	} = $props();

	let open = $state(false);
	let opened = false;
	let dialog: HTMLDialogElement | undefined = $state();

	function show() {
		if (onclick) return onclick();
		open = true;
		if (!opened) {
			opened = true;
			onopen?.();
		}
	}
	const LOOK = {
		set: { icon: CircleCheck, color: 'var(--good-text)', word: 'Set up', button: 'Manage' },
		outdated: {
			icon: CircleAlert,
			color: 'var(--warning-text)',
			word: 'Set up, older than this Kastellan',
			button: 'Renew'
		},
		available: { icon: CircleDashed, color: 'var(--muted)', word: 'Not set up', button: 'Set up' }
	};
	const look = $derived(LOOK[stage]);
</script>

<div class="card flex h-full flex-col p-4">
	<div class="flex items-start gap-2">
		<look.icon class="mt-0.5 size-4 shrink-0" style="color: {look.color}" />
		<div class="min-w-0">
			<h3 class="text-sm font-semibold">{title}</h3>
			<p class="mt-1 text-xs leading-relaxed text-muted">{about}</p>
		</div>
	</div>
	<div class="mt-auto flex flex-wrap items-center justify-between gap-2 pt-3">
		<span class="text-xs" style:color={look.color}>{note || look.word}</span>
		<button class="btn btn-sm {stage === 'set' ? '' : 'btn-primary'}" onclick={show}>
			{button ?? look.button}
		</button>
	</div>
</div>

<!-- Its state lives in the component that fills it: closing loses nothing. -->
{#if open}
	<dialog
		use:modal
		aria-label={title}
		bind:this={dialog}
		onclose={() => (open = false)}
		class="m-auto max-h-[calc(100dvh-2rem)] w-[min(48rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-0 text-text shadow-pop backdrop:bg-black/40"
	>
		<div class="flex max-h-[calc(100dvh-2rem)] flex-col">
			<div class="flex items-center justify-between border-b border-line px-5 py-4">
				<h2 class="text-base font-semibold">{title}</h2>
				<button class="text-muted hover:text-text" onclick={() => dialog?.close()}>
					<X class="size-5" /><span class="sr-only">Close</span>
				</button>
			</div>
			<div class="overflow-y-auto overscroll-contain px-5 pt-2 pb-5">
				{@render children?.()}
			</div>
		</div>
	</dialog>
{/if}
