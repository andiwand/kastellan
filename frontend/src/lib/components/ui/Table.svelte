<script lang="ts" module>
	import type { Snippet } from 'svelte';

	/** One column. `priority` decides what a narrow screen keeps: 1 always, 2 from 640 px, 3 from
	 * 1024 px; a phone drops columns instead of scrolling sideways. Give the name column and the
	 * state priority 1, the one number that matters 1 or 2, the rest 3. */
	export interface Column<T> {
		key: string;
		label: string;
		priority?: 1 | 2 | 3;
		align?: 'left' | 'right';
		numeric?: boolean; // tabular numbers, right-aligned
		mono?: boolean; // identifiers: an address, an image
		width?: string; // a CSS width, e.g. '7rem'
		grow?: boolean; // takes the width that is left; long text is cut with "…"
		title?: string; // the header's tooltip: what the column means
		value?: (row: T) => string | number | null | undefined; // plain text ('–' when empty)
		cell?: Snippet<[T]>; // anything else: a badge, a link, two lines
	}
</script>

<script lang="ts" generics="T">
	import { goto } from '$app/navigation';
	import { SvelteSet } from 'svelte/reactivity';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';

	/** The table every list of like things uses: devices, containers, VMs, backups, sign-ins…
	 * One look (44 px rows, 13 px cells, muted 12 px header), columns that drop by priority on
	 * narrow screens, optional group rows (`group`: rows must come sorted by it), a link per row
	 * (`href`: the whole row opens it), and expandable rows (`expand`: details under the row,
	 * opened by its chevron). Entity tables (one per kind of thing) are built on this. */
	let {
		columns,
		rows,
		key,
		href,
		expand,
		expandable,
		group,
		empty = 'Nothing here.',
		label,
		bare = false
	}: {
		columns: Column<T>[];
		rows: T[];
		key: (row: T) => string;
		href?: (row: T) => string | null | undefined;
		expand?: Snippet<[T]>;
		expandable?: (row: T) => boolean;
		group?: (row: T) => string;
		empty?: string | Snippet;
		label?: string; // what the table lists, for screen readers
		bare?: boolean; // without the card around it
	} = $props();

	const open = new SvelteSet<string>();
	const PRIORITY = { 1: '', 2: 'hidden sm:table-cell', 3: 'hidden lg:table-cell' } as const;
	const span = $derived(columns.length + (expand ? 1 : 0));
	const canExpand = (row: T) => !!expand && (expandable?.(row) ?? true);

	function cellClass(c: Column<T>) {
		const right = c.align === 'right' || (c.numeric && c.align !== 'left');
		return [
			PRIORITY[c.priority ?? 1],
			right ? 'text-right' : '',
			c.numeric ? 'tabular' : '',
			c.mono ? 'font-mono' : '',
			c.grow ? 'w-full max-w-0' : ''
		].join(' ');
	}
	function toggle(row: T) {
		const k = key(row);
		if (open.has(k)) open.delete(k);
		else open.add(k);
	}
	// A click anywhere on a linked row opens it, unless it was on something of its own.
	function rowClick(e: MouseEvent, row: T) {
		const to = href?.(row);
		if (!to || (e.target as HTMLElement).closest('a, button, input, select, textarea, label'))
			return;
		if (e.metaKey || e.ctrlKey) window.open(to, '_blank');
		else goto(to);
	}
</script>

<div class={bare ? '' : 'card overflow-hidden'}>
	<table class="w-full text-cell" aria-label={label}>
		<thead>
			<tr class="border-b border-line text-left text-meta text-muted">
				{#each columns as c (c.key)}
					<th
						class="h-9 px-3 font-medium whitespace-nowrap first:pl-4 last:pr-4 {cellClass(c)}"
						style:width={c.width}
						title={c.title}
						scope="col"
					>
						{c.label}
					</th>
				{/each}
				{#if expand}<th class="w-10 pr-2"><span class="sr-only">Details</span></th>{/if}
			</tr>
		</thead>
		<tbody>
			{#each rows as row, i (key(row))}
				{@const g = group?.(row)}
				{#if group && (i === 0 || group(rows[i - 1]) !== g)}
					<tr class="border-b border-line bg-surface-2/60">
						<th
							colspan={span}
							scope="colgroup"
							class="px-4 py-1.5 text-left text-meta font-medium tracking-wide text-muted uppercase"
						>
							{g}
						</th>
					</tr>
				{/if}
				{@const link = href?.(row)}
				<tr
					class="h-row border-b border-line last:border-0 {link
						? 'cursor-pointer transition-colors hover:bg-surface-2/60'
						: ''}"
					onclick={(e) => rowClick(e, row)}
				>
					{#each columns as c, j (c.key)}
						<td class="px-3 py-2 align-middle first:pl-4 last:pr-4 {cellClass(c)}">
							<div class={c.grow ? 'min-w-0 truncate' : ''}>
								{#if c.cell}
									{@render c.cell(row)}
								{:else}
									{@const v = c.value?.(row)}
									{#if j === 0 && link}
										<a href={link} class="font-medium hover:underline">{v ?? '–'}</a>
									{:else if v == null || v === ''}
										<span class="text-faint">–</span>
									{:else}
										{v}
									{/if}
								{/if}
							</div>
						</td>
					{/each}
					{#if expand}
						<td class="w-10 pr-2 text-right">
							{#if canExpand(row)}
								<button
									class="grid size-7 place-items-center rounded-md text-faint hover:bg-surface-3 hover:text-text"
									aria-expanded={open.has(key(row))}
									aria-label="Details"
									onclick={() => toggle(row)}
								>
									<ChevronRight
										class="size-4 transition-transform {open.has(key(row)) ? 'rotate-90' : ''}"
									/>
								</button>
							{/if}
						</td>
					{/if}
				</tr>
				{#if expand && open.has(key(row))}
					<tr class="border-b border-line bg-surface-2/40 last:border-0">
						<td colspan={span} class="px-4 py-3">{@render expand(row)}</td>
					</tr>
				{/if}
			{:else}
				<tr>
					<td colspan={span} class="px-4 py-6 text-center text-body text-muted">
						{#if typeof empty === 'string'}{empty}{:else}{@render empty()}{/if}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
