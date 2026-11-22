import type { InternetView } from './api';
import { bitrate, ms } from './format';
import { count } from './glossary';
import { worst, type Tone } from './states';

/** Where the internet is slow or down, in plain words, from what the Internet page already
 * measures (internet.py): the router answering, the line as the router tells it, the
 * provider's first routers, the outside addresses and name resolution, plus Wi-Fi's issues
 * (the hub measures from a wired place, so Wi-Fi is judged by its findings, never by a number)
 * and the latest speed test (latency under load). */

/** One link of the way out, in order: Wi-Fi, router, line, provider, internet, names. */
export interface Hop {
	id: 'wifi' | 'router' | 'line' | 'provider' | 'internet' | 'dns';
	label: string;
	value: string; // one number or word
	tone: Tone; // neutral: not measured from here
	seen: boolean; // false: not seen from the hub (drawn dashed)
	title?: string;
}

export interface Diagnosis {
	tone: Tone;
	headline: string; // one sentence: where the problem is, or that the line is fine
	notes: string[]; // short extra sentences (busy line, bufferbloat)
	wifi: number; // Wi-Fi issues (the page links to /wifi)
	hops: Hop[];
}

// Above these a link is slow (internet.py marks the line itself slow at 150 ms or 10 % loss).
export const SLOW = { router: 20, provider: 60, internet: 150, dns: 300, loss: 0.1 };

const pct = (v: number) => `${Math.round(v * 100)} %`;

export function diagnose(
	view: Pick<InternetView, 'now' | 'speedtests' | 'speedtest'>,
	wifiIssues: Tone[] = [],
	routerName = 'Router'
): Diagnosis {
	const now = view.now;
	const step = now?.state === 'down' ? now.step : null;
	const down = (id: string) => step === id;

	const router: Hop = {
		id: 'router',
		label: routerName,
		value: down('router') ? 'no answer' : ms(now?.router_ms),
		tone: down('router')
			? 'critical'
			: now?.router_ms == null
				? 'neutral'
				: now.router_ms > SLOW.router
					? 'warning'
					: 'good',
		seen: now?.router_ms != null || down('router'),
		title: 'How fast the router answers the hub'
	};
	const line: Hop = {
		id: 'line',
		label: 'Line',
		value: down('line') || now?.line === false ? 'not connected' : now?.line ? 'connected' : '–',
		tone: down('line') || now?.line === false ? 'critical' : now?.line ? 'good' : 'neutral',
		seen: now?.line != null,
		title: 'The connection as the router tells it'
	};
	const providerSlow = (now?.hop_ms ?? 0) > SLOW.provider || (now?.hop_loss ?? 0) > SLOW.loss;
	const provider: Hop = {
		id: 'provider',
		label: 'Provider',
		value: down('provider') ? 'no answer' : ms(now?.hop_ms),
		tone: down('provider')
			? 'critical'
			: now?.hop_ms == null
				? 'neutral'
				: providerSlow
					? 'warning'
					: 'good',
		seen: now?.hop_ms != null || down('provider'),
		title: 'The provider’s first routers on the way out'
	};
	const lossy = (now?.loss ?? 0) > SLOW.loss;
	const internet: Hop = {
		id: 'internet',
		label: 'Internet',
		value: down('internet') ? 'no answer' : ms(now?.ms),
		tone: down('internet')
			? 'critical'
			: now?.ms == null
				? 'neutral'
				: (now.ms ?? 0) > SLOW.internet || lossy
					? 'warning'
					: 'good',
		seen: now?.ms != null || down('internet'),
		title: 'Addresses of several providers outside'
	};
	const dns: Hop = {
		id: 'dns',
		label: 'Names',
		value: down('dns') ? 'fail' : ms(now?.dns_ms),
		tone: down('dns')
			? 'critical'
			: now?.dns_ms == null
				? 'neutral'
				: now.dns_ms > SLOW.dns
					? 'warning'
					: 'good',
		seen: now?.dns_ms != null || down('dns'),
		title: 'Name resolution (DNS)'
	};
	const wifiTone = worst(wifiIssues);
	const wifi: Hop = {
		id: 'wifi',
		label: 'Wi-Fi',
		value: wifiIssues.length ? count(wifiIssues.length, 'issue') : 'not measured',
		tone: wifiIssues.length ? wifiTone : 'neutral',
		seen: false,
		title: 'The hub is wired: Wi-Fi is judged by its findings, not measured from here'
	};
	const hops = [
		wifi,
		router,
		...(line.seen || down('line') ? [line] : []),
		provider,
		internet,
		dns
	];

	const notes: string[] = [];
	const latest = [...(view.speedtests ?? [])].sort((a, b) => b.t - a.t)[0];
	if (latest?.ms_idle && latest.ms_loaded && latest.ms_loaded > latest.ms_idle * 4)
		notes.push(
			`Under load, latency rises from ${ms(latest.ms_idle)} to ${ms(latest.ms_loaded)} (bufferbloat): calls and games stutter while something downloads.`
		);
	const booked = view.speedtest?.booked_down;
	if (booked && now?.down != null && now.down > booked * 1e6 * 0.8)
		notes.push(`The line is busy: ${bitrate(now.down)} of ${booked} Mbit/s in use.`);

	let headline: string;
	let tone: Tone;
	if (!now || now.state === 'unknown') {
		tone = 'neutral';
		headline = now?.note ? `Not read: ${now.note}.` : 'Not read yet.';
	} else if (now.state === 'down') {
		tone = 'critical';
		headline =
			{
				router: `Down at home: ${routerName} doesn’t answer the hub.`,
				line: `Down at the line: ${routerName} has no internet connection; the line or the provider.`,
				provider:
					'Down at the provider: its first routers don’t answer. Your home network is fine.',
				internet:
					'Down beyond the provider: nothing outside answers, though the provider’s routers do.',
				dns: 'Names can’t be resolved: addresses work, but the name server (router or provider) doesn’t answer.'
			}[step ?? ''] ?? `Down: ${now.why ?? 'nothing outside answers'}.`;
	} else if (now.state === 'degraded') {
		tone = 'warning';
		if (router.tone === 'warning')
			headline = `Slow at home: ${routerName} answers in ${ms(now.router_ms)} (usually under 5 ms).`;
		else if (provider.tone === 'warning')
			headline = `Slow at the provider: its first routers answer in ${ms(now.hop_ms)}${now.hop_loss ? ` and lose ${pct(now.hop_loss)}` : ''}; ${routerName} is fine.`;
		else if (!lossy && (now.ms ?? 0) <= SLOW.internet && (now.v6_loss ?? 0) > SLOW.loss)
			headline = `Slow over IPv6: ${pct(now.v6_loss!)} of its answers are lost; IPv4 is fine.`;
		else
			headline = `Slow beyond the provider: the internet answers in ${ms(now.ms)}${lossy ? ` and loses ${pct(now.loss!)}` : ''}; ${routerName}${provider.seen ? ' and the provider are' : ' is'} fine.`;
	} else {
		tone = 'good';
		const parts = [
			router.seen ? `${routerName} ${ms(now.router_ms)}` : null,
			provider.seen ? `provider ${ms(now.hop_ms)}` : null,
			`internet ${ms(now.ms)}`
		].filter(Boolean);
		headline = `The line is fine: ${parts.join(', ')}.`;
		if (dns.tone === 'warning') {
			tone = 'warning';
			headline = `The line is fine, but names resolve slowly (${ms(now.dns_ms)}).`;
		}
	}
	return { tone, headline, notes, wifi: wifiIssues.length, hops };
}
