<script lang="ts">
	import { api, type Backup } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import { onMount } from 'svelte';
	import CodeBlock from './CodeBlock.svelte';
	import StateBadge from './ui/StateBadge.svelte';
	import { backupState } from '#lib/states.js';

	/** The backups of a device that report in (backups.py): when each last said it ran, and for
	 * admins the command its job runs when done (with a token made for it, shown once). */
	let { device }: { device: string } = $props();

	let all = $state<Backup[] | null>(null);
	const mine = $derived((all ?? []).filter((b) => b.device === device));
	let asking = $state<string | null>(null);
	let shown = $state<{ id: string; token: string } | null>(null);
	let error = $state<string | null>(null);

	async function load() {
		try {
			all = await api.backups();
		} catch {
			/* the card stays as it was */
		}
	}
	onMount(() => {
		load();
		const t = setInterval(load, 60_000);
		return () => clearInterval(t);
	});

	async function makeToken(id: string) {
		error = null;
		try {
			shown = { id, token: (await api.backupToken(id)).token };
			asking = null;
			await load();
		} catch (e) {
			error = (e as Error).message;
		}
	}

	const command = (id: string, token: string) =>
		[
			`# At the end of the backup job, on success:`,
			`curl -fsS -m 30 -X POST -H "Authorization: Bearer ${token}" ${location.origin}/api/backups/${id}/ok`,
			`# Or when it failed (the reason as the body):`,
			`curl -fsS -m 30 -X POST -H "Authorization: Bearer ${token}" --data "why" ${location.origin}/api/backups/${id}/fail`,
			``,
			`# e.g. restic backup /data && curl ... /ok || curl ... /fail`
		].join('\n');
	const admin = $derived(store.me?.role === 'admin');
</script>

{#if mine.length}
	<section class="mt-8" id="backups">
		<h2 class="mb-3 text-sm font-semibold">Backups</h2>
		<ul class="card divide-y divide-line">
			{#each mine as b (b.id)}
				<li class="flex flex-col gap-2 px-5 py-3 text-sm">
					<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
						<span class="font-medium">{b.name}</span>
						<StateBadge state={backupState[b.state]} />
						<span class="text-xs text-muted">
							{b.ok ? `last success ${ago(b.ok, store.now)}` : 'no success yet'}
							· expected every {b.within_hours} h
						</span>
						{#if admin}
							<button
								class="ml-auto text-xs text-muted underline hover:text-text"
								onclick={() => ((asking = b.id), (shown = null))}
							>
								{b.token ? 'New token' : 'How it reports in'}
							</button>
						{/if}
					</div>
					{#if b.state === 'failed' && b.message}
						<p class="text-xs text-muted">
							Failed {ago(b.failed, store.now)}: {b.message}
						</p>
					{/if}
					{#if asking === b.id}
						<p class="flex flex-wrap items-center gap-2 text-xs">
							{b.token
								? 'A new token stops the one its job uses now. Make one?'
								: 'Its job calls Kastellan when done, with a token made for it (shown once). Make it?'}
							<button class="btn btn-sm btn-primary" onclick={() => makeToken(b.id)}>
								Make a token
							</button>
							<button class="btn btn-sm" onclick={() => (asking = null)}>Not now</button>
						</p>
					{/if}
					{#if shown?.id === b.id}
						<p class="text-xs text-muted">
							Put this at the end of the backup job. The token is shown only now.
						</p>
						<CodeBlock text={command(b.id, shown.token)} wrap />
					{/if}
					{#if error && asking === b.id}
						<p class="text-xs" style:color="var(--critical-text)">{error}</p>
					{/if}
				</li>
			{/each}
		</ul>
	</section>
{/if}
