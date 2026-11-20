<script lang="ts">
	import type { Cert } from '#lib/api.js';
	import Lock from '@lucide/svelte/icons/lock';
	import LockOpen from '@lucide/svelte/icons/lock-open';

	/** One line about an https service's certificate: valid and for how long, or why not. Coloured
	 * only when its alarms are on (a public name, or `tls: true`): a self-signed certificate on a
	 * device at home is normal, not a problem. */
	let { cert }: { cert: Cert } = $props();

	const days = $derived(cert.days_left);
	const left = $derived(
		days == null
			? ''
			: days < 0
				? 'ended'
				: days < 1
					? 'ends today'
					: `${days} ${days === 1 ? 'day' : 'days'}`
	);
	const text = $derived(cert.valid ? `valid · ${left}` : (cert.error ?? 'not valid'));
	const color = $derived(
		!cert.problem ? '' : !cert.valid ? 'var(--critical-text)' : 'var(--warning-text)'
	);
	const title = $derived(
		[
			cert.valid ? 'Certificate valid' : `Certificate not valid: ${cert.error ?? ''}`,
			cert.expires ? `ends ${cert.expires.slice(0, 10)}` : '',
			cert.issuer ? `from ${cert.issuer}` : '',
			cert.names.length ? `for ${cert.names.join(', ')}` : '',
			cert.watched ? 'alarms on' : 'alarms off (a name at home, or tls: false)'
		]
			.filter(Boolean)
			.join('\n')
	);
</script>

<span
	class="flex min-w-0 shrink-0 items-center gap-1 text-xs {color ? '' : 'text-faint'}"
	style:color={color || undefined}
	{title}
>
	{#if cert.valid}<Lock class="size-3 shrink-0" />{:else}<LockOpen class="size-3 shrink-0" />{/if}
	<span class="truncate">{text}</span>
</span>
