import { describe, expect, it } from 'vitest';
import type { Finding, Vulns } from './api';
import { severityState, vulnRows } from './vulns';

const finding = (id: string, over: Partial<Finding> = {}): Finding => ({
	id,
	source: 'nvd',
	summary: '',
	score: null,
	severity: 'medium',
	published: null,
	url: '',
	aliases: [],
	fixed_in: null,
	exploited: false,
	confidence: 'exact',
	...over
});

describe('severityState', () => {
	it('is red for critical or exploited, amber for high, neutral otherwise', () => {
		expect(severityState(finding('a', { severity: 'critical' }))).toMatchObject({
			word: 'Critical',
			tone: 'critical'
		});
		expect(severityState(finding('a', { severity: 'low', exploited: true })).tone).toBe('critical');
		expect(severityState(finding('a', { severity: 'high' })).tone).toBe('warning');
		expect(severityState(finding('a', { severity: 'unknown' }))).toMatchObject({
			word: 'Unknown',
			tone: 'neutral'
		});
	});
});

describe('vulnRows', () => {
	const vulns: Vulns = {
		checked: '2026-11-22T10:00:00Z',
		items: [
			{ where: 'ssh', product: 'OpenSSH', findings: [finding('s1')] },
			{
				where: 'image nginx:1.25',
				product: 'nginx',
				findings: [
					finding('n1', { severity: 'low' }),
					finding('n2', { severity: 'high' }),
					finding('n3', { severity: 'medium', exploited: true }),
					finding('n4'),
					finding('n5'),
					finding('n6')
				]
			},
			{ where: 'firmware', product: 'OpenWrt', findings: [] }
		],
		unknown: [],
		errors: {}
	};

	it('puts the product with most findings first, exploited then most serious first', () => {
		const { rows, total, hidden } = vulnRows(vulns, false);
		expect(total).toBe(7);
		expect(rows.map((r) => r.id)).toEqual(['n3', 'n2', 'n4', 'n5', 'n6', 's1']);
		expect(hidden).toBe(1);
		expect(rows[0]).toMatchObject({ product: 'nginx', where: 'image nginx:1.25' });
	});

	it('shows every finding when asked', () => {
		const { rows, hidden } = vulnRows(vulns, true);
		expect(rows).toHaveLength(7);
		expect(hidden).toBe(0);
	});
});
