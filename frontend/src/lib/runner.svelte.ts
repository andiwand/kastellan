import { actStream, type ActResult } from '#lib/api.js';
import { store } from '#lib/state.svelte.js';

/** One thing the actor does on a host (an update, a container's or a VM's start or stop, ...).
 * Shared by the Updates, Containers and Virtual machines pages and a device's own lists, so all
 * show the same state, also when moving between pages while it runs. */
export type Job =
	| {
			device: string;
			kind: 'container';
			name: string;
			tag?: string; // another tag of its image (one the hub found); none: a new build
	  }
	| {
			device: string;
			kind: 'rollback' | 'restart' | 'start' | 'stop';
			name: string;
	  }
	| {
			device: string;
			kind: 'vm-start' | 'vm-shutdown' | 'vm-reboot' | 'vm-force-off';
			name: string;
	  }
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

const ABILITY: Record<Job['kind'], string> = {
	container: 'update-container',
	rollback: 'rollback-container',
	restart: 'restart-container',
	start: 'start-container',
	stop: 'stop-container',
	'vm-start': 'vm-start',
	'vm-shutdown': 'vm-shutdown',
	'vm-reboot': 'vm-reboot',
	'vm-force-off': 'vm-force-off',
	packages: 'upgrade-packages',
	reboot: 'reboot'
};

const VERB: Record<Job['kind'], string> = {
	container: 'update',
	rollback: 'go back to the previous image of',
	restart: 'restart',
	start: 'start',
	stop: 'stop',
	'vm-start': 'start VM',
	'vm-shutdown': 'shut down VM',
	'vm-reboot': 'restart VM',
	'vm-force-off': 'force off VM',
	packages: 'system updates',
	reboot: 'reboot'
};

function labelOf(j: Job): string {
	const where = store.byId?.get(j.device)?.name ?? j.device;
	const to = j.kind === 'container' && j.tag ? ` to ${j.tag}` : '';
	return `${where}: ${VERB[j.kind]}${'name' in j ? ` ${j.name}` : ''}${to}`;
}

/** The answer in a sentence. */
function sentence(j: Job, r: ActResult): string {
	if (j.kind === 'container')
		return r.updated
			? r.to_tag
				? `Updated to ${r.to_tag} (from ${r.from_tag}).`
				: `Updated (${r.from} → ${r.to}).`
			: `Nothing to do: ${r.why ?? 'newest'}.`;
	if (j.kind === 'rollback') return `Back to the previous image (${r.from} → ${r.to}).`;
	if (j.kind === 'restart') return 'Restarted.';
	if (j.kind === 'start') return 'Started.';
	if (j.kind === 'stop') return 'Stopped.';
	// libvirt can't tell whether a guest restarted (it runs throughout): say what was done.
	if (j.kind === 'vm-reboot') return 'Asked it to restart.';
	if (j.kind.startsWith('vm-'))
		return `${j.kind === 'vm-force-off' ? 'Forced off' : 'Done'}${r.state ? `: it is ${r.state}` : ''}.`;
	if (j.kind === 'packages') {
		const n = r.upgraded ?? 0;
		return `${n} ${n === 1 ? 'package' : 'packages'} updated${r.reboot ? '; it wants a reboot now' : ''}.`;
	}
	return 'Rebooting in a minute (cancel on the host: shutdown -c).';
}

const SHOWN_DONE = 60_000; // how long a row says what was done

// A host's reboot goes last on it: after the packages, containers and VMs queued with it.
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
						say,
						job.kind === 'container' ? job.tag : undefined
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

	state(j: Job): JobState | undefined {
		return this.states[jobKey(j)];
	}

	/** Of a thing's jobs (a container's update, restart, start, stop), the latest: what its row
	 * shows. A job done a while ago isn't news any more; one that failed stays until dismissed. */
	latest(jobs: Job[], now = Date.now()): { job: Job; state: JobState } | undefined {
		const found = jobs
			.map((job) => ({ job, state: this.states[jobKey(job)] }))
			.filter((x): x is { job: Job; state: JobState } => x.state !== undefined)
			.sort((a, b) => b.state.queued - a.state.queued)[0];
		if (found?.state.state === 'done' && now - (found.state.ended ?? now) > SHOWN_DONE) return;
		return found;
	}

	/** A finished job off its row (its log goes too). */
	dismiss(j: Job) {
		const k = jobKey(j);
		if (['done', 'failed'].includes(this.states[k]?.state ?? '')) {
			delete this.states[k];
			this.batch = this.batch.filter((x) => x !== k);
		}
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
