<script lang="ts">
	import type { FritzReport } from '#lib/api.js';

	/** A FRITZ!Box's WireGuard connections, read through its web interface's own API (AVM doesn't
	 * document it, so a FRITZ!OS update can change what shows here). */
	let { wg }: { wg: NonNullable<FritzReport['wireguard']> } = $props();
	const text = (v: unknown) => (v == null || v === '' ? null : String(v));
</script>

<p class="text-xs text-muted">
	{wg.server ? `${wg.server}${wg.port ? `, port ${wg.port}` : ''}` : 'WireGuard'}{wg.error
		? ` · ${wg.error}`
		: ''}
</p>
{#if wg.connections.length}
	<ul class="mt-1 space-y-0.5 text-sm">
		{#each wg.connections as c, i (i)}
			<li>
				<span class="font-medium">{c.name}</span>
				<span class="text-xs text-muted">
					{c.kind === 'network' ? 'a network' : 'a device'}{text(c.status)
						? ` · ${text(c.status)}`
						: ''}{text(c.last_handshake) ? ` · last handshake ${text(c.last_handshake)}` : ''}{text(
						c.endpoint
					)
						? ` · ${text(c.endpoint)}`
						: ''}
				</span>
			</li>
		{/each}
	</ul>
{:else}
	<p class="mt-1 text-sm text-muted">No WireGuard connections set up on it.</p>
{/if}
