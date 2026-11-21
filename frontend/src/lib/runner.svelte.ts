import { actStream, type ActResult } from '#lib/api.js';
import { store } from '#lib/state.svelte.js';

/** One thing the actor does on a host (an update from the Updates page or a container's row).
 * Shared by the Updates page, a device's containers and its system updates, so all show the
 * same state, also when moving between pages while it runs. */
export type Job =
	| { device: string; kind: 'container'; name: string }
	| { device: string; kind: 'restart'; name: string }
	| { device: string; kind: 'packages' }
	| { device: string; kind: 'reboot' };

export interface LogLine {
	at: number; // ms since the epoch
	text: string;
}

export interface JobState {
	state: 'queued' | 'running' | 'done' | 'failed';
	label: string; // what and where, for lists ("NAS: container app")
	text?: string; // the outcome in a sentence (done or failed)
	log: LogLine[]; // queued, started, each step the host reported, the outcome
	queued: number;
	started?: number;
	ended?: number;
	result?: ActResult;
}

export const jobKey = (j: Job) => `${j.device}:${j.kind}${'name' in j ? `:${j.name}` : ''}`;

const ABILITY = {
	container: 'update-container',
	restart: 'restart-container',
	packages: 'upgrade-packages',
	reboot: 'reboot'
} as const;

function labelOf(j: Job): string {
	const where = store.byId?.get(j.device)?.name ?? j.device;
	const what =
		j.kind === 'container'
			? `update ${j.name}`
			: j.kind === 'restart'
				? `restart ${j.name}`
				: j.kind === 'packages'
					? 'system updates'
					: 'reboot';
	return `${where}: ${what}`;
}

/** The answer in a sentence. */
function sentence(j: Job, r: ActResult): string {
	if (j.kind === 'container')
		return r.updated ? `Updated (${r.from} → ${r.to}).` : `Nothing to do: ${r.why ?? 'newest'}.`;
	if (j.kind === 'restart') return 'Restarted.';
	if (j.kind === 'packages') {
		const n = r.upgraded ?? 0;
		return `${n} ${n === 1 ? 'package' : 'packages'} updated${r.reboot ? '; it wants a reboot now' : ''}.`;
	}
	return 'Rebooting in a minute (cancel on the host: shutdown -c).';
}

// A reboot goes last on its host: after the packages and containers queued with it.
const rank = (j: Job) => (j.kind === 'reboot' ? 1 : 0);

class Runner {
	states = $state<Record<string, JobState>>({});
	/** The jobs queued since the queue was last idle: what the progress counts. */
	batch = $state<string[]>([]);
	#hosts = new Map<string, Job[]>(); // waiting, per device
	#busy = new Set<string>(); // devices working through their list

	/** Queue jobs: one at a time per device (its actor does one thing at a time anyway), devices
	 * side by side. A job already queued or running isn't added again. */
	add(jobs: Job[]) {
		if (!this.working) this.batch = [];
		const now = Date.now();
		for (const j of jobs) {
			const k = jobKey(j);
			const s = this.states[k]?.state;
			if (s === 'queued' || s === 'running') continue;
			this.states[k] = {
				state: 'queued',
				label: labelOf(j),
				queued: now,
				log: [{ at: now, text: 'waiting its turn on this host' }]
			};
			if (!this.batch.includes(k)) this.batch.push(k);
			const list = this.#hosts.get(j.device) ?? [];
			list.push(j);
			list.sort((a, b) => rank(a) - rank(b));
			this.#hosts.set(j.device, list);
		}
		for (const device of this.#hosts.keys()) if (!this.#busy.has(device)) this.#drain(device);
	}

	async #drain(device: string) {
		this.#busy.add(device);
		let failed = false; // something on this host failed: no reboot after it
		try {
			for (let j = this.#hosts.get(device)?.shift(); j; j = this.#hosts.get(device)?.shift()) {
				const job = j;
				const s = this.states[jobKey(job)];
				const say = (text: string) => s.log.push({ at: Date.now(), text });
				if (job.kind === 'reboot' && failed) {
					// An upgrade that failed (or that the host stopped waiting for) may still run.
					s.text = 'skipped: an earlier update there failed';
					s.state = 'failed';
					s.ended = Date.now();
					say(s.text);
					continue;
				}
				s.state = 'running';
				s.started = Date.now();
				say('started');
				try {
					const result = await actStream(
						job.device,
						ABILITY[job.kind],
						'name' in job ? job.name : null,
						say
					);
					s.result = result;
					s.text = sentence(job, result);
					s.state = 'done';
				} catch (e) {
					s.text = (e as Error).message;
					s.state = 'failed';
					failed = true;
				}
				s.ended = Date.now();
				say(s.state === 'done' ? `done: ${s.text}` : `failed: ${s.text}`);
				await store.refresh();
			}
		} finally {
			this.#busy.delete(device);
			this.#hosts.delete(device);
		}
	}

	/** Not started yet: taken off the list. */
	cancel(j: Job) {
		const k = jobKey(j);
		if (this.states[k]?.state !== 'queued') return;
		const list = this.#hosts.get(j.device) ?? [];
		this.#hosts.set(
			j.device,
			list.filter((x) => jobKey(x) !== k)
		);
		delete this.states[k];
		this.batch = this.batch.filter((x) => x !== k);
	}

	state(j: Job): JobState | undefined {
		return this.states[jobKey(j)];
	}

	/** Where the current (or last) batch stands. */
	get progress() {
		const jobs = this.batch.map((k) => this.states[k]).filter((s) => s !== undefined);
		const count = (state: JobState['state']) => jobs.filter((s) => s.state === state).length;
		return {
			total: jobs.length,
			done: count('done'),
			failed: count('failed'),
			waiting: count('queued'),
			running: jobs.filter((s) => s.state === 'running')
		};
	}

	get working() {
		return Object.values(this.states).some((s) => s.state === 'queued' || s.state === 'running');
	}

	/** The finished batch off the progress card (each job keeps its state and log). */
	clear() {
		if (!this.working) this.batch = [];
	}
}

export const runner = new Runner();
