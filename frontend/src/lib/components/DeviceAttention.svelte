<script lang="ts">
	import type { Issue } from '#lib/attention.js';
	import { worst } from '#lib/states.js';
	import { count } from '#lib/glossary.js';
	import { issueHref, shortTitle, type SectionId } from '#lib/devicePage.js';
	import StateDot from './ui/StateDot.svelte';

	/** One line at the top of a device page when something about it needs attention: its issues
	 * (the same list the overview counts), each a link to the section on this page that shows
	 * it. Phones wrap it instead of cutting it off. */
	let {
		issues,
		device,
		name,
		shown
	}: { issues: Issue[]; device: string; name: string; shown: SectionId[] } = $props();
	const tone = $derived(worst(issues.map((i) => i.tone)));
</script>

{#if issues.length}
	<div class="card mb-4 flex items-center gap-3 px-4 py-2.5 text-body" role="status">
		<StateDot {tone} label={count(issues.length, 'issue')} pulse={tone === 'critical'} />
		<p class="min-w-0 flex-1 sm:truncate">
			{#each issues as i, n (i.key)}{#if n}<span class="hidden text-faint sm:inline">
						·
					</span>{/if}<a
					class="block hover:underline sm:inline"
					href={issueHref(i, device, shown)}
					title={i.detail || undefined}
				>
					{shortTitle(i.title, name)}
				</a>{/each}
		</p>
		{#if issues.length > 1}<span class="tabular shrink-0 text-meta text-muted">
				{count(issues.length, 'issue')}
			</span>{/if}
	</div>
{/if}
