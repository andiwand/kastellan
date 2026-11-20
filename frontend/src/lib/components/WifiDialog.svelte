<script lang="ts">
	import { api, type WifiSettings } from '#lib/api.js';
	import X from '@lucide/svelte/icons/x';

	/** An OpenWrt device's Wi-Fi: network names, passwords, on/off, channels. Only what changes
	 * is sent; a password is only sent when a new one is typed. The device applies it with a
	 * rollback: if it can't be reached afterwards, it undoes the change by itself. */
	let { device, onclose }: { device: string; onclose: () => void } = $props();

	let settings = $state<WifiSettings | null>(null);
	let edits = $state<Record<string, Record<string, string>>>({});
	let error = $state<string | null>(null);
	let busy = $state(false);
	let done = $state(false);
	let dialog: HTMLDialogElement | undefined = $state();

	$effect(() => {
		dialog?.showModal();
	});
	$effect(() => {
		const root = document.documentElement;
		const before = root.style.overflow;
		root.style.overflow = 'hidden';
		return () => {
			root.style.overflow = before;
		};
	});

	async function load() {
		try {
			settings = await api.wifi(device);
		} catch (e) {
			error = (e as Error).message;
		}
	}
	load();

	function set(section: string, key: string, value: string) {
		edits[section] = { ...(edits[section] ?? {}), [key]: value };
	}

	const changes = $derived(
		Object.fromEntries(
			Object.entries(edits)
				.map(([s, v]) => [s, Object.fromEntries(Object.entries(v).filter(([, x]) => x !== ''))])
				.filter(([, v]) => Object.keys(v as object).length)
		)
	);

	async function save(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = null;
		try {
			settings = await api.setWifi(device, changes);
			edits = {};
			done = true;
		} catch (err) {
			error = (err as Error).message;
		} finally {
			busy = false;
		}
	}

	const input =
		'h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent';
	const label = 'flex flex-col gap-1 text-xs font-medium text-muted';
	const button =
		'inline-flex h-9 items-center justify-center rounded-lg border border-line px-3 text-sm font-medium hover:border-line-strong disabled:opacity-50';
</script>

<dialog
	bind:this={dialog}
	{onclose}
	class="m-auto max-h-[calc(100dvh-2rem)] w-[min(40rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-0 text-text shadow-pop backdrop:bg-black/40"
	aria-labelledby="wifi-title"
>
	<form class="flex max-h-[calc(100dvh-2rem)] flex-col" onsubmit={save}>
		<div class="flex items-center justify-between border-b border-line px-5 py-4">
			<h2 id="wifi-title" class="text-base font-semibold">Wi-Fi settings</h2>
			<button type="button" class="text-muted hover:text-text" onclick={() => dialog?.close()}>
				<X class="size-5" /><span class="sr-only">Close</span>
			</button>
		</div>
		<div class="flex flex-col gap-4 overflow-y-auto overscroll-contain px-5 py-4">
			{#if !settings && !error}<p class="text-sm text-muted">Loading…</p>{/if}
			{#if settings}
				<p class="text-xs text-muted">
					Applied with a rollback: kept only if the device still answers and its radios come back
					up; otherwise undone (by the device itself after 90 seconds if it can't be reached).
				</p>
				{#each settings.interfaces as iface (iface.section)}
					<fieldset class="grid gap-3 rounded-xl border border-line p-3 sm:grid-cols-2">
						<legend class="px-1 text-xs text-muted">
							{iface.mode ?? 'network'} on {iface.device}
						</legend>
						<label class={label}>
							Network name (SSID)
							<input
								class={input}
								value={edits[iface.section]?.ssid ?? iface.ssid ?? ''}
								oninput={(e) => set(iface.section, 'ssid', e.currentTarget.value)}
							/>
						</label>
						<label class={label}>
							New password <span class="font-normal text-faint">
								{iface.key_set ? '(empty: keep the current one)' : '(none set)'}
							</span>
							<input
								class={input}
								type="password"
								autocomplete="new-password"
								minlength="8"
								value={edits[iface.section]?.key ?? ''}
								oninput={(e) => set(iface.section, 'key', e.currentTarget.value)}
							/>
						</label>
						<label class="choice text-sm sm:col-span-2">
							<input
								type="checkbox"
								checked={(edits[iface.section]?.disabled ?? iface.disabled) !== '1'}
								onchange={(e) =>
									set(iface.section, 'disabled', e.currentTarget.checked ? '0' : '1')}
							/>
							On
						</label>
					</fieldset>
				{/each}
				{#each settings.radios as radio (radio.section)}
					<fieldset class="grid gap-3 rounded-xl border border-line p-3 sm:grid-cols-2">
						<legend class="px-1 text-xs text-muted">
							radio {radio.section} · {radio.band ?? ''}
						</legend>
						<label class={label}>
							Channel
							<input
								class={input}
								placeholder="auto"
								value={edits[radio.section]?.channel ?? radio.channel ?? ''}
								oninput={(e) => set(radio.section, 'channel', e.currentTarget.value)}
							/>
						</label>
						<label class="choice self-end text-sm">
							<input
								type="checkbox"
								checked={(edits[radio.section]?.disabled ?? radio.disabled) !== '1'}
								onchange={(e) =>
									set(radio.section, 'disabled', e.currentTarget.checked ? '0' : '1')}
							/>
							Radio on
						</label>
					</fieldset>
				{/each}
			{/if}
			{#if error}<p class="text-sm" style:color="var(--critical-text)">{error}</p>{/if}
			{#if done}<p class="text-sm" style:color="var(--good-text)">
					Applied, and the device still answers.
				</p>{/if}
		</div>
		<div class="flex justify-end gap-2 border-t border-line px-5 py-3">
			<button type="button" class={button} onclick={() => dialog?.close()}>Close</button>
			<button
				class="{button} border-transparent bg-accent text-accent-ink hover:opacity-90"
				disabled={busy || !Object.keys(changes).length}
			>
				{busy ? 'Applying…' : 'Apply'}
			</button>
		</div>
	</form>
</dialog>
