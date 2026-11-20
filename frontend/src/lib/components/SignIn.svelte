<script lang="ts">
	import { api, SignedOut } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import Logo from './Logo.svelte';

	let user = $state('');
	let password = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = null;
		try {
			await api.login(user.trim(), password);
			password = '';
			await store.refresh();
			store.refreshHistory();
		} catch (err) {
			error = err instanceof SignedOut ? 'Wrong user or password.' : (err as Error).message;
		} finally {
			busy = false;
		}
	}
</script>

<div class="grid min-h-dvh place-items-center bg-bg px-4">
	<form onsubmit={submit} class="card w-full max-w-sm p-7 shadow-pop">
		<div class="flex items-center gap-2.5">
			<Logo size={30} />
			<span class="text-lg font-semibold tracking-tight">Kastellan</span>
		</div>
		<h1 class="mt-6 text-xl font-semibold tracking-tight">Sign in</h1>
		<p class="mt-1 text-sm text-muted">to see and look after the devices on this network.</p>

		<label class="mt-6 block text-sm font-medium" for="user">User</label>
		<input
			id="user"
			bind:value={user}
			autocomplete="username"
			autocapitalize="none"
			spellcheck="false"
			required
			class="mt-1.5 h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
		/>
		<label class="mt-4 block text-sm font-medium" for="password">Password</label>
		<input
			id="password"
			type="password"
			bind:value={password}
			autocomplete="current-password"
			required
			class="mt-1.5 h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
		/>
		{#if error}
			<p class="mt-4 text-sm" style:color="var(--critical-text)" role="alert">{error}</p>
		{/if}
		<button
			type="submit"
			disabled={busy}
			class="mt-6 h-10 w-full rounded-xl bg-accent text-sm font-medium text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-60"
		>
			{busy ? 'Signing in…' : 'Sign in'}
		</button>
	</form>
</div>
