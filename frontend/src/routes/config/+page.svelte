<script lang="ts">
	import { api } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import Check from '@lucide/svelte/icons/check';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';

	/** config.yaml itself: for what the forms don't cover (alarms, notifications, VPN, users).
	 * Checked before it is saved, by the rules the hub loads it with; saving over a change made
	 * in the meantime is refused. */
	let text = $state('');
	let saved = $state(''); // as last loaded or saved
	let version = $state('');
	let error = $state<string | null>(null);
	let note = $state<string | null>(null);
	let busy = $state(false);
	let loaded = $state(false);
	let hubError = $state<string | null>(null);

	const dirty = $derived(text !== saved);

	async function load() {
		error = note = null;
		try {
			const got = await api.config();
			text = saved = got.text;
			version = got.version;
			hubError = got.error;
			loaded = true;
		} catch (e) {
			error = (e as Error).message;
		}
	}

	$effect(() => {
		if (store.editBlocked === null && !loaded) load();
	});

	async function check() {
		busy = true;
		error = note = null;
		try {
			const r = await api.checkConfig(text);
			if (r.ok) note = 'Looks right.';
			else error = r.error;
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}

	async function save() {
		busy = true;
		error = note = null;
		try {
			version = (await api.saveConfig(text, version)).version;
			saved = text;
			hubError = null;
			note = 'Saved, and in use. The version before is kept in config-backups/.';
			await store.refresh();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}

	// Leaving with changes that aren't saved: the browser asks first.
	function leaving(e: BeforeUnloadEvent) {
		if (dirty) e.preventDefault();
	}

	function key(e: KeyboardEvent) {
		const area = e.currentTarget as HTMLTextAreaElement;
		if (e.key === 'Tab' && !e.shiftKey) {
			// Two spaces, as YAML wants; Esc then Tab leaves the field.
			e.preventDefault();
			area.setRangeText('  ', area.selectionStart, area.selectionEnd, 'end');
			text = area.value;
		} else if ((e.metaKey || e.ctrlKey) && e.key === 's') {
			e.preventDefault();
			if (dirty && !busy) save();
		}
	}

	const button =
		'inline-flex h-9 items-center gap-1.5 rounded-xl border border-line bg-surface px-3.5 text-sm font-medium shadow-card hover:border-line-strong disabled:opacity-50';
</script>

<svelte:head><title>Config · Kastellan</title></svelte:head>
<svelte:window onbeforeunload={leaving} />

<div class="flex flex-wrap items-end justify-between gap-4">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">Config</h1>
		<p class="mt-1 max-w-2xl text-sm text-muted">
			config.yaml as it is, comments and all. Devices and services are easier on their pages; here
			is everything else: alarms, notifications, VPN, sign-in users.
		</p>
	</div>
	{#if loaded}
		<div class="flex gap-2">
			<button
				class={button}
				disabled={!dirty || busy}
				onclick={() => ((text = saved), (error = note = null))}
			>
				<RotateCcw class="size-4 text-muted" /> Undo changes
			</button>
			<button
				class={button}
				disabled={busy}
				onclick={load}
				title="Read config.yaml again (your unsaved changes here go)"
			>
				Reload
			</button>
			<button class={button} disabled={busy} onclick={check}>
				<Check class="size-4 text-muted" /> Check
			</button>
			<button
				class="inline-flex h-9 items-center rounded-xl bg-accent px-3.5 text-sm font-medium text-accent-ink shadow-card hover:opacity-90 disabled:opacity-50"
				disabled={!dirty || busy}
				onclick={save}
			>
				{busy ? 'Saving…' : 'Save'}
			</button>
		</div>
	{/if}
</div>

{#if store.editBlocked !== null}
	<div class="card mt-6 p-5 text-sm text-muted">
		The config can't be changed from here: {store.editBlocked}.
	</div>
{:else}
	{#if hubError}
		<p class="mt-4 text-sm" style="color: var(--warning-text)">
			The file on disk has a problem the hub ignores for now (it keeps the last good one): {hubError}
		</p>
	{/if}
	{#if error}
		<p class="mt-4 text-sm whitespace-pre-wrap" style="color: var(--critical-text)" role="alert">
			{error}
		</p>
	{:else if note}
		<p class="mt-4 text-sm" style="color: var(--good-text)" role="status">{note}</p>
	{/if}
	<textarea
		bind:value={text}
		onkeydown={key}
		spellcheck="false"
		autocapitalize="off"
		autocomplete="off"
		aria-label="config.yaml"
		class="card mt-4 h-[70dvh] w-full resize-y p-4 font-mono text-[13px] leading-relaxed outline-none focus:border-accent"
		disabled={!loaded}></textarea>
	<p class="mt-2 text-xs text-faint">
		Tab indents, ⌘S / Ctrl+S saves. Saving is refused if the file changed since it was opened.
	</p>
{/if}
