<script lang="ts">
	import { untrack } from 'svelte';
	import { api, type GroupSsh, type SshFile, type SshRow } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import JobDialog from './JobDialog.svelte';
	import X from '@lucide/svelte/icons/x';
	import Plus from '@lucide/svelte/icons/plus';

	/** A group's SSH files side by side, changed on all at once: fetch, edit, push. A column per
	 * device and user (several users per device: root on some, a name on others); a row per key
	 * or known host. Edits wait as pending until pushed; a column whose file changed meanwhile is
	 * refused there (fetch again). Kastellan's own keys are shown, never removed. */
	let { group, canChange }: { group: string; canChange: boolean } = $props();

	const LOGIN = '@login';
	const stored = () => `kastellan.ssh-users.${group}`;
	function remembered(): string[] {
		try {
			const v = JSON.parse(localStorage.getItem(stored()) ?? 'null');
			return Array.isArray(v) && v.every((u) => typeof u === 'string') && v.length ? v : [LOGIN];
		} catch {
			return [LOGIN];
		}
	}

	let file = $state<SshFile>('authorized_keys');
	let users = $state<string[]>(untrack(remembered)); // per group, kept in this browser
	let newUser = $state('');
	let data = $state<GroupSsh | null>(null);
	let error = $state<string | null>(null);
	let busy = $state(false);
	// Pending edits per column: lines to add, entries (by key) to remove.
	let pending = $state<Record<string, { add: string[]; remove: string[] }>>({});
	let pasted = $state('');
	let pasteError = $state<string | null>(null);
	let pushing = $state(false);

	function remember() {
		try {
			localStorage.setItem(stored(), JSON.stringify(users));
		} catch {
			// private window: only for now
		}
	}

	async function fetchAll() {
		busy = true;
		error = null;
		try {
			data = await api.groupSsh(group, file, users);
			pending = {};
			pasteError = null;
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}

	function addUser(e: SubmitEvent) {
		e.preventDefault();
		const u = newUser.trim();
		if (!/^[a-z_][\w.-]{0,31}$/i.test(u) || users.includes(u)) return;
		users = [...users, u];
		newUser = '';
		remember();
	}
	function dropUser(u: string) {
		users = users.filter((x) => x !== u);
		if (!users.length) users = [LOGIN];
		remember();
	}

	const readable = $derived((data?.columns ?? []).filter((c) => !c.error));
	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const col = (id: string) => (pending[id] ??= { add: [], remove: [] });
	const has = (list: string[], v: string) => list.includes(v);
	function toggle(list: string[], v: string) {
		const i = list.indexOf(v);
		if (i >= 0) list.splice(i, 1);
		else list.push(v);
	}

	// What a cell will be after the push: the row's state there, and what is pending.
	function cell(row: SshRow, column: string) {
		const p = pending[column];
		if (row.on[column]) return p && has(p.remove, row.key) ? 'removes' : row.on[column];
		return p && has(p.add, row.line) ? 'adds' : 'missing';
	}
	function toggleCell(row: SshRow, column: string) {
		if (row.kastellan) return;
		if (row.on[column]) toggle(col(column).remove, row.key);
		else toggle(col(column).add, row.line);
	}
	function removeEverywhere(row: SshRow) {
		for (const c of readable)
			if (row.on[c.id] && !has(col(c.id).remove, row.key)) col(c.id).remove.push(row.key);
	}
	function addWhereMissing(row: SshRow) {
		for (const c of readable)
			if (!row.on[c.id] && !has(col(c.id).add, row.line)) col(c.id).add.push(row.line);
	}
	function undo(row: SshRow) {
		for (const p of Object.values(pending)) {
			p.remove = p.remove.filter((k) => k !== row.key);
			p.add = p.add.filter((l) => l !== row.line);
		}
	}
	const touched = (row: SshRow) =>
		Object.values(pending).some((p) => has(p.remove, row.key) || has(p.add, row.line));

	// A pasted line: added to every readable column (the hub skips where it is already).
	function addPasted(e: SubmitEvent) {
		e.preventDefault();
		const line = pasted.trim();
		const ok =
			file === 'authorized_keys'
				? /^(\S+\s+)?(ssh-|ecdsa-|sk-)\S+\s+[A-Za-z0-9+/]+={0,3}(\s.*)?$/.test(line)
				: /^(@\S+\s+)?\S+\s+(ssh-|ecdsa-|sk-)\S+\s+[A-Za-z0-9+/]+={0,3}(\s.*)?$/.test(line);
		if (!ok || line.includes('\n')) {
			pasteError =
				file === 'authorized_keys'
					? 'one key line: [options] type key [comment]'
					: 'one line: hosts type key';
			return;
		}
		pasteError = null;
		for (const c of readable) if (!has(col(c.id).add, line)) col(c.id).add.push(line);
		pasted = '';
	}
	const newLines = $derived.by(() => {
		const known = new Set((data?.rows ?? []).map((r) => r.line));
		return [...new Set(Object.values(pending).flatMap((p) => p.add))].filter((l) => !known.has(l));
	});
	function dropNew(line: string) {
		for (const p of Object.values(pending)) p.add = p.add.filter((l) => l !== line);
	}

	const changes = $derived(
		Object.fromEntries(
			Object.entries(pending).filter(([, p]) => p.add.length || p.remove.length)
		) as Record<string, { add: string[]; remove: string[] }>
	);
	const count = $derived(Object.keys(changes).length);
	const columnOf = (id: string) => data?.columns.find((c) => c.id === id);
	const short = (fp: string) => fp.replace(/^SHA256:/, '').slice(0, 10);

	/** A line's identity as the hub keys rows: "type data" for a key, "[marker ]hosts type" for a
	 * known host; a pasted line with one that is there already changes that line, in place. */
	function keyOf(line: string): string {
		const w = line.trim().split(/\s+/);
		if (data?.file === 'known_hosts') {
			const marker = w[0]?.startsWith('@') ? `${w.shift()} ` : '';
			return `${marker}${w[0]} ${w[1]}`;
		}
		const i = w.findIndex((x) => /^(ssh-|ecdsa-|sk-)/.test(x));
		return i >= 0 ? `${w[i]} ${w[i + 1]}` : line;
	}
	function summary(id: string, p: { add: string[]; remove: string[] }): string {
		const there = new Set((data?.rows ?? []).filter((r) => r.on[id]).map((r) => r.key));
		const changed = p.add.filter((l) => there.has(keyOf(l))).length;
		const parts = [
			p.add.length - changed && `adds ${p.add.length - changed}`,
			changed && `changes ${changed}`,
			p.remove.filter((k) => !p.add.some((l) => keyOf(l) === k)).length &&
				`removes ${p.remove.filter((k) => !p.add.some((l) => keyOf(l) === k)).length}`
		];
		return parts.filter(Boolean).join(', ');
	}
	// Worth a second look before pushing: root gaining a way in, and options dropbear may ignore.
	function caution(id: string, p: { add: string[] }): string | null {
		const c = columnOf(id);
		const device = c ? store.byId.get(c.device) : undefined;
		if (!p.add.length || data?.file !== 'authorized_keys') return null;
		if (device && (device.openwrt || ['router', 'access-point', 'switch'].includes(device.kind)))
			if (p.add.some((l) => !/^(ssh-|ecdsa-|sk-)/.test(l.trim())))
				return "OpenWrt's dropbear may ignore options (from=, restrict): the key may get more than meant";
		if (c?.user === 'root') return 'gives these keys a root login';
		return null;
	}

	const STATE: Record<string, { text: string; color: string; bg?: string }> = {
		same: { text: 'yes', color: 'var(--good-text)' },
		differs: { text: 'differs', color: 'var(--warning-text)' },
		missing: { text: '—', color: 'var(--faint)' },
		adds: { text: 'adds', color: 'var(--accent)', bg: 'var(--accent-soft)' },
		removes: {
			text: 'removes',
			color: 'var(--critical-text)',
			bg: 'color-mix(in srgb, var(--critical-text) 10%, transparent)'
		}
	};
</script>

<div class="mt-5 border-t border-line pt-4">
	<div class="flex flex-wrap items-center gap-x-3 gap-y-2">
		<h3 class="text-sm font-semibold">SSH</h3>
		<select
			class="h-8 rounded-lg border border-line bg-surface px-2 text-xs"
			bind:value={file}
			onchange={() => (data = null)}
			aria-label="Which file"
		>
			<option value="authorized_keys">Who may log in (authorized_keys)</option>
			<option value="known_hosts">Hosts they trust (known_hosts)</option>
		</select>
		<span class="flex flex-wrap items-center gap-1.5 text-xs">
			{#each users as u (u)}
				<span class="inline-flex items-center gap-1 rounded-md border border-line px-1.5 py-0.5">
					{u === LOGIN ? 'its login' : u}
					<button
						class="text-faint hover:text-text"
						onclick={() => dropUser(u)}
						title="Not this user"
					>
						<X class="size-3" /><span class="sr-only">Remove {u}</span>
					</button>
				</span>
			{/each}
			<form class="inline-flex" onsubmit={addUser}>
				<input
					class="h-6 w-24 rounded-md border border-line bg-surface px-1.5 text-xs"
					bind:value={newUser}
					placeholder="+ user"
					aria-label="Another user"
				/>
			</form>
		</span>
		<button class="btn btn-sm ml-auto" disabled={busy} onclick={fetchAll}>
			{busy ? 'Reading' : data ? 'Fetch again' : 'Fetch'}
		</button>
	</div>
	<p class="mt-1 text-xs text-muted">
		Each device's file for these users, read over its <code>ssh:</code> login (another user's needs root
		or sudo there).
	</p>
	{#if error}<p class="mt-2 text-xs" style:color="var(--critical-text)">{error}</p>{/if}

	{#if data}
		{#if data.no_ssh.length}
			<p class="mt-2 text-xs text-muted">
				Can't be reached by SSH (no <code>ssh:</code>): {data.no_ssh.map(name).join(', ')}
			</p>
		{/if}
		<div class="mt-3 overflow-x-auto rounded-lg border border-line">
			<table class="w-full text-xs">
				<thead>
					<tr class="border-b border-line text-left text-muted">
						<th class="px-2 py-2 font-medium">{file === 'known_hosts' ? 'Host' : 'Key'}</th>
						{#each data.columns as c (c.id)}
							<th class="px-2 py-2 text-center font-medium whitespace-nowrap" title={c.path ?? ''}>
								{name(c.device)}<br /><span class="font-normal text-faint">{c.user}</span>
								{#if c.error}<span
										class="mx-auto block max-w-36 truncate font-normal"
										style:color="var(--warning-text)"
										title={c.error}
									>
										{c.error}
									</span>{:else if !c.exists}<span class="block font-normal text-faint">
										no file yet
									</span>{/if}
							</th>
						{/each}
						{#if canChange}<th class="px-2 py-2"></th>{/if}
					</tr>
				</thead>
				<tbody class="divide-y divide-line">
					{#each data.rows as row (row.key)}
						<tr class={touched(row) ? 'bg-surface-2' : ''}>
							<td class="max-w-64 px-2 py-1.5">
								<span class="block truncate" title={row.line}>
									{row.label || '(no comment)'}
								</span>
								<span class="font-mono text-[10px] text-faint">
									{row.type} · {short(row.fingerprint)}
								</span>
								{#if row.kastellan}<span class="ml-1 text-[10px] text-muted">
										· {row.kastellan}, kept
									</span>{/if}
								{#if row.conflict}<span class="block text-[10px]" style:color="var(--warning-text)">
										different keys for this host
									</span>{/if}
							</td>
							{#each data.columns as c (c.id)}
								{@const s = c.error ? null : STATE[cell(row, c.id)]}
								<td class="px-1 py-1 text-center">
									{#if s}
										<button
											class="w-full rounded px-1 py-0.5 disabled:cursor-default"
											style:color={s.color}
											style:background={s.bg ?? 'transparent'}
											disabled={!canChange || !!row.kastellan}
											title={row.kastellan ? "Kastellan's own: rotate it instead" : 'Change here'}
											onclick={() => toggleCell(row, c.id)}
										>
											{s.text}
										</button>
									{/if}
								</td>
							{/each}
							{#if canChange}
								<td class="px-2 py-1 text-right whitespace-nowrap">
									{#if touched(row)}
										<button class="text-muted underline" onclick={() => undo(row)}>undo</button>
									{:else if !row.kastellan}
										<button class="text-muted underline" onclick={() => addWhereMissing(row)}>
											add where missing
										</button>
										·
										<button
											class="underline"
											style:color="var(--critical-text)"
											onclick={() => removeEverywhere(row)}
										>
											remove everywhere
										</button>
									{/if}
								</td>
							{/if}
						</tr>
					{/each}
					{#each newLines as line (line)}
						<tr class="bg-surface-2">
							<td class="max-w-64 px-2 py-1.5">
								<span class="block truncate font-mono text-[10px]" title={line}>{line}</span>
							</td>
							{#each data.columns as c (c.id)}
								<td class="px-1 py-1 text-center">
									{#if !c.error && pending[c.id]?.add.includes(line)}
										<span class="rounded px-1 py-0.5" style:color="var(--accent)">adds</span>
									{/if}
								</td>
							{/each}
							<td class="px-2 py-1 text-right">
								<button class="text-muted underline" onclick={() => dropNew(line)}>undo</button>
							</td>
						</tr>
					{/each}
					{#if !data.rows.length && !newLines.length}
						<tr>
							<td class="px-2 py-3 text-muted" colspan={data.columns.length + 2}>Nothing in it.</td>
						</tr>
					{/if}
				</tbody>
			</table>
		</div>

		{#if canChange}
			<form class="mt-3 flex flex-wrap items-start gap-2" onsubmit={addPasted}>
				<input
					class="h-8 min-w-0 flex-1 rounded-lg border border-line bg-surface px-2 font-mono text-xs"
					bind:value={pasted}
					placeholder={file === 'authorized_keys'
						? 'ssh-ed25519 AAAA… someone@laptop'
						: 'nas.lan,192.168.1.10 ssh-ed25519 AAAA…'}
					aria-label="A line to add to all"
				/>
				<button class="btn btn-sm" disabled={!pasted.trim()}>
					<Plus class="size-3.5" /> Add to all
				</button>
				<button
					type="button"
					class="btn btn-sm btn-primary"
					disabled={!count}
					onclick={() => (pushing = true)}
				>
					Review and push{count ? ` (${count})` : ''}
				</button>
			</form>
			{#if pasteError}<p class="mt-1 text-xs" style:color="var(--critical-text)">
					{pasteError}
				</p>{/if}
		{/if}
	{/if}
</div>

{#if pushing && data}
	{@const fetched = data}
	<JobDialog
		title="Push the SSH changes"
		path="/groups/{encodeURIComponent(group)}/ssh/push"
		action="Push"
		body={() => ({
			file: fetched.file,
			changes: Object.fromEntries(
				Object.entries(changes).map(([id, p]) => [
					id,
					{ version: columnOf(id)?.version ?? '', add: p.add, remove: p.remove }
				])
			)
		})}
		onclose={() => (pushing = false)}
		ondone={fetchAll}
	>
		<p class="text-muted">
			Device by device, each only if its file is still as fetched; every other line stays.
		</p>
		<ul class="mt-2 space-y-1">
			{#each Object.entries(changes) as [id, p] (id)}
				{@const c = columnOf(id)}
				<li>
					<span class="font-medium">{name(c?.device ?? id)}</span>
					<span class="text-muted">· {c?.user}</span>:
					{summary(id, p)}
					{#if caution(id, p)}
						<span class="block text-xs" style:color="var(--warning-text)">{caution(id, p)}</span>
					{/if}
				</li>
			{/each}
		</ul>
	</JobDialog>
{/if}
