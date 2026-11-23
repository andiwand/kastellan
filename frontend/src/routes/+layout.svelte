<script lang="ts">
	import './layout.css';
	import { page } from '$app/state';
	import { store } from '#lib/state.svelte.js';
	import { badgeOf, isActive, moreBadge, navFor, type NavItem } from '#lib/nav.js';
	import { toneColor } from '#lib/states.js';
	import { modal } from '#lib/modal.js';
	import { ago } from '#lib/format.js';
	import Logo from '#lib/components/Logo.svelte';
	import LayoutDashboard from '@lucide/svelte/icons/layout-dashboard';
	import MonitorSmartphone from '@lucide/svelte/icons/monitor-smartphone';
	import Container from '@lucide/svelte/icons/container';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import GlobeLock from '@lucide/svelte/icons/globe-lock';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';
	import LockKeyhole from '@lucide/svelte/icons/lock-keyhole';
	import Ellipsis from '@lucide/svelte/icons/ellipsis';
	import X from '@lucide/svelte/icons/x';
	import Settings from '@lucide/svelte/icons/settings';
	import ScrollText from '@lucide/svelte/icons/scroll-text';
	import Globe from '@lucide/svelte/icons/globe';
	import Wifi from '@lucide/svelte/icons/wifi';
	import DatabaseBackup from '@lucide/svelte/icons/database-backup';
	import CircleArrowUp from '@lucide/svelte/icons/circle-arrow-up';
	import { runner } from '#lib/runner.svelte.js';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import LogOut from '@lucide/svelte/icons/log-out';
	import Bell from '@lucide/svelte/icons/bell';
	import PushDialog from '#lib/components/PushDialog.svelte';
	import SignIn from '#lib/components/SignIn.svelte';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();
	let pushOpen = $state(false); // notifications on this device (web push)
	let moreOpen = $state(false); // the phone's More sheet

	$effect(() => store.use());

	const ICON: Record<string, typeof LayoutDashboard> = {
		overview: LayoutDashboard,
		devices: MonitorSmartphone,
		internet: Globe,
		wifi: Wifi,
		vpn: GlobeLock,
		services: AppWindow,
		containers: Container,
		updates: CircleArrowUp,
		backups: DatabaseBackup,
		logs: ScrollText,
		signins: ShieldCheck,
		settings: Settings
	};
	const groups = $derived(navFor(store.me?.role === 'admin'));
	const items = $derived(groups.flatMap((g) => g.items));
	const phoneItems = $derived(items.filter((i) => i.phone));
	const moreGroups = $derived(
		groups
			.map((g) => ({ ...g, items: g.items.filter((i) => !i.phone) }))
			.filter((g) => g.items.length)
	);
	const active = (item: NavItem) => isActive(item, page.url.pathname);
	const moreActive = $derived(moreGroups.some((g) => g.items.some(active)));
	// Every badge counts from the one attention model (attention.ts, nav.ts).
	const badge = (item: NavItem) => badgeOf(item.href, store.attention);
	const more = $derived(
		moreBadge(
			moreGroups.flatMap((g) => g.items),
			store.attention
		)
	);
	$effect(() => {
		void page.url.pathname;
		moreOpen = false; // a place was chosen
	});
	// Before the first answer it is connecting, not unreachable (no red flash on every load).
	const link = $derived(store.error ? 'down' : store.updated != null ? 'up' : 'connecting');
</script>

{#if store.health?.locked}
	<div class="grid min-h-dvh place-items-center bg-bg px-4">
		<div class="card w-full max-w-md p-7">
			<div class="flex items-center gap-2.5">
				<Logo size={30} />
				<span class="text-lg font-semibold tracking-tight">Kastellan</span>
			</div>
			<h1 class="mt-6 text-xl font-semibold tracking-tight">Locked</h1>
			<p class="mt-2 text-sm leading-relaxed text-muted">
				The hub could not read its config.yaml when it started, so it does not know who may sign in.
				Rather than open up, it refuses everything until the file is fixed; it picks up the fix by
				itself. The hub's log says what is wrong.
			</p>
		</div>
	</div>
{:else if store.me?.signin && !store.me.user}
	<SignIn />
{:else}
	<div class="min-h-dvh lg:pl-60">
		<!-- Sidebar (wide screens) -->
		<aside
			class="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r border-line bg-surface lg:flex"
		>
			<a href="/" class="flex h-16 shrink-0 items-center gap-2.5 px-5">
				<Logo />
				<span class="text-[15px] font-semibold tracking-tight">Kastellan</span>
			</a>
			<nav class="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 pt-1 pb-3" aria-label="Main">
				{#each groups as g, gi (gi)}
					{#if g.label}
						<p class="mt-4 mb-1 px-3 text-meta font-medium text-faint">{g.label}</p>
					{:else if gi > 0}
						<div class="mt-3"></div>
					{/if}
					{#each g.items as item (item.href)}
						{@const Icon = ICON[item.icon]}
						<a
							href={item.href}
							class="group flex h-9 shrink-0 items-center gap-3 rounded-lg px-3 text-sm font-medium transition-colors {active(
								item
							)
								? 'bg-accent-soft text-accent'
								: 'text-muted hover:bg-surface-2 hover:text-text'}"
							aria-current={active(item) ? 'page' : undefined}
						>
							<Icon class="size-4" strokeWidth={1.9} />
							{item.label}
							{#if item.href === '/updates' && runner.working}
								<span
									class="tabular ml-auto inline-flex items-center gap-1 text-[11px] font-semibold text-accent"
									title="Updates running"
								>
									<span
										class="size-2.5 animate-spin rounded-full border-2 border-line border-t-accent"
									></span>
									{runner.progress.done + runner.progress.failed}/{runner.progress.total}
								</span>
							{:else}
								{@const b = badge(item)}
								{#if b?.count}
									<span
										class="tabular ml-auto rounded-full px-1.5 text-[11px] leading-5 font-semibold {b.tone ===
										'neutral'
											? 'bg-surface-3 text-muted'
											: ''}"
										style:background={b.tone === 'neutral'
											? undefined
											: `color-mix(in srgb, ${toneColor[b.tone].fill} 14%, transparent)`}
										style:color={b.tone === 'neutral' ? undefined : toneColor[b.tone].text}
										title={b.title}
									>
										{b.count}
									</span>
								{:else if b}
									<span
										class="mr-1 ml-auto size-2 rounded-full"
										style:background={toneColor[b.tone].fill}
										title={b.title}
									></span>
								{/if}
							{/if}
						</a>
					{/each}
				{/each}
			</nav>
			<div class="shrink-0 space-y-3 border-t border-line p-4 text-xs text-muted">
				{#if store.health?.demo}
					<p class="rounded-lg border border-dashed border-line-strong px-3 py-2 leading-relaxed">
						<span class="font-medium text-text">Demo mode.</span> A made-up network with simulated results.
					</p>
				{/if}
				{#if store.me?.user}
					<div class="flex items-center gap-2.5">
						<div
							class="grid size-7 place-items-center rounded-full bg-surface-3 text-xs font-semibold text-text uppercase"
						>
							{store.me.user.slice(0, 1)}
						</div>
						<div class="min-w-0 flex-1 leading-tight">
							<p class="truncate font-medium text-text">{store.me.user}</p>
							<p class="capitalize">{store.me.role}</p>
						</div>
						<button
							onclick={() => (pushOpen = true)}
							class="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-text"
							title="Notifications on this device"
							aria-label="Notifications on this device"
						>
							<Bell class="size-4" />
						</button>
						<button
							onclick={() => store.signOut()}
							class="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-text"
							title="Sign out"
							aria-label="Sign out"
						>
							<LogOut class="size-4" />
						</button>
					</div>
				{:else if store.me && !store.me.signin}
					<p class="leading-relaxed" title="Add a user to config.yaml to turn sign-in on">
						<span class="font-medium text-text">Sign-in is off.</span> Anyone who reaches this page is
						an admin; the terminal and remote desktop stay locked.
					</p>
				{/if}
				{#if store.alarms?.heartbeat?.on}
					{@const hb = store.alarms.heartbeat}
					<div
						class="flex items-center gap-2"
						title="Kastellan calls your dead man's switch every minute; if the calls stop, it warns you that the hub is gone."
					>
						<span
							class="size-1.5 rounded-full"
							style:background={hb.error
								? 'var(--warning)'
								: hb.last
									? 'var(--good)'
									: 'var(--offline)'}
						></span>
						{hb.error
							? `Heartbeat failing (${hb.error})`
							: hb.last
								? `Heartbeat sent ${ago(hb.last, store.now)}`
								: 'Heartbeat starting'}
					</div>
				{/if}
				<div class="flex items-center gap-2">
					<span
						class="size-1.5 rounded-full"
						style:background={link === 'up'
							? 'var(--good)'
							: link === 'down'
								? 'var(--critical)'
								: 'var(--offline)'}
					></span>
					{link === 'up'
						? 'Connected to the hub'
						: link === 'down'
							? 'Hub not reachable'
							: 'Connecting to the hub'}
					{#if store.health?.newer}
						<a
							class="tabular ml-auto font-medium text-accent hover:underline"
							href={store.health.newer.url}
							target="_blank"
							rel="noopener noreferrer"
							title="Kastellan {store.health.newer.version} is out: what changed"
						>
							v{store.health.version} → {store.health.newer.version}
						</a>
					{:else}
						<span class="tabular ml-auto text-faint">v{store.health?.version ?? '–'}</span>
					{/if}
				</div>
			</div>
		</aside>

		<!-- Top bar (narrow screens) -->
		<header
			class="sticky top-0 z-30 flex h-14 items-center gap-2.5 border-b border-line bg-surface/85 px-4 backdrop-blur-lg lg:hidden"
		>
			<Logo size={26} />
			<span class="font-semibold tracking-tight">Kastellan</span>
			<div class="ml-auto flex items-center gap-2">
				{#if store.health?.demo}
					<span class="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">
						Demo
					</span>
				{/if}
				{#if store.me?.user}
					<button
						onclick={() => (pushOpen = true)}
						class="rounded-lg p-1.5 text-muted hover:text-text"
						title="Notifications on this device"
						aria-label="Notifications on this device"
					>
						<Bell class="size-4" />
					</button>
					<button
						onclick={() => store.signOut()}
						class="rounded-lg p-1.5 text-muted hover:text-text"
						title="Sign out {store.me.user}"
						aria-label="Sign out"
					>
						<LogOut class="size-4" />
					</button>
				{/if}
			</div>
		</header>

		{#if store.health?.ping_refused}
			<div
				class="flex items-center gap-2 border-b border-line px-4 py-2.5 text-sm lg:px-8"
				style:background="color-mix(in srgb, var(--warning) 10%, var(--surface))"
				style:color="var(--warning-text)"
			>
				<TriangleAlert class="size-4 shrink-0" />
				The hub is not allowed to ping, so ping checks fail. On Linux: sysctl net.ipv4.ping_group_range="0
				2147483647".
			</div>
		{/if}

		{#if store.error}
			<div
				class="flex items-center gap-2 border-b border-line px-4 py-2.5 text-sm lg:px-8"
				style:background="color-mix(in srgb, var(--critical) 8%, var(--surface))"
				style:color="var(--critical-text)"
			>
				<TriangleAlert class="size-4 shrink-0" />
				The hub is not answering; what you see may be out of date.
			</div>
		{/if}

		<main class="mx-auto max-w-6xl px-4 pt-6 pb-28 sm:px-6 lg:px-10 lg:pt-10 lg:pb-16">
			{#if store.health?.vault_waiting?.length}
				<!-- After a restart: what needs a secret waits until its ring opens. -->
				<div
					class="mb-6 flex items-center gap-3 rounded-xl border px-4 py-3 text-sm"
					style="border-color: color-mix(in srgb, var(--warning) 40%, transparent); background: color-mix(in srgb, var(--warning) 8%, transparent)"
					role="status"
				>
					<LockKeyhole class="size-4 shrink-0" style="color: var(--warning-text)" />
					<span class="min-w-0 flex-1">
						<b style:color="var(--warning-text)">
							{store.health.vault_waiting.length > 1 ? 'Key rings' : 'A key ring'} Kastellan uses
							{store.health.vault_waiting.length > 1 ? 'are' : 'is'} locked ({store.health.vault_waiting.join(
								', '
							)}).
						</b>
						Checks go on; what needs a password, key or token waits (router logins, actions, the terminal,
						push){store.me?.role === 'admin' ? '.' : ': waiting for an admin to sign in.'}
					</span>
					{#if store.me?.role === 'admin'}
						<a
							href="/settings?tab=keys"
							class="shrink-0 text-sm font-medium"
							style:color="var(--warning-text)"
						>
							Unlock
						</a>
					{/if}
				</div>
			{/if}
			{#if store.health?.vault === 'damaged' && store.me?.role === 'admin'}
				<div
					class="mb-6 flex items-center gap-3 rounded-xl border px-4 py-3 text-sm"
					style="border-color: color-mix(in srgb, var(--critical) 40%, transparent); background: color-mix(in srgb, var(--critical) 8%, transparent)"
					role="alert"
				>
					<LockKeyhole class="size-4 shrink-0" style="color: var(--critical-text)" />
					<span class="min-w-0 flex-1">
						<b style:color="var(--critical-text)">The key rings can't be read.</b> What needs them waits.
					</span>
					<a
						href="/settings?tab=keys"
						class="shrink-0 text-sm font-medium"
						style:color="var(--critical-text)"
					>
						Details
					</a>
				</div>
			{/if}
			{@render children()}
		</main>

		<!-- Tab bar (narrow screens): the four places most looked at on a phone, the rest under More. -->
		<nav
			class="fixed inset-x-0 bottom-0 z-30 flex border-t border-line bg-surface/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-lg lg:hidden"
			aria-label="Main"
		>
			{#each phoneItems as item (item.href)}
				{@const Icon = ICON[item.icon]}
				{@const b = badge(item)}
				<a
					href={item.href}
					class="relative flex h-16 min-w-0 flex-1 flex-col items-center justify-center gap-1 px-1 text-[11px] font-medium {active(
						item
					)
						? 'text-accent'
						: 'text-muted'}"
					aria-current={active(item) ? 'page' : undefined}
				>
					<Icon class="size-5" strokeWidth={1.9} />
					<span class="max-w-full truncate">{item.short ?? item.label}</span>
					{#if b}
						<span
							class="absolute top-2 left-1/2 ml-2 size-2 rounded-full"
							style:background={toneColor[b.tone].fill}
							title={b.title}
						></span>
					{/if}
				</a>
			{/each}
			<button
				class="relative flex h-16 min-w-0 flex-1 flex-col items-center justify-center gap-1 px-1 text-[11px] font-medium {moreActive ||
				moreOpen
					? 'text-accent'
					: 'text-muted'}"
				aria-haspopup="dialog"
				aria-expanded={moreOpen}
				onclick={() => (moreOpen = true)}
			>
				<Ellipsis class="size-5" strokeWidth={1.9} />
				More
				{#if runner.working}
					<span
						class="absolute top-2 left-1/2 ml-2 size-2.5 animate-spin rounded-full border-2 border-line border-t-accent"
						title="Updates running"
					></span>
				{:else if more?.count}
					<span
						class="tabular absolute top-1.5 left-1/2 ml-1.5 min-w-4 rounded-full px-1 text-center text-[10px] leading-4 font-semibold {more.tone ===
						'neutral'
							? 'bg-surface-3 text-muted'
							: ''}"
						style:background={more.tone === 'neutral'
							? undefined
							: `color-mix(in srgb, ${toneColor[more.tone].fill} 22%, var(--surface))`}
						style:color={more.tone === 'neutral' ? undefined : toneColor[more.tone].text}
						title={more.title}
					>
						{more.count}
					</span>
				{:else if more}
					<span
						class="absolute top-2 left-1/2 ml-2 size-2 rounded-full"
						style:background={toneColor[more.tone].fill}
						title={more.title}
					></span>
				{/if}
			</button>
		</nav>
		{#if moreOpen}
			<!-- The rest of the places, as a sheet from the bottom. -->
			<dialog
				use:modal
				onclose={() => (moreOpen = false)}
				onclick={(e) => {
					if (e.target === e.currentTarget) moreOpen = false; // the backdrop
				}}
				class="mx-0 mt-auto mb-0 max-h-[85dvh] w-full max-w-none overflow-y-auto rounded-t-2xl border border-b-0 border-line bg-surface p-0 text-text shadow-pop backdrop:bg-black/40 lg:hidden"
				aria-label="More places"
			>
				<div class="flex items-center justify-between px-5 pt-4 pb-1">
					<h2 class="text-section">More</h2>
					<button
						class="grid size-8 place-items-center rounded-lg text-muted hover:text-text"
						onclick={() => (moreOpen = false)}
					>
						<X class="size-5" /><span class="sr-only">Close</span>
					</button>
				</div>
				<nav class="px-3 pb-[calc(env(safe-area-inset-bottom)+1rem)]" aria-label="More">
					{#each moreGroups as g, gi (gi)}
						{#if g.label}
							<p class="mt-3 mb-1 px-3 text-meta font-medium text-faint">{g.label}</p>
						{:else}
							<div class="mt-3 border-t border-line"></div>
						{/if}
						{#each g.items as item (item.href)}
							{@const Icon = ICON[item.icon]}
							{@const b = badge(item)}
							<a
								href={item.href}
								class="flex h-11 items-center gap-3 rounded-lg px-3 text-body font-medium {active(
									item
								)
									? 'bg-accent-soft text-accent'
									: 'text-text hover:bg-surface-2'}"
								aria-current={active(item) ? 'page' : undefined}
								onclick={() => (moreOpen = false)}
							>
								<Icon class="size-5 text-muted" strokeWidth={1.9} />
								{item.label}
								{#if b?.count}
									<span class="tabular ml-auto text-meta text-muted" title={b.title}>
										{b.count}
									</span>
								{:else if b}
									<span
										class="mr-1 ml-auto size-2 rounded-full"
										style:background={toneColor[b.tone].fill}
										title={b.title}
									></span>
								{/if}
							</a>
						{/each}
					{/each}
					{#if store.health?.demo || (store.me && !store.me.signin)}
						<div class="mt-4 space-y-2 border-t border-line px-3 pt-3 text-meta text-muted">
							{#if store.health?.demo}
								<p>
									<span class="font-medium text-text">Demo mode.</span> A made-up network with simulated
									results.
								</p>
							{/if}
							{#if store.me && !store.me.signin}
								<p>
									<span class="font-medium text-text">Sign-in is off.</span> Anyone who reaches this page
									is an admin; the terminal and remote desktop stay locked.
								</p>
							{/if}
						</div>
					{/if}
				</nav>
			</dialog>
		{/if}
	</div>
	{#if pushOpen}<PushDialog onclose={() => (pushOpen = false)} />{/if}
{/if}
