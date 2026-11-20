<script lang="ts">
	import { onDestroy } from 'svelte';
	import { page } from '$app/state';
	import { api } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import RotateCw from '@lucide/svelte/icons/rotate-cw';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import Copy from '@lucide/svelte/icons/copy';
	import '@xterm/xterm/css/xterm.css';
	import type { Terminal } from '@xterm/xterm';

	/** What the page shows besides the terminal: why it can't connect, and what to do. */
	type Problem =
		| { kind: 'unknown-host'; fingerprint: string }
		| { kind: 'changed-host'; fingerprint: string }
		| { kind: 'no-key'; message: string; publicKey: string | null }
		| { kind: 'error'; message: string }
		| { kind: 'exit'; status: number | null }
		| { kind: 'closed'; message: string };

	const id = $derived(page.params.id ?? '');
	const device = $derived(store.byId.get(id));

	let host: HTMLDivElement | undefined = $state();
	let problem = $state<Problem | null>(null);
	let connected = $state(false);
	let busy = $state(false);
	let copied = $state(false);

	let term: Terminal | null = null;
	let socket: WebSocket | null = null;
	let observer: ResizeObserver | null = null;

	// Each open() is one generation; a slower earlier one must not leave a live shell behind.
	let generation = 0;

	async function open() {
		const mine = ++generation;
		close();
		problem = null;
		connected = false;
		if (!host) return;
		const [{ Terminal }, { FitAddon }] = await Promise.all([
			import('@xterm/xterm'),
			import('@xterm/addon-fit')
		]);
		if (mine !== generation) return; // a newer open() took over while this one loaded
		close();
		const dark = matchMedia('(prefers-color-scheme: dark)').matches;
		term = new Terminal({
			cursorBlink: true,
			fontFamily: "'Geist Mono Variable', ui-monospace, Menlo, monospace",
			fontSize: 13.5,
			lineHeight: 1.2,
			theme: dark
				? {
						background: '#0d1015',
						foreground: '#e8ebf0',
						cursor: '#7b97ff',
						selectionBackground: '#7b97ff55'
					}
				: {
						background: '#ffffff',
						foreground: '#10141c',
						cursor: '#3b63f3',
						selectionBackground: '#3b63f333'
					}
		});
		const fit = new FitAddon();
		term.loadAddon(fit);
		term.open(host);
		fit.fit();
		term.focus();

		const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
		const ws = new WebSocket(
			`${scheme}://${location.host}/api/devices/${encodeURIComponent(id)}/terminal?cols=${term.cols}&rows=${term.rows}`
		);
		ws.binaryType = 'arraybuffer';
		socket = ws;
		ws.onopen = () => (connected = true);
		ws.onmessage = async (e) => {
			if (e.data instanceof ArrayBuffer) {
				term?.write(new Uint8Array(e.data));
				return;
			}
			const m = JSON.parse(e.data);
			if (m.type === 'unknown-host' || m.type === 'changed-host') {
				problem = { kind: m.type, fingerprint: m.fingerprint };
			} else if (m.type === 'exit') {
				problem = { kind: 'exit', status: m.status };
			} else if (m.type === 'error' && /does not accept the hub's key/.test(m.message)) {
				let publicKey: string | null = null;
				try {
					publicKey = (await api.sshKey()).public_key;
				} catch {
					// not an admin, or the hub is gone
				}
				problem = { kind: 'no-key', message: m.message, publicKey };
			} else if (m.type === 'error') {
				problem = { kind: 'error', message: m.message };
			}
		};
		ws.onclose = (e) => {
			connected = false;
			if (!problem) {
				problem = {
					kind: 'closed',
					message: e.reason || (e.code === 1006 ? 'The connection dropped.' : 'The session ended.')
				};
			}
		};
		term.onData((data) => {
			if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'input', data }));
		});
		term.onResize(({ cols, rows }) => {
			if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'resize', cols, rows }));
		});
		observer = new ResizeObserver(() => fit.fit());
		observer.observe(host);
	}

	function close() {
		observer?.disconnect();
		observer = null;
		socket?.close();
		socket = null;
		term?.dispose();
		term = null;
	}

	async function trust(fingerprint: string) {
		busy = true;
		try {
			await api.trustHost(id, fingerprint);
			await open();
		} catch (e) {
			problem = { kind: 'error', message: (e as Error).message };
		} finally {
			busy = false;
		}
	}

	async function forget() {
		busy = true;
		try {
			await api.forgetHost(id);
			await open();
		} catch (e) {
			problem = { kind: 'error', message: (e as Error).message };
		} finally {
			busy = false;
		}
	}

	async function copy(text: string) {
		await navigator.clipboard.writeText(text);
		copied = true;
		setTimeout(() => (copied = false), 1500);
	}

	// Open once the device is known and the page has its element.
	let started = false;
	$effect(() => {
		if (!started && host && device?.ssh) {
			started = true;
			open();
		}
	});
	onDestroy(close);
</script>

<svelte:head><title>Terminal · {device?.name ?? id} · Kastellan</title></svelte:head>

<div class="flex flex-wrap items-center justify-between gap-3">
	<div class="flex min-w-0 items-center gap-3">
		<a
			href="/devices/{id}"
			class="inline-flex items-center gap-1.5 text-sm text-muted hover:text-text"
			aria-label="Back to the device"
		>
			<ArrowLeft class="size-4" />
		</a>
		<h1 class="truncate text-lg font-semibold tracking-tight">
			{device?.name ?? id}
			{#if device?.ssh}
				<span class="ml-1 font-mono text-sm font-normal text-muted">
					{device.ssh.user}@{device.host}
				</span>
			{/if}
		</h1>
		<span
			class="inline-flex items-center gap-1.5 rounded-full border border-line px-2 py-0.5 text-xs"
			style:color={connected ? 'var(--good-text)' : 'var(--muted)'}
		>
			<span
				class="size-1.5 rounded-full"
				style:background={connected ? 'var(--good)' : 'var(--offline)'}
			></span>
			{connected ? 'Connected' : 'Not connected'}
		</span>
	</div>
	<button
		onclick={open}
		class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-surface px-3 text-sm font-medium shadow-card hover:border-line-strong"
	>
		<RotateCw class="size-3.5" /> Reconnect
	</button>
</div>

{#if store.devices && !device}
	<p class="card mt-4 px-5 py-8 text-center text-sm text-muted">No device {id}.</p>
{:else if device && !device.ssh}
	<div class="card mt-4 px-6 py-8 text-sm leading-relaxed text-muted">
		<p class="font-medium text-text">This device has no SSH access set up.</p>
		<p class="mt-2">Add it to the device in config.yaml:</p>
		<pre class="mt-3 rounded-lg bg-surface-2 p-3 font-mono text-[13px] text-text">ssh:
  user: root</pre>
	</div>
{:else}
	{#if problem}
		<div
			class="mt-4 rounded-xl border px-4 py-3 text-sm"
			style:border-color={problem.kind === 'changed-host'
				? 'color-mix(in srgb, var(--critical) 50%, transparent)'
				: 'var(--line)'}
			style:background="var(--surface)"
		>
			{#if problem.kind === 'unknown-host'}
				<div class="flex gap-3">
					<KeyRound class="mt-0.5 size-4 shrink-0 text-accent" />
					<div class="min-w-0">
						<p class="font-medium">First connection to {device?.host}</p>
						<p class="mt-1 text-muted">
							Kastellan doesn't know this host's key yet. Compare it with the host's own (<code
								class="font-mono text-xs"
							>
								ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
							</code>) before you trust it:
						</p>
						<p class="mt-2 font-mono text-xs break-all text-text">{problem.fingerprint}</p>
						<button
							disabled={busy}
							onclick={() => problem?.kind === 'unknown-host' && trust(problem.fingerprint)}
							class="mt-3 h-8 rounded-lg bg-accent px-3 text-sm font-medium text-accent-ink hover:opacity-90 disabled:opacity-60"
						>
							Trust this key and connect
						</button>
					</div>
				</div>
			{:else if problem.kind === 'changed-host'}
				<div class="flex gap-3">
					<ShieldAlert class="mt-0.5 size-4 shrink-0" style="color: var(--critical-text)" />
					<div class="min-w-0">
						<p class="font-medium" style:color="var(--critical-text)">This host's key changed</p>
						<p class="mt-1 text-muted">
							That happens after a reinstall, and when someone sits in between. Kastellan won't
							connect until you know which. The key it shows now:
						</p>
						<p class="mt-2 font-mono text-xs break-all text-text">{problem.fingerprint}</p>
						<button
							disabled={busy}
							onclick={forget}
							class="mt-3 h-8 rounded-lg border border-line px-3 text-sm font-medium hover:border-line-strong disabled:opacity-60"
						>
							It was reinstalled: forget the old key
						</button>
					</div>
				</div>
			{:else if problem.kind === 'no-key'}
				<p class="font-medium">{problem.message}</p>
				{#if problem.publicKey}
					<p class="mt-1 text-muted">
						Add the hub's key to <code class="font-mono text-xs">~/.ssh/authorized_keys</code> of
						{device?.ssh?.user} on {device?.host}:
					</p>
					<div class="mt-2 flex items-start gap-2">
						<code class="min-w-0 flex-1 rounded-lg bg-surface-2 p-2.5 font-mono text-xs break-all">
							{problem.publicKey}
						</code>
						<button
							onclick={() =>
								problem?.kind === 'no-key' && problem.publicKey && copy(problem.publicKey)}
							class="rounded-lg border border-line p-2 text-muted hover:text-text"
							aria-label="Copy the key"
						>
							<Copy class="size-4" />
						</button>
					</div>
					{#if copied}<p class="mt-1 text-xs" style:color="var(--good-text)">Copied.</p>{/if}
				{/if}
			{:else if problem.kind === 'exit'}
				<p class="text-muted">
					The shell ended{problem.status != null ? ` (exit ${problem.status})` : ''}.
				</p>
			{:else}
				<p class="text-muted">{problem.message}</p>
			{/if}
		</div>
	{/if}

	<div
		class="mt-4 overflow-hidden rounded-xl border border-line p-2 shadow-card"
		style:background="var(--terminal-bg)"
	>
		<div
			bind:this={host}
			class="h-[calc(100dvh-14rem)] min-h-64 w-full lg:h-[calc(100dvh-12rem)]"
		></div>
	</div>
	<p class="mt-2 text-xs text-faint">
		Who opens a terminal, and for how long, goes to the audit log; what is typed doesn't.
	</p>
{/if}
