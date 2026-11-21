<script lang="ts">
	import { more, type WebSetupView } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { onMount } from 'svelte';
	import CodeBlock from './CodeBlock.svelte';

	/** DNS and HTTPS for the site (web_setup.py): the hub at web.domain and device pages under
	 * it, behind a proxy that ends TLS. Whether the names resolve (the same as on the internet),
	 * the proxy's certificates, and the OpenWrt routers forwarding the zone to the site's DNS. */
	let view = $state<WebSetupView | null>(null);
	let error = $state<string | null>(null);
	let busy = $state(false);

	async function load(again = false) {
		busy = true;
		try {
			view = await more.webSetup(again);
			error = null;
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}
	onMount(() => load());
	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const mark = (ok: boolean | null | undefined) => (ok == null ? '–' : ok ? '✓' : '✗');
	const tone = (ok: boolean | null | undefined) =>
		ok == null ? undefined : ok ? 'var(--good-text)' : 'var(--warning-text)';
</script>

<div class="mt-6 max-w-3xl space-y-5">
	{#if error}<p class="text-sm" style:color="var(--critical-text)">{error}</p>{/if}
	{#if view && !view.domain}
		<section class="card p-5 text-sm text-muted">
			Configure a DNS zone and an HTTPS reverse proxy, then add
			<code>
				web: {'{'}domain: kastellan.&lt;zone&gt;, dns: &lt;the site's DNS server&gt;{'}'}
			</code>.
		</section>
	{:else if view}
		<section class="card p-5">
			<div class="flex flex-wrap items-baseline justify-between gap-2">
				<h2 class="text-sm font-semibold">Names</h2>
				<button class="btn btn-sm" disabled={busy} onclick={() => load(true)}>
					{busy ? 'Checking…' : 'Check again'}
				</button>
			</div>
			<p class="mt-1 text-xs text-muted">
				Hub: <span class="font-mono">{view.domain}</span>. Devices:
				<span class="font-mono">&lt;device&gt;.{view.domain}</span>. Compare local and public DNS
				answers for <span class="font-mono">{view.zone}</span>.
			</p>
			<ul class="mt-3 space-y-1 text-sm">
				{#each view.names ?? [] as n (n.name)}
					<li class="flex flex-wrap items-baseline gap-x-3">
						<span style:color={tone(n.ok && n.same)}>{mark(n.ok && n.same)}</span>
						<span class="font-mono text-xs">{n.name}</span>
						<span class="text-xs text-muted">
							{n.what} · {n.answers.length ? n.answers.join(', ') : 'no answer'}
							{#if n.same == null}<span class="text-faint"> · no public answer to compare</span>
							{:else if !n.same}<span style:color="var(--warning-text)">
									· the internet says {n.public.join(', ')}
								</span>{/if}
						</span>
					</li>
				{/each}
			</ul>
			<p class="mt-2 text-xs text-faint">
				Give the hub's name and its wildcard their own DNS records; a DNS-01 challenge can prevent
				the zone wildcard from covering them.
			</p>
		</section>

		<section class="card p-5">
			<h2 class="text-sm font-semibold">Certificates (the proxy's)</h2>
			<ul class="mt-2 space-y-1 text-sm">
				{#each view.certificates ?? [] as c (c.name)}
					{@const cert = c.certificate}
					<li class="flex flex-wrap items-baseline gap-x-3">
						<span style:color={tone(cert ? cert.valid && (cert.days_left ?? 99) >= 14 : false)}>
							{mark(cert ? cert.valid && (cert.days_left ?? 99) >= 14 : false)}
						</span>
						<span class="font-mono text-xs">{c.name}</span>
						<span class="text-xs text-muted">
							{#if !cert}no HTTPS answer{:else if !cert.valid}{cert.error}{:else}{cert.issuer} · {cert.days_left}
								days left{/if}
						</span>
					</li>
				{/each}
			</ul>
		</section>

		<section class="card p-5">
			<h2 class="text-sm font-semibold">Router DNS</h2>
			<p class="mt-1 text-xs text-muted">
				Allow the zone's private addresses and forward queries to the site's DNS server{view.dns_server
					? ` (${view.dns_server})`
					: ''} for local resolution during an internet outage.
			</p>
			<ul class="mt-2 space-y-1 text-sm">
				{#each view.routers ?? [] as r (r.device)}
					<li class="flex flex-wrap items-baseline gap-x-3">
						<span class="font-medium">{name(r.device)}</span>
						{#if r.error}
							<span class="text-xs text-muted">{r.error}</span>
						{:else if !r.dnsmasq}
							<span class="text-xs text-muted">no dnsmasq</span>
						{:else}
							<span class="text-xs" style:color={tone(r.rebind_ok)}>
								{mark(r.rebind_ok)} rebind exception
							</span>
							<span class="text-xs" style:color={tone(r.forward_ok)}>
								{mark(r.forward_ok)} forward{r.forwards?.length ? ` ${r.forwards.join(', ')}` : ''}
							</span>
							{#if r.local?.length}<span class="text-xs" style:color="var(--warning-text)">
									answers the zone itself ({r.local.join(', ')}): split DNS
								</span>{/if}
						{/if}
					</li>
				{:else}
					<li class="text-xs text-muted">No OpenWrt router with Kastellan's login.</li>
				{/each}
			</ul>
			{#if (view.routers ?? []).some((r) => r.dnsmasq && (!r.rebind_ok || !r.forward_ok))}
				<div class="mt-3"><CodeBlock text={view.commands ?? ''} /></div>
			{/if}
		</section>
		{#if view.checked}
			<p class="text-xs text-faint">Checked {new Date(view.checked).toLocaleString()}.</p>
		{/if}
	{/if}
</div>
