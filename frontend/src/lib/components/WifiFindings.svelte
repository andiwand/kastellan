<script lang="ts">
	import { more, type WifiFinding } from '#lib/api.js';
	import type { Issue } from '#lib/attention.js';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import { openFindings } from '#lib/wifi.js';
	import IssueRow from './ui/IssueRow.svelte';

	/** What is off about the Wi-Fi as sentences, each with what fixes it on its row: the open
	 * Wi-Fi alarms first (`issues`, from store.attention), then the findings (wifi.findings) an
	 * alarm doesn't already say. `device` keeps those about one device (its page's section).
	 * Actions the Wi-Fi page runs itself (suggest channels, show weak clients, scan, change a
	 * network, show the radios) go to `onaction`; without it they link to the Wi-Fi page. Turning
	 * off WPS runs here; setting up and roaming assistance link to where they are done. */
	let {
		findings,
		issues = [],
		device,
		onaction
	}: {
		findings: WifiFinding[];
		issues?: Issue[];
		device?: string;
		onaction?: (f: WifiFinding) => void;
	} = $props();

	const shownIssues = $derived(device ? issues.filter((i) => i.device === device) : issues);
	const shown = $derived(
		openFindings(
			findings,
			issues.map((i) => i.key),
			device
		)
	);
	const blocked = $derived(store.editBlocked);
	let notes = $state<Record<string, { ok: boolean; text: string }>>({});

	const LABEL: Record<string, string> = {
		plan: 'Suggest channels',
		network: 'Change',
		'wps-off': 'Turn off WPS',
		setup: 'Set up',
		roaming: 'Roaming assistance',
		clients: 'Show them',
		scan: 'Scan now',
		radios: 'Show radios'
	};
	const ANCHOR: Record<string, string> = {
		plan: '#channel-plan',
		network: '#networks',
		clients: '#clients',
		scan: '',
		radios: '#access-points'
	};
	// What changes a device needs a signed-in admin (not in demo mode).
	const CHANGES = new Set(['network', 'wps-off', 'scan']);

	function href(f: WifiFinding): string | null {
		const a = f.action;
		if (!a) return null;
		if (a.kind === 'setup') return `/devices/${encodeURIComponent(a.device)}?tab=setup`;
		if (a.kind === 'roaming') return '/devices?section=groups&tab=setup';
		if (!onaction) return `/wifi${ANCHOR[a.kind] ?? ''}`;
		return null;
	}

	async function act(f: WifiFinding) {
		const a = f.action;
		if (a?.kind === 'wps-off') {
			try {
				await more.fritzWpsOff(a.device);
				notes[f.key] = { ok: true, text: 'WPS is off; it shows within a minute.' };
			} catch (e) {
				notes[f.key] = { ok: false, text: (e as Error).message };
			}
		} else onaction?.(f);
	}
</script>

{#if shownIssues.length || shown.length}
	<ul class="card divide-y divide-line">
		{#each shownIssues as i (i.key)}
			<IssueRow
				tone={i.tone}
				title={i.title}
				detail={i.detail}
				meta={i.since ? ago(i.since, store.now) : undefined}
				note={i.alarm?.acknowledged_by
					? `${i.alarm.acknowledged_by} is on it`
					: i.alarm?.held
						? 'in maintenance · not sent'
						: 'alarm'}
			/>
		{/each}
		{#each shown as f (f.key)}
			{@const link = href(f)}
			{@const note = notes[f.key]}
			<IssueRow
				tone={f.level === 'warning' ? 'warning' : 'neutral'}
				title={f.text}
				detail={note?.text ?? f.fix}
			>
				{#snippet action()}
					{#if f.action && link}
						<a class="btn btn-sm" href={link}>{LABEL[f.action.kind]}</a>
					{:else if f.action && !note?.ok}
						{@const needs = CHANGES.has(f.action.kind) && blocked}
						<button
							class="btn btn-sm"
							disabled={!!needs}
							title={needs ? `Can't change here: ${blocked}` : undefined}
							onclick={() => act(f)}
						>
							{LABEL[f.action.kind]}
						</button>
					{/if}
				{/snippet}
			</IssueRow>
		{/each}
	</ul>
{/if}
