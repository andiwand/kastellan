import { describe, expect, it, vi } from 'vitest';

// What the actor was asked, in order, with a moment between start and end; two steps each.
const calls: string[] = [];
vi.mock('#lib/api.js', () => ({
	actStream: async (
		device: string,
		ability: string,
		name: string | null,
		onStep: (t: string) => void
	) => {
		const what = `${device} ${ability}${name ? ` ${name}` : ''}`;
		calls.push(`${what} start`);
		onStep('pulling');
		await new Promise((r) => setTimeout(r, 5));
		onStep('starting');
		calls.push(`${what} end`);
		if (name === 'broken') throw new Error('pull: manifest unknown');
		return { updated: true, from: 'a', to: 'b', upgraded: 2 };
	}
}));
vi.mock('#lib/state.svelte.js', () => ({ store: { refresh: async () => {} } }));

const { runner } = await import('./runner.svelte.js');
const idle = () =>
	vi.waitFor(() => {
		if (runner.working) throw new Error('still working');
	});

describe('runner', () => {
	it('runs a host one thing at a time, a reboot last, hosts side by side', async () => {
		runner.add([
			{ device: 'nas', kind: 'reboot' },
			{ device: 'nas', kind: 'packages' },
			{ device: 'pi', kind: 'container', name: 'app' },
			{ device: 'nas', kind: 'container', name: 'db' }
		]);
		expect(runner.progress).toMatchObject({ total: 4, done: 0, failed: 0 });
		await idle();
		const nas = calls.filter((c) => c.startsWith('nas'));
		expect(nas).toEqual([
			'nas upgrade-packages start',
			'nas upgrade-packages end',
			'nas update-container db start',
			'nas update-container db end',
			'nas reboot start',
			'nas reboot end'
		]);
		// pi didn't wait for nas: it started before nas's first job ended.
		expect(calls.indexOf('pi update-container app start')).toBeLessThan(
			calls.indexOf('nas upgrade-packages end')
		);
		expect(runner.progress).toMatchObject({ total: 4, done: 4, failed: 0, waiting: 0 });
		const db = runner.state({ device: 'nas', kind: 'container', name: 'db' })!;
		expect(db.log.map((l) => l.text)).toEqual([
			'waiting its turn on this host',
			'started',
			'pulling',
			'starting',
			'done: Updated (a → b).'
		]);
		expect(db.ended! >= db.started! && db.started! >= db.queued).toBe(true);
	});

	it('never queues the same thing twice, says why one failed, starts a new batch when idle', async () => {
		calls.length = 0;
		const job = { device: 'pi', kind: 'container', name: 'broken' } as const;
		runner.add([job, job]);
		runner.add([job]);
		expect(runner.progress.total).toBe(1); // the earlier batch was done: a new one
		await idle();
		expect(calls.filter((c) => c.endsWith('start'))).toHaveLength(1);
		expect(runner.state(job)).toMatchObject({ state: 'failed', text: 'pull: manifest unknown' });
		expect(runner.progress).toMatchObject({ total: 1, done: 0, failed: 1 });
		runner.clear();
		expect(runner.progress.total).toBe(0);
	});

	it("skips a host's reboot after an update there failed", async () => {
		calls.length = 0;
		runner.add([
			{ device: 'old', kind: 'reboot' },
			{ device: 'old', kind: 'container', name: 'broken' }
		]);
		await idle();
		expect(calls.some((c) => c.startsWith('old reboot'))).toBe(false);
		expect(runner.state({ device: 'old', kind: 'reboot' })).toMatchObject({
			state: 'failed',
			text: 'skipped: an earlier update there failed'
		});
	});

	it('starts and stops containers and VMs through their own abilities', async () => {
		calls.length = 0;
		runner.add([
			{ device: 'sv1', kind: 'stop', name: 'app' },
			{ device: 'sv1', kind: 'vm-shutdown', name: 'win11' },
			{ device: 'sv1', kind: 'vm-force-off', name: 'stuck' }
		]);
		await idle();
		expect(calls.filter((c) => c.endsWith('start'))).toEqual([
			'sv1 stop-container app start',
			'sv1 vm-shutdown win11 start',
			'sv1 vm-force-off stuck start'
		]);
		expect(runner.state({ device: 'sv1', kind: 'stop', name: 'app' })?.text).toBe('Stopped.');
		expect(runner.state({ device: 'sv1', kind: 'vm-force-off', name: 'stuck' })?.label).toBe(
			'sv1: force off VM stuck'
		);
	});
});
