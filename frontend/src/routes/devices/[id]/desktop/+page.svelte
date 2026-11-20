<script lang="ts">
	import { onDestroy } from 'svelte';
	import { page } from '$app/state';
	import { api } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import Maximize from '@lucide/svelte/icons/maximize';
	import Power from '@lucide/svelte/icons/power';
	import type Guacamole from 'guacamole-common-js';

	const id = $derived(page.params.id ?? '');
	const device = $derived(store.byId.get(id));

	type Phase = 'form' | 'connecting' | 'connected' | 'ended';
	let phase = $state<Phase>('form');
	let message = $state<string | null>(null);
	let username = $state('');
	let password = $state('');
	let domain = $state('');
	let screen: HTMLDivElement | undefined = $state();
	let frame: HTMLDivElement | undefined = $state();

	let client: Guacamole.Client | null = null;
	let keyboard: Guacamole.Keyboard | null = null;
	let observer: ResizeObserver | null = null;

	$effect(() => {
		if (device?.desktop?.user && !username) username = device.desktop.user;
	});

	async function connect(e: SubmitEvent) {
		e.preventDefault();
		if (!frame || !screen) return;
		phase = 'connecting';
		message = null;
		let ticket: string;
		try {
			// The password goes to the hub once, for a one-time ticket; it is never in a URL.
			ticket = (await api.desktopTicket(id, { username, password, domain })).ticket;
		} catch (err) {
			phase = 'form';
			message = (err as Error).message;
			return;
		} finally {
			password = '';
		}

		const { default: G } = await import('guacamole-common-js');
		const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
		const tunnel = new G.WebSocketTunnel(
			`${scheme}://${location.host}/api/devices/${encodeURIComponent(id)}/desktop`
		);
		const c = new G.Client(tunnel);
		client = c;
		const display = c.getDisplay();
		screen.replaceChildren(display.getElement());

		const fit = () => {
			const w = display.getWidth();
			const h = display.getHeight();
			if (frame && w && h)
				display.scale(Math.min(frame.clientWidth / w, frame.clientHeight / h, 1));
		};
		display.onresize = fit;
		observer = new ResizeObserver(fit);
		observer.observe(frame);

		c.onstatechange = (state: number) => {
			if (state === 3) phase = 'connected'; // CONNECTED
			if (state === 5) {
				phase = 'ended'; // DISCONNECTED
				cleanup();
			}
		};
		c.onerror = (status: Guacamole.Status) => {
			message = status.message || 'The remote desktop ended with an error.';
		};

		// Mouse and touch on the screen (positions back to the remote screen's scale), keyboard
		// while the page has focus.
		const send = (e: Guacamole.Event) => {
			const s = (e as Guacamole.Mouse.Event).state;
			const scale = display.getScale() || 1;
			c.sendMouseState(
				new G.Mouse.State(s.x / scale, s.y / scale, s.left, s.middle, s.right, s.up, s.down)
			);
		};
		const events = ['mousedown', 'mouseup', 'mousemove'];
		new G.Mouse(display.getElement()).onEach(events, send);
		new G.Mouse.Touchscreen(display.getElement()).onEach(events, send);
		// One keyboard listener for the page's life; connecting again only sets its handlers.
		keyboard ??= new G.Keyboard(document);
		keyboard.onkeydown = (keysym: number) => {
			c.sendKeyEvent(1, keysym);
			return false;
		};
		keyboard.onkeyup = (keysym: number) => c.sendKeyEvent(0, keysym);

		const dpi = Math.round(96 * (window.devicePixelRatio || 1));
		const params = new URLSearchParams({
			ticket,
			width: String(Math.round(frame.clientWidth)),
			height: String(Math.round(frame.clientHeight)),
			dpi: String(dpi)
		});
		c.connect(params.toString());
	}

	function cleanup() {
		observer?.disconnect();
		observer = null;
		if (keyboard) {
			keyboard.reset(); // releases held keys while the handlers can still send them
			keyboard.onkeydown = null;
			keyboard.onkeyup = null;
		}
	}

	function disconnect() {
		client?.disconnect();
		client = null;
		cleanup();
		phase = 'ended';
	}

	function fullscreen() {
		frame?.requestFullscreen?.();
	}

	onDestroy(disconnect);
</script>

<svelte:head><title>Desktop · {device?.name ?? id} · Kastellan</title></svelte:head>

<div class="flex flex-wrap items-center justify-between gap-3">
	<div class="flex min-w-0 items-center gap-3">
		<a href="/devices/{id}" class="text-muted hover:text-text" aria-label="Back to the device">
			<ArrowLeft class="size-4" />
		</a>
		<h1 class="truncate text-lg font-semibold tracking-tight">
			{device?.name ?? id}
			{#if device?.desktop}
				<span class="ml-1 text-sm font-normal text-muted uppercase">{device.desktop.protocol}</span>
			{/if}
		</h1>
	</div>
	{#if phase === 'connected'}
		<div class="flex gap-2">
			<button
				onclick={fullscreen}
				class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-surface px-3 text-sm font-medium shadow-card hover:border-line-strong"
			>
				<Maximize class="size-3.5" /> Full screen
			</button>
			<button
				onclick={disconnect}
				class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-surface px-3 text-sm font-medium shadow-card hover:border-line-strong"
			>
				<Power class="size-3.5" /> Disconnect
			</button>
		</div>
	{/if}
</div>

{#if store.devices && !device}
	<p class="card mt-4 px-5 py-8 text-center text-sm text-muted">No device {id}.</p>
{:else if device && !device.desktop}
	<div class="card mt-4 px-6 py-8 text-sm leading-relaxed text-muted">
		<p class="font-medium text-text">This device has no remote desktop set up.</p>
		<p class="mt-2">Add it to the device in config.yaml:</p>
		<pre class="mt-3 rounded-lg bg-surface-2 p-3 font-mono text-[13px] text-text">desktop:
  protocol: rdp   # or vnc
  user: andreas</pre>
	</div>
{:else}
	{#if phase === 'form' || phase === 'ended'}
		<form onsubmit={connect} class="card mx-auto mt-6 max-w-sm p-6">
			<h2 class="font-semibold tracking-tight">
				{phase === 'ended' ? 'Disconnected' : 'Connect to the desktop'}
			</h2>
			{#if message}
				<p class="mt-2 text-sm" style:color="var(--critical-text)" role="alert">{message}</p>
			{/if}
			<p class="mt-1 text-sm text-muted">
				The password is used for this connection only and never stored.
			</p>
			<label class="mt-5 block text-sm font-medium" for="du">User</label>
			<input
				id="du"
				bind:value={username}
				autocomplete="off"
				class="mt-1.5 h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
			/>
			<label class="mt-4 block text-sm font-medium" for="dp">Password</label>
			<input
				id="dp"
				type="password"
				bind:value={password}
				autocomplete="off"
				class="mt-1.5 h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
			/>
			{#if device?.desktop?.protocol === 'rdp'}
				<label class="mt-4 block text-sm font-medium" for="dd">
					Domain <span class="font-normal text-faint">(if any)</span>
				</label>
				<input
					id="dd"
					bind:value={domain}
					autocomplete="off"
					class="mt-1.5 h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
				/>
			{/if}
			<button
				type="submit"
				class="mt-6 h-10 w-full rounded-xl bg-accent text-sm font-medium text-accent-ink hover:opacity-90"
			>
				Connect
			</button>
		</form>
	{/if}
	<div
		bind:this={frame}
		class="mt-4 grid h-[calc(100dvh-12rem)] min-h-64 place-items-center overflow-hidden rounded-xl border border-line bg-black shadow-card"
		class:hidden={phase === 'form' || phase === 'ended'}
	>
		{#if phase === 'connecting'}<p class="text-sm text-white/70">Connecting…</p>{/if}
		<div bind:this={screen} class="cursor-none"></div>
	</div>
	<p class="mt-2 text-xs text-faint">
		Opening and closing the desktop goes to the audit log; the password and what is on the screen
		don't.
	</p>
{/if}
