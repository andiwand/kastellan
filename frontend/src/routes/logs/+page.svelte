<script lang="ts">
	import { page } from '$app/state';
	import {
		api,
		more,
		type LogFacets,
		type LogLine,
		type LogMark,
		type LogSources
	} from '#lib/api.js';
	import { onMount } from 'svelte';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import Search from '@lucide/svelte/icons/search';
	import Radio from '@lucide/svelte/icons/radio';
	import Pause from '@lucide/svelte/icons/pause';
	import ShieldQuestion from '@lucide/svelte/icons/shield-question';

	/** Every device's log lines in one place, interleaved by time (logs.py): filtered by device,
	 * source, program, level, time and text; following new lines live. Admins only. */
	const LEVELS = [
		{ id: 7, label: 'All' },
		{ id: 5, label: 'Notice and worse' },
		{ id: 4, label: 'Warnings and worse' },
		{ id: 3, label: 'Errors' }
	];
	const RANGES = [
		{ id: 1, label: '1 h' },
		{ id: 24, label: '24 h' },
		{ id: 168, label: '7 d' },
		{ id: 0, label: 'All' }
	];
	const NAMES = ['emerg', 'alert', 'crit', 'error', 'warning', 'notice', 'info', 'debug'];

	let devices = $state<string[]>(page.url.searchParams.getAll('device'));
	let groups = $state<string[]>(page.url.searchParams.getAll('group'));
	let groupList = $state<{ id: string; name: string; devices: string[] }[]>([]);
	let marks = $state<LogMark[]>([]);
	onMount(async () => {
		try {
			groupList = (await api.groups()).map((g) => ({
				id: g.id,
				name: g.name || g.id,
				devices: g.devices
			}));
		} catch {
			// no groups to offer
		}
	});
	let sources = $state<string[]>([]);
	let programs = $state<string[]>(page.url.searchParams.getAll('program'));
	let level = $state(Number(page.url.searchParams.get('level') ?? 7));
	let hours = $state(24);
	let text = $state(page.url.searchParams.get('q') ?? '');
	let query = $state(page.url.searchParams.get('q') ?? ''); // what is searched (after Enter or a pause)
	let follow = $state(true);

	let lines = $state<LogLine[]>([]);
	let facets = $state<LogFacets | null>(null);
	let info = $state<LogSources | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(false);
	let more_ = $state(true); // older lines may exist
	let generation = 0;

	const filter = $derived({
		device: devices,
		group: groups,
		source: sources,
		program: programs,
		level,
		since: hours ? Math.floor(Date.now() / 1000 - hours * 3600) : undefined,
		q: query.trim() || undefined
	});

	async function load() {
		const mine = ++generation;
		loading = true;
		try {
			const res = await more.logs({ ...filter, limit: 300 });
			if (mine !== generation) return;
			lines = res.lines;
			marks = res.marks ?? [];
			more_ = res.lines.length === 300;
			error = null;
		} catch (e) {
			if (mine === generation) error = (e as Error).message;
		} finally {
			if (mine === generation) loading = false;
		}
	}

	async function older() {
		if (!lines.length) return;
		const before = Math.min(...lines.map((line) => line.id));
		const mine = generation;
		try {
			const res = await more.logs({ ...filter, before, limit: 300 });
			if (mine !== generation) return;
			const seen = new Set(lines.map((line) => line.id));
			lines = [...lines, ...res.lines.filter((line) => !seen.has(line.id))].sort(
				(a, b) => b.ts - a.ts || b.id - a.id
			);
			const known = new Set(marks.map((m) => `${m.ts}:${m.key}:${m.what}`));
			marks = [
				...marks,
				...(res.marks ?? []).filter((m) => !known.has(`${m.ts}:${m.key}:${m.what}`))
			];
			more_ = res.lines.length === 300;
		} catch (e) {
			if (mine === generation) error = (e as Error).message;
		}
	}

	async function newer() {
		const top = lines.reduce((m, l) => Math.max(m, l.id), 0);
		const mine = generation;
		try {
			const res = await more.logs({ ...filter, after: top, limit: 500, since: undefined });
			if (mine !== generation || !res.lines.length) return;
			const seen = new Set(lines.map((l) => l.id));
			lines = [...res.lines.filter((l) => !seen.has(l.id)), ...lines]
				.sort((a, b) => b.ts - a.ts || b.id - a.id)
				.slice(0, 2000);
			const known = new Set(marks.map((m) => `${m.ts}:${m.key}:${m.what}`));
			marks = [
				...marks,
				...(res.marks ?? []).filter((m) => !known.has(`${m.ts}:${m.key}:${m.what}`))
			].filter((m) => m.ts >= lines[lines.length - 1].ts);
		} catch {
			// the next round tries again
		}
	}

	$effect(() => {
		void filter;
		load();
	});
	$effect(() => {
		if (!follow) return;
		const timer = setInterval(newer, 3000);
		return () => clearInterval(timer);
	});
	$effect(() => {
		const go = async () => {
			try {
				[facets, info] = await Promise.all([more.logFacets(hours || 24 * 365), more.logSources()]);
			} catch {
				// shown with the lines' error
			}
		};
		go();
		const timer = setInterval(go, 30000);
		return () => clearInterval(timer);
	});
	// Typing searches after a short pause.
	$effect(() => {
		const t = text;
		const timer = setTimeout(() => (query = t), 400);
		return () => clearTimeout(timer);
	});

	function toggle(list: string[], value: string): string[] {
		return list.includes(value) ? list.filter((x) => x !== value) : [...list, value];
	}
	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const time = (ts: number) => {
		const d = new Date(ts * 1000);
		const today = new Date().toDateString() === d.toDateString();
		return (
			(today ? '' : d.toLocaleDateString([], { day: 'numeric', month: 'short' }) + ' ') +
			d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
		);
	};
	// Lines and the alarms and events between them, newest first.
	type Row = { line: LogLine; mark?: undefined } | { mark: LogMark; line?: undefined };
	const rows = $derived.by((): Row[] => {
		const out: Row[] = lines.map((line) => ({ line }));
		if (!lines.length) return out;
		const oldest = lines[lines.length - 1].ts;
		for (const mark of marks) if (mark.ts >= oldest) out.push({ mark });
		return out.sort((a, b) => (b.line?.ts ?? b.mark!.ts) - (a.line?.ts ?? a.mark!.ts));
	});
	const MARK = {
		opened: { text: 'Alarm', color: 'var(--critical-text)' },
		resolved: { text: 'Resolved', color: 'var(--good-text)' },
		event: { text: 'Event', color: 'var(--accent)' }
	} as const;
	const tone = (l: number) =>
		l <= 3 ? 'var(--critical-text)' : l === 4 ? 'var(--warning-text)' : undefined;
	const deviceList = $derived(
		Object.entries(facets?.devices ?? {}).sort((a, b) => name(a[0]).localeCompare(name(b[0])))
	);
	const programList = $derived(Object.entries(facets?.programs ?? {}).slice(0, 24));
	const sourceList = $derived(Object.entries(facets?.sources ?? {}));
	const problems = $derived(Object.entries(info?.devices ?? {}).filter(([, s]) => s.error));
	const chip = (on: boolean) =>
		`inline-flex h-7 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium transition-colors ${on ? 'border-accent bg-accent/10 text-text' : 'border-line text-muted hover:border-line-strong hover:text-text'}`;
</script>

<svelte:head><title>Logs · Kastellan</title></svelte:head>

<div class="flex flex-wrap items-end justify-between gap-3">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">Logs</h1>
		<p class="mt-1 max-w-2xl text-sm text-muted">
			Device logs, newest first. Kept {info?.days ?? 14} days. Collection requires the device's logs or
			journal permission, or a FRITZ!Box login.
		</p>
	</div>
	<button
		class="btn btn-sm"
		onclick={() => (follow = !follow)}
		title={follow ? 'New lines appear as they come' : 'Paused'}
	>
		{#if follow}<Radio class="size-3.5" style="color: var(--good-text)" /> Following{:else}<Pause
				class="size-3.5"
			/> Paused{/if}
	</button>
</div>

{#if problems.length}
	<div class="card mt-4 p-3 text-xs">
		{#each problems as [id, s] (id)}
			<p>
				<a class="font-medium hover:underline" href="/devices/{id}?tab=setup">{name(id)}</a>:
				<span style:color={s.denied ? 'var(--muted)' : 'var(--warning-text)'}>{s.error}</span>
			</p>
		{/each}
	</div>
{/if}
{#if info?.syslog_tls?.enabled}
	<p class="mt-3 text-xs text-muted">
		Syslog over TLS:
		{#if info.syslog_tls.listening}
			listening on TCP {info.syslog_tls.port}, {info.syslog_tls.received} lines taken,
			{info.syslog_tls.refused} connections refused (no certificate Kastellan issued).
		{:else}
			<span style:color="var(--warning-text)">{info.syslog_tls.error ?? 'not listening'}</span>
		{/if}
	</p>
{/if}
{#if info?.syslog.enabled}
	<p class="mt-3 text-xs text-muted">
		Syslog port:
		{#if info.syslog.listening}
			listening on UDP {info.syslog.port}, {info.syslog.received} lines taken,
			{info.syslog.refused} refused (not from a known device). Such lines are marked unverified: plain
			syslog can be forged.
		{:else}
			<span style:color="var(--warning-text)">{info.syslog.error ?? 'not listening'}</span>
		{/if}
	</p>
{/if}

<!-- Filters -->
<div class="card mt-5 flex flex-col gap-3 p-4">
	<div class="flex flex-wrap items-center gap-3">
		<label class="relative min-w-56 flex-1">
			<Search class="pointer-events-none absolute top-2.5 left-3 size-4 text-faint" />
			<input
				class="h-9 w-full rounded-lg border border-line bg-surface pr-3 pl-9 text-sm outline-none focus:border-accent"
				placeholder="Search the lines (every word must appear)"
				bind:value={text}
				onkeydown={(e) => e.key === 'Enter' && (query = text)}
			/>
		</label>
		<div class="flex gap-1 rounded-xl border border-line bg-surface p-1">
			{#each RANGES as r (r.id)}
				<button
					class="h-7 rounded-lg px-2.5 text-xs font-medium {hours === r.id
						? 'bg-surface-3 text-text'
						: 'text-muted hover:text-text'}"
					onclick={() => (hours = r.id)}
				>
					{r.label}
				</button>
			{/each}
		</div>
		<select
			class="h-9 rounded-lg border border-line bg-surface px-2 text-sm"
			bind:value={level}
			aria-label="Level"
		>
			{#each LEVELS as l (l.id)}<option value={l.id}>{l.label}</option>{/each}
		</select>
	</div>
	{#if deviceList.length}
		<div class="flex flex-wrap items-center gap-1.5">
			<span class="mr-1 w-16 text-xs text-faint">Devices</span>
			{#each deviceList as [id, n] (id)}
				<button class={chip(devices.includes(id))} onclick={() => (devices = toggle(devices, id))}>
					{name(id)} <span class="tabular text-faint">{n}</span>
				</button>
			{/each}
		</div>
	{/if}
	{#if sourceList.length > 1}
		<div class="flex flex-wrap items-center gap-1.5">
			<span class="mr-1 w-16 text-xs text-faint">Sources</span>
			{#each sourceList as [id, n] (id)}
				<button class={chip(sources.includes(id))} onclick={() => (sources = toggle(sources, id))}>
					{id} <span class="tabular text-faint">{n}</span>
				</button>
			{/each}
		</div>
	{/if}
	{#if programList.length}
		<div class="flex flex-wrap items-center gap-1.5">
			<span class="mr-1 w-16 text-xs text-faint">Programs</span>
			{#each programList as [id, n] (id)}
				<button
					class={chip(programs.includes(id))}
					onclick={() => (programs = toggle(programs, id))}
				>
					{id || '(none)'} <span class="tabular text-faint">{n}</span>
				</button>
			{/each}
		</div>
	{/if}
	{#if groupList.length}
		<div class="flex flex-wrap items-center gap-1.5">
			<span class="mr-1 w-16 text-xs text-faint">Groups</span>
			{#each groupList as g (g.id)}
				<button
					class={chip(groups.includes(g.id))}
					title={g.devices.map(name).join(', ')}
					onclick={() => (groups = toggle(groups, g.id))}
				>
					{g.name}
				</button>
			{/each}
		</div>
	{/if}
	{#if devices.length || groups.length || sources.length || programs.length || level < 7 || query}
		<button
			class="self-start text-xs text-muted underline hover:text-text"
			onclick={() => {
				devices = [];
				groups = [];
				sources = [];
				programs = [];
				level = 7;
				text = query = '';
			}}
		>
			Clear the filters
		</button>
	{/if}
</div>

{#if error}
	<p class="mt-4 text-sm" style:color="var(--critical-text)">{error}</p>
{/if}

<div class="card mt-4 overflow-hidden">
	{#if loading && !lines.length}
		<div class="h-48 animate-pulse"></div>
	{:else if !lines.length}
		<p class="px-5 py-8 text-sm text-muted">
			{facets?.total
				? 'No lines match.'
				: 'No lines yet. Logs come from devices whose login may read them: an OpenWrt device (tick “Read its system log” in its Setup), a Linux host whose actor may read its journal, or a FRITZ!Box with Kastellan’s login.'}
		</p>
	{:else}
		<ol class="divide-y divide-line font-mono text-[12px] leading-relaxed">
			{#each rows as row, i (row.line ? `l${row.line.id}` : `m${i}`)}
				{#if row.mark}
					{@const m = row.mark}
					<li
						class="grid grid-cols-[auto_1fr] gap-x-3 bg-surface-2 px-4 py-1.5 font-sans sm:grid-cols-[8.5rem_9rem_8rem_1fr]"
					>
						<span class="tabular font-mono text-faint">{time(m.ts)}</span>
						{#if m.device}
							<a
								class="truncate text-xs font-medium text-muted hover:underline"
								href="/devices/{m.device}"
							>
								{name(m.device)}
							</a>
						{:else}<span class="text-xs text-muted">Kastellan</span>{/if}
						<span class="hidden text-xs font-semibold sm:block" style:color={MARK[m.what].color}>
							{MARK[m.what].text}
						</span>
						<a
							class="col-span-2 text-xs hover:underline sm:col-span-1"
							href={m.device ? `/devices/${m.device}` : '/'}
							style:color={MARK[m.what].color}
						>
							{m.title}
						</a>
					</li>
				{:else}
					{@const l = row.line!}
					<li
						class="grid grid-cols-[auto_1fr] gap-x-3 px-4 py-1.5 sm:grid-cols-[8.5rem_9rem_8rem_1fr]"
					>
						<span class="tabular text-faint" title={new Date(l.ts * 1000).toLocaleString()}>
							{time(l.ts)}
						</span>
						<a
							class="truncate font-sans text-xs font-medium text-muted hover:text-text hover:underline"
							href="/devices/{l.device}"
						>
							{name(l.device)}
						</a>
						<button
							class="hidden truncate text-left text-muted hover:text-text sm:block"
							title="Only {l.program}"
							onclick={() => (programs = [l.program])}
						>
							{l.program || '–'}
						</button>
						<span class="col-span-2 min-w-0 break-words sm:col-span-1" style:color={tone(l.level)}>
							{#if l.level <= 4}<span class="mr-1 font-sans text-[10px] font-semibold uppercase">
									{NAMES[l.level]}
								</span>{/if}{l.msg}
							{#if !l.verified}<span
									class="ml-1 inline-flex align-[-2px] text-faint"
									title="Came in on the open syslog port: could be forged"
								>
									<ShieldQuestion class="size-3.5" />
								</span>{/if}
						</span>
					</li>
				{/if}
			{/each}
		</ol>
		{#if more_}
			<div class="border-t border-line p-3 text-center">
				<button class="btn btn-sm" onclick={older}>Older lines</button>
			</div>
		{/if}
	{/if}
</div>
{#if facets}
	<p class="mt-3 text-xs text-faint">
		{facets.total.toLocaleString()} lines kept, up to {info?.days ?? 14} days.
	</p>
{/if}
