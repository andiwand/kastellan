<script lang="ts">
	import { settings, copy } from '#lib/settings.svelte.js';
	import { store } from '#lib/state.svelte.js';
	import KindIcon from './KindIcon.svelte';
	import X from '@lucide/svelte/icons/x';
	import Search from '@lucide/svelte/icons/search';

	/** A new group, or one to change: its name and its devices (any kind). Saved into
	 * config.yaml's `groups:`. */
	let { id = null, onclose }: { id?: string | null; onclose: () => void } = $props();

	const existing = $derived(id ? settings.data?.sections.groups.groups[id] : undefined);
	let name = $state('');
	let gid = $state('');
	let members = $state<string[]>([]);
	let filter = $state('');
	let busy = $state(false);
	let error = $state<string | null>(null);
	let dialog: HTMLDialogElement | undefined = $state();
	let first: HTMLInputElement | undefined = $state();
	let idTouched = false;

	$effect(() => {
		dialog?.showModal();
		first?.focus(); // the name, not the close button
	});
	$effect(() => {
		const root = document.documentElement;
		const before = root.style.overflow;
		root.style.overflow = 'hidden';
		return () => {
			root.style.overflow = before;
		};
	});
	$effect.pre(() => {
		if (existing && !gid) {
			name = existing.name;
			gid = id ?? '';
			members = copy(existing.devices);
		}
	});

	function named(v: string) {
		name = v;
		if (!id && !idTouched)
			gid = v
				.toLowerCase()
				.normalize('NFKD')
				.replace(/[^a-z0-9]+/g, '-')
				.replace(/^-+|-+$/g, '')
				.slice(0, 40);
	}

	const devices = $derived(
		(store.devices ?? [])
			.filter((d) => {
				const q = filter.trim().toLowerCase();
				return !q || `${d.name} ${d.host} ${d.kind} ${d.id}`.toLowerCase().includes(q);
			})
			.sort((a, b) => a.name.localeCompare(b.name))
	);
	const taken = $derived(!id && !!settings.data?.sections.groups.groups[gid]);
	const ready = $derived(/^[a-z0-9][a-z0-9_-]*$/.test(gid) && !taken && members.length > 0);

	async function save(e: SubmitEvent) {
		e.preventDefault();
		if (!settings.data) return;
		busy = true;
		error = null;
		try {
			const groups = copy(settings.data.sections.groups.groups ?? {});
			groups[gid] = { name: name.trim() || gid, devices: members };
			await settings.save('groups', { groups });
			dialog?.close();
		} catch (err) {
			error = (err as Error).message;
		} finally {
			busy = false;
		}
	}
</script>

<dialog
	bind:this={dialog}
	{onclose}
	class="m-auto max-h-[calc(100dvh-2rem)] w-[min(38rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-0 text-text shadow-pop backdrop:bg-black/40"
	aria-labelledby="group-title"
>
	<form class="flex max-h-[calc(100dvh-2rem)] flex-col" onsubmit={save}>
		<div class="flex items-center justify-between border-b border-line px-5 py-4">
			<h2 id="group-title" class="text-base font-semibold">
				{id ? `Change “${existing?.name ?? id}”` : 'A new group'}
			</h2>
			<button type="button" class="text-muted hover:text-text" onclick={() => dialog?.close()}>
				<X class="size-5" /><span class="sr-only">Close</span>
			</button>
		</div>
		<div class="flex min-h-0 flex-col gap-4 overflow-y-auto overscroll-contain px-5 py-4">
			<div class="grid gap-3 sm:grid-cols-[1.4fr_1fr]">
				<label class="field">
					Name
					<input
						class="input"
						bind:this={first}
						value={name}
						oninput={(e) => named(e.currentTarget.value)}
						placeholder="Access points"
						required
					/>
				</label>
				<label class="field">
					Id
					<input
						class="input font-mono"
						bind:value={gid}
						oninput={() => (idTouched = true)}
						readonly={!!id}
						placeholder="access-points"
					/>
					{#if taken}<span class="hint" style:color="var(--warning-text)">
							there is one already
						</span>{:else if !id}<span class="hint">its name in config.yaml; it stays</span>{/if}
				</label>
			</div>
			<div class="flex flex-col gap-2">
				<div class="flex items-center justify-between gap-3">
					<span class="text-xs font-medium text-muted">
						Devices <span class="text-faint">· {members.length} chosen</span>
					</span>
					<label class="relative w-48">
						<Search class="absolute top-2.5 left-2.5 size-3.5 text-faint" />
						<input class="input h-8 pl-8 text-xs" placeholder="Find" bind:value={filter} />
					</label>
				</div>
				<div
					class="grid max-h-72 gap-x-4 gap-y-0.5 overflow-y-auto rounded-xl border border-line p-2 sm:grid-cols-2"
				>
					{#each devices as d (d.id)}
						<label class="choice items-center rounded-lg px-2 py-1.5 text-sm hover:bg-surface-2">
							<input type="checkbox" value={d.id} bind:group={members} />
							<KindIcon kind={d.kind} class="size-4 shrink-0 text-muted" />
							<span class="min-w-0 truncate">{d.name}</span>
						</label>
					{:else}
						<p class="p-2 text-xs text-muted">No device matches.</p>
					{/each}
				</div>
				<p class="hint">
					Any kind of device. For OpenWrt members, the group also keeps Wi-Fi, mesh and root's
					password the same.
				</p>
			</div>
			{#if error}<p class="text-sm" style:color="var(--critical-text)">{error}</p>{/if}
		</div>
		<div class="flex justify-end gap-2 border-t border-line px-5 py-3">
			<button type="button" class="btn" onclick={() => dialog?.close()}>Cancel</button>
			<button class="btn btn-primary" disabled={!ready || busy}>
				{busy ? 'Saving…' : id ? 'Save' : 'Add group'}
			</button>
		</div>
	</form>
</dialog>
