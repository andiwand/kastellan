<script lang="ts">
	import type { FritzReport } from '#lib/api.js';

	/** FRITZ!Mesh as the FRITZ!Box reports it: its members (master, repeaters) and how each
	 * hangs on the mesh (Wi-Fi with signal and speed, or cable). */
	let { mesh }: { mesh: NonNullable<FritzReport['mesh']> } = $props();
	const name = (uid: string) => mesh.members.find((m) => m.uid === uid)?.name ?? uid;
	const mbit = (kbit: number | null) => (kbit == null ? '–' : `${Math.round(kbit / 1000)} Mbit/s`);
</script>

<ul class="mt-1 space-y-0.5 text-sm">
	{#each mesh.members as m (m.uid)}
		<li>
			<span class="font-medium">{m.name ?? m.mac}</span>
			<span class="text-xs text-muted">
				{m.role === 'master' ? 'mesh master' : 'mesh repeater'}{m.model ? ` · ${m.model}` : ''}
			</span>
		</li>
	{/each}
</ul>
{#if mesh.links.length}
	<ul class="mt-1 space-y-0.5 text-xs text-muted">
		{#each mesh.links as l, i (i)}
			<li>
				{name(l.a)} ↔ {name(l.b)}: {l.type === 'WLAN'
					? 'Wi-Fi'
					: l.type === 'LAN'
						? 'cable'
						: l.type}
				{#if l.type === 'WLAN'}· ↓ {mbit(l.rate_rx)} ↑ {mbit(l.rate_tx)}{l.signal != null
						? ` · ${l.signal} dBm`
						: ''}{/if}
			</li>
		{/each}
	</ul>
{/if}
