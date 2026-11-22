import type { Finding, Vulns } from '#lib/api.js';
import type { State } from '#lib/states.js';

/** A device's known vulnerabilities as rows of one table (VulnsList): by product, the products
 * with most findings first, within each exploited first, then by severity. */

const ORDER: Finding['severity'][] = ['critical', 'high', 'medium', 'low', 'unknown'];
const rank = (f: Finding) => (f.exploited ? -1 : ORDER.indexOf(f.severity));

/** A severity as a State word: critical red, high amber, the rest neutral (worth reading, not
 * alarming); an exploited one is critical whatever its score says. */
export function severityState(f: Finding): State {
	const word =
		f.severity === 'unknown' ? 'Unknown' : f.severity[0].toUpperCase() + f.severity.slice(1);
	if (f.exploited || f.severity === 'critical')
		return { word, tone: 'critical', title: f.exploited ? 'Exploited in the wild' : undefined };
	if (f.severity === 'high') return { word, tone: 'warning' };
	return { word, tone: 'neutral' };
}

export type VulnRow = Finding & { key: string; product: string; where: string };

/** The rows, `perProduct` of each product unless `all`; `hidden` says how many are left out. */
export function vulnRows(
	vulns: Vulns,
	all: boolean,
	perProduct = 5
): { rows: VulnRow[]; total: number; hidden: number } {
	const items = vulns.items
		.filter((i) => i.findings.length)
		.map((i) => ({ ...i, findings: [...i.findings].sort((a, b) => rank(a) - rank(b)) }))
		.sort((a, b) => b.findings.length - a.findings.length);
	const total = items.reduce((n, i) => n + i.findings.length, 0);
	const rows = items.flatMap((i) =>
		(all ? i.findings : i.findings.slice(0, perProduct)).map((f) => ({
			...f,
			key: `${i.where}/${f.id}`,
			product: i.product,
			where: i.where
		}))
	);
	return { rows, total, hidden: total - rows.length };
}
