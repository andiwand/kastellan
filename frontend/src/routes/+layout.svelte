<script lang="ts">
	import './layout.css';
	import { page } from '$app/state';
	import { store } from '#lib/state.svelte.js';
	import { needsAttention } from '#lib/status.js';
	import Logo from '#lib/components/Logo.svelte';
	import LayoutDashboard from '@lucide/svelte/icons/layout-dashboard';
	import List from '@lucide/svelte/icons/list';
	import Layers from '@lucide/svelte/icons/layers';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import LogOut from '@lucide/svelte/icons/log-out';
	import SignIn from '#lib/components/SignIn.svelte';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	$effect(() => store.use());

	const nav = [
		{ href: '/', label: 'Overview', icon: LayoutDashboard },
		{ href: '/devices', label: 'Devices', icon: List },
		{ href: '/services', label: 'Services', icon: Layers },
		{ href: '/vpn', label: 'VPN', icon: KeyRound }
	] as { href: string; label: string; short?: string; icon: typeof List }[];
	const active = (href: string) =>
		href === '/' ? page.url.pathname === '/' : page.url.pathname.startsWith(href);
	const attention = $derived((store.devices ?? []).filter(needsAttention).length);
	const connected = $derived(!store.error && store.updated != null);
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
			<a href="/" class="flex h-16 items-center gap-2.5 px-5">
				<Logo />
				<span class="text-[15px] font-semibold tracking-tight">Kastellan</span>
			</a>
			<nav class="flex flex-col gap-0.5 px-3 pt-2" aria-label="Main">
				{#each nav as item (item.href)}
					<a
						href={item.href}
						class="group flex h-9 items-center gap-3 rounded-lg px-3 text-sm font-medium transition-colors {active(
							item.href
						)
							? 'bg-accent-soft text-accent'
							: 'text-muted hover:bg-surface-2 hover:text-text'}"
						aria-current={active(item.href) ? 'page' : undefined}
					>
						<item.icon class="size-4" strokeWidth={1.9} />
						{item.label}
						{#if item.href === '/devices' && attention}
							<span
								class="tabular ml-auto rounded-full px-1.5 text-[11px] leading-5 font-semibold"
								style:background="color-mix(in srgb, var(--critical) 14%, transparent)"
								style:color="var(--critical-text)"
							>
								{attention}
							</span>
						{/if}
					</a>
				{/each}
			</nav>
			<div class="mt-auto space-y-3 border-t border-line p-4 text-xs text-muted">
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
				<div class="flex items-center gap-2">
					<span
						class="size-1.5 rounded-full"
						style:background={connected ? 'var(--good)' : 'var(--critical)'}
					></span>
					{connected ? 'Connected to the hub' : 'Hub not reachable'}
					<span class="tabular ml-auto text-faint">v{store.health?.version ?? '–'}</span>
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
			{@render children()}
		</main>

		<!-- Tab bar (narrow screens) -->
		<nav
			class="fixed inset-x-0 bottom-0 z-30 grid grid-cols-4 border-t border-line bg-surface/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-lg lg:hidden"
			aria-label="Main"
		>
			{#each nav as item (item.href)}
				<a
					href={item.href}
					class="relative flex h-16 flex-col items-center justify-center gap-1 text-[11px] font-medium {active(
						item.href
					)
						? 'text-accent'
						: 'text-muted'}"
					aria-current={active(item.href) ? 'page' : undefined}
				>
					<item.icon class="size-5" strokeWidth={1.9} />
					{item.short ?? item.label}
					{#if item.href === '/devices' && attention}
						<span
							class="absolute top-2 left-1/2 ml-2 size-2 rounded-full"
							style:background="var(--critical)"
						></span>
					{/if}
				</a>
			{/each}
		</nav>
	</div>
{/if}
